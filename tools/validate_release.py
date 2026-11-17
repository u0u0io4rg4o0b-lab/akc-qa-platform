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
PREVIOUS_RELEASE = "72ede98cee177eed083a6f2fdf16c350596a7a61"


class CensusResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started_ids = []
        self.passed_ids = []

    def startTest(self, test):
        self.started_ids.append(test.id())
        super().startTest(test)

    def addSuccess(self, test):
        self.passed_ids.append(test.id())
        super().addSuccess(test)


def test_ids(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from test_ids(item)
        else:
            yield item.id()


def discovery_problems(actual, expected):
    problems = []
    if not expected or len(expected) != len(set(expected)):
        problems.append("required test contract is empty or duplicated")
    if len(actual) != len(set(actual)):
        problems.append("duplicate discovered test IDs")
    if set(actual) != set(expected):
        problems.append({"missing": sorted(set(expected) - set(actual)),
                         "unexpected": sorted(set(actual) - set(expected))})
    return problems


def execution_problems(result, expected, allowed_skips):
    problems = discovery_problems(result.started_ids, expected)
    if result.testsRun != len(expected):
        problems.append("executed test denominator differs from contract")
    if not result.wasSuccessful() or result.expectedFailures:
        problems.append("required tests failed, errored, or unexpectedly succeeded/expected-failed")
    skipped = [test.id() for test, reason in result.skipped]
    if any(allowed_skips.get(test.id()) != reason for test, reason in result.skipped):
        problems.append("unapproved test skip or reason")
    if len(skipped) != len(set(skipped)) or len(result.passed_ids) != len(set(result.passed_ids)):
        problems.append("duplicate test outcomes")
    if set(result.passed_ids) != set(expected) - set(skipped):
        problems.append("required tests did not each finish with success or an approved skip")
    return problems


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
    contract = json.loads((ROOT / "tests/required_cases.json").read_text(encoding="utf-8"))
    expected = contract["required_test_ids"]
    allowed_skips = contract["allowed_skips"].get(os.name, {})
    assert contract["schema_version"] == 1 and set(allowed_skips) <= set(expected)
    discovered = list(test_ids(suite))
    problems = discovery_problems(discovered, expected)
    if problems:
        print("CI_GATE_JSON=" + json.dumps({"status": "BLOCKED", "stage": "discovery",
              "required": len(expected), "discovered": len(discovered), "executed": 0,
              "problems": problems}, ensure_ascii=False))
        return 1
    tests = unittest.TextTestRunner(verbosity=2, resultclass=CensusResult).run(suite)
    problems = execution_problems(tests, expected, allowed_skips)
    if problems:
        print("CI_GATE_JSON=" + json.dumps({"status": "BLOCKED", "stage": "execution",
              "required": len(expected), "executed": tests.testsRun, "passed": len(tests.passed_ids),
              "skips": [{"id": test.id(), "reason": reason} for test, reason in tests.skipped],
              "problems": problems}, ensure_ascii=False))
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
        previous = root / "preview2"
        previous.mkdir()
        (previous / "qa.py").write_bytes(invoke(["git", "show", PREVIOUS_RELEASE + ":qa.py"]).stdout)
        shutil.copytree(ROOT / "examples", previous / "examples")
        invoke([sys.executable, "-B", str(previous / "qa.py"), "demo", "--out", str(root / "previous")], env=env)
        previous_verify = json.loads(invoke([sys.executable, "-B", str(ROOT / "qa.py"), "verify",
                                            str(root / "previous/evidence")], env=env).stdout)
        assert previous_verify["integrity"] == "MATCH" and previous_verify["verdict"] == "PASS"
        demo = json.loads(invoke([sys.executable, "-B", str(ROOT / "qa.py"), "demo",
                                 "--out", str(root / "new")], env=env).stdout)
        assert demo["counts"]["PASS"] == 5
        assert b"\r\n" not in (root / "new/evidence/REPORT.md").read_bytes()
        verified = json.loads(invoke([sys.executable, "-B", str(ROOT / "qa.py"), "verify",
                                     str(root / "new/evidence")], env=env).stdout)
        assert verified["integrity"] == "MATCH" and verified["verdict"] == "PASS"
        assert verified["requirements_status"] == "ALL_DECLARED_REQUIREMENTS_HAVE_PASSING_CHECKS"
        value = {"platform": platform.platform(), "python": platform.python_version(),
                 "tests": {"total": tests.testsRun, "failures": len(tests.failures),
                           "errors": len(tests.errors), "skipped": len(tests.skipped),
                           "passed": len(tests.passed_ids),
                           "required_ids": expected,
                           "approved_skips": [{"id": test.id(), "reason": reason} for test, reason in tests.skipped],
                           "census": "MATCH"},
                 "predecessor_commit": PREDECESSOR, "predecessor_demo_exit": old.returncode,
                 "predecessor_checks_passed": 5, "legacy_verifier": legacy_verify["report_line_endings"],
                 "previous_release": PREVIOUS_RELEASE, "preview2_verify": "MATCH",
                 "new_demo_passed": 5, "new_report": "UTF8_LF", "new_verify": "MATCH",
                 "claim_ceiling": "SYNTHETIC_PLATFORM_VALIDATION_NOT_CLAUDE_OR_FRIEND_ACCEPTANCE"}
        print("CI_RESULT_JSON=" + json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
