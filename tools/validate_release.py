"""Synthetic release checks for hosted CI; never loads a user's project.

Needs Git only for the explicit, pinned predecessor check. The QA tool itself
and ordinary unittest suite remain independent of Git and hosted CI.
"""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = "6d872a43380feb6c5a80e0f2f3bb71ffdc61852f"


def invoke(argv, *, env=None, expected=0):
    completed = subprocess.run(argv, cwd=ROOT, capture_output=True, env=env, timeout=180)
    if completed.returncode != expected:
        raise AssertionError({"argv": argv, "expected_exit": expected,
                              "actual_exit": completed.returncode,
                              "stdout": completed.stdout.decode("utf-8", errors="backslashreplace"),
                              "stderr": completed.stderr.decode("utf-8", errors="backslashreplace")})
    return completed


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    tests = unittest.TextTestRunner(verbosity=2).run(suite)
    if not tests.wasSuccessful():
        return 1
    with tempfile.TemporaryDirectory(prefix="akc-qa-release-") as scratch:
        root = Path(scratch)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        predecessor_source = invoke(["git", "show", PREDECESSOR + ":qa.py"]).stdout
        legacy = root / "舊版 空白"
        legacy.mkdir()
        (legacy / "qa.py").write_bytes(predecessor_source)
        shutil.copytree(ROOT / "examples", legacy / "examples")
        # Native Windows reproduces the original CRLF failure; other OSes
        # must complete normally. Both are read-only verified by preview.2.
        old = invoke([sys.executable, "-B", str(legacy / "qa.py"), "demo", "--out", str(root / "old")],
                     env=env, expected=2 if os.name == "nt" else 0)
        old_result = json.loads((root / "old/evidence/result.json").read_bytes())
        assert old_result["counts"]["PASS"] == 5
        if os.name == "nt":
            assert "報告與重算結果不符" in old.stderr.decode("utf-8")
        legacy_verify = json.loads(invoke([sys.executable, "-B", str(ROOT / "qa.py"), "verify",
                                          str(root / "old/evidence")], env=env).stdout)
        assert legacy_verify["report_line_endings"] == ("LEGACY_CRLF" if os.name == "nt" else "LF")
        demo = json.loads(invoke([sys.executable, "-B", str(ROOT / "qa.py"), "demo",
                                 "--out", str(root / "new")], env=env).stdout)
        assert demo["counts"]["PASS"] == 5
        assert b"\r\n" not in (root / "new/evidence/REPORT.md").read_bytes()
        verified = json.loads(invoke([sys.executable, "-B", str(ROOT / "qa.py"), "verify",
                                     str(root / "new/evidence")], env=env).stdout)
        assert verified["integrity"] == "MATCH" and verified["verdict"] == "PASS"
        value = {"platform": platform.platform(), "python": platform.python_version(),
                 "tests": {"total": tests.testsRun, "failures": len(tests.failures),
                           "errors": len(tests.errors), "skipped": len(tests.skipped),
                           "passed": tests.testsRun - len(tests.failures) - len(tests.errors) - len(tests.skipped)},
                 "predecessor_commit": PREDECESSOR, "predecessor_demo_exit": old.returncode,
                 "predecessor_checks_passed": 5, "legacy_verifier": legacy_verify["report_line_endings"],
                 "new_demo_passed": 5, "new_report": "UTF8_LF", "new_verify": "MATCH",
                 "claim_ceiling": "HOSTED_SYNTHETIC_PLATFORM_VALIDATION_NOT_CLAUDE_OR_FRIEND_ACCEPTANCE"}
        print("CI_RESULT_JSON=" + json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
