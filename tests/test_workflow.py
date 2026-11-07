import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("akc_qa", ROOT / "qa.py")
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)

# Test-host budget only. Never rewrites a user's plan or production timeouts.
TIMEOUT_SCALE = float(os.environ.get("AKC_QA_TEST_TIMEOUT_SCALE", "1"))
if not 1 <= TIMEOUT_SCALE <= 10:
    raise ValueError("AKC_QA_TEST_TIMEOUT_SCALE must be between 1 and 10")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="akc-qa-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "朋友的 個人專案"
        shutil.copytree(ROOT / "examples" / "discount", self.project)
        self.task = qa.load(self.project / "qa-task.json")

    def plan(self, task=None):
        if task is not None:
            (self.project / "qa-task.json").write_text(json.dumps(task), encoding="utf-8")
        value = qa.make_plan(self.project / "qa-task.json", self.project)
        path = self.root / ("plan-" + str(len(list(self.root.glob("plan-*")))) + ".json")
        qa.put(path, value)
        return path, value

    def execute(self, task=None, name="evidence"):
        path, value = self.plan(task)
        out = self.root / name
        result = qa.run_plan(path, out, value["plan_id"])
        return result, out

    def custom(self, code):
        (self.project / "check.py").write_text(code, encoding="utf-8")
        task = copy.deepcopy(self.task)
        task["files"].append("check.py")
        task["checks"] = [copy.deepcopy(task["checks"][0])]
        task["checks"][0]["argv"] = ["{python}", "-I", "-B", "{project}/check.py"]
        task["checks"][0]["timeout_seconds"] = min(120, 30 * TIMEOUT_SCALE)
        return task

    def reseal(self, out):
        (out / "manifest.json").unlink()
        qa.seal(out)

    def test_real_subject_and_relocated_evidence(self):
        result, out = self.execute()
        self.assertEqual(result["counts"]["PASS"], 5)
        self.assertEqual(qa.verify(out)["verdict"], "PASS")
        moved = self.root / "搬移後 證據"
        shutil.copytree(out, moved)
        shutil.rmtree(self.project)
        self.assertEqual(qa.verify(moved)["total"], 5)

    def test_known_product_fault_is_fail_and_later_cases_not_run(self):
        product = self.project / "discount.py"
        product.write_text(product.read_text().replace("(100 - percent)", "(100 - percent // 2)"))
        result, out = self.execute()
        self.assertEqual(result["verdict"], "FAIL")
        self.assertEqual(result["counts"]["FAIL"], 1)
        self.assertEqual(result["counts"]["NOT_RUN"], 4)
        self.assertEqual(qa.verify(out)["verdict"], "FAIL")

    def test_wrong_approval_does_not_start_or_create_output(self):
        path, value = self.plan()
        with self.assertRaises(qa.QAError):
            qa.run_plan(path, self.root / "denied", "wrong")
        self.assertFalse((self.root / "denied").exists())

    def test_source_drift_refused_before_launch(self):
        path, value = self.plan()
        with (self.project / "discount.py").open("a") as handle:
            handle.write("\n# changed\n")
        with self.assertRaises(qa.QAError):
            qa.run_plan(path, self.root / "denied", value["plan_id"])
        self.assertFalse((self.root / "denied").exists())

    def test_source_mutated_by_command_is_inconclusive(self):
        task = self.custom("from pathlib import Path\np=Path(__file__)\np.write_text('changed')\nprint('{\"cents\":90}')\n")
        result, out = self.execute(task)
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertEqual(qa.verify(out)["verdict"], "INCONCLUSIVE")

    def test_existing_attempt_cannot_be_replayed(self):
        path, value = self.plan()
        out = self.root / "evidence"
        qa.run_plan(path, out, value["plan_id"])
        before = (out / "manifest.json").read_bytes()
        with self.assertRaises(qa.QAError):
            qa.run_plan(path, out, value["plan_id"])
        self.assertEqual(before, (out / "manifest.json").read_bytes())

    def test_timeout_keeps_partial_evidence(self):
        task = self.custom("import time\nprint('{\"cents\":90}', flush=True)\ntime.sleep(600)\n")
        task["checks"][0]["timeout_seconds"] = 2 * TIMEOUT_SCALE
        result, out = self.execute(task)
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        # A timeout is valid even when a busy machine has not started Python yet.
        # When output arrived, it must be retained exactly; don't require a 0.2s startup.
        captured = (out / "checks/C01/stdout.bin").read_bytes()
        self.assertIn(captured, (b"", b'{"cents":90}\n', b'{"cents":90}\r\n'))
        self.assertEqual(qa.load(out / "checks/C01/native.json")["state"], "TIMED_OUT")
        self.assertEqual(qa.verify(out)["verdict"], "INCONCLUSIVE")

    def test_output_limit_is_not_pass(self):
        task = self.custom("print('x' * (2 * 1024 * 1024))\n")
        result, out = self.execute(task)
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertLessEqual((out / "checks/C01/stdout.bin").stat().st_size, qa.MAX_OUTPUT)
        self.assertFalse(qa.load(out / "checks/C01/native.json")["stderr_truncated"])
        self.assertEqual(qa.verify(out)["verdict"], "INCONCLUSIVE")

    def test_empty_suite_exit_zero_is_not_qa_pass(self):
        task = self.custom("pass\n")
        task["checks"][0]["mode"] = "exit_code"
        result, out = self.execute(task)
        self.assertEqual(result["verdict"], "COMMANDS_COMPLETED_QA_UNCONFIRMED")
        self.assertEqual(result["counts"]["PASS"], 0)
        self.assertEqual(qa.verify(out)["counts"]["EXIT_MATCH"], 1)

    def test_empty_json_output_is_inconclusive(self):
        result, _ = self.execute(self.custom("pass\n"))
        self.assertEqual(result["verdict"], "INCONCLUSIVE")

    def test_bool_not_integer(self):
        task = self.custom("print('{\"cents\":true}')\n")
        task["checks"][0]["expected_json"] = {"cents": 1}
        result, _ = self.execute(task)
        self.assertEqual(result["verdict"], "FAIL")

    def test_duplicate_key_and_trailing_output_rejected(self):
        for content in ('{"cents":1,"cents":90}', '{"cents":90}\nPASS'):
            with self.subTest(content=content):
                task = self.custom("print(" + repr(content) + ")\n")
                name = "evidence-" + str(len(list(self.root.glob("evidence-*"))))
                result, _ = self.execute(task, name=name)
                self.assertEqual(result["verdict"], "INCONCLUSIVE")

    def test_nonempty_stderr_fails_even_if_stdout_matches(self):
        task = self.custom("import sys\nprint('{\"cents\":90}')\nprint('unexpected', file=sys.stderr)\n")
        result, _ = self.execute(task)
        self.assertEqual(result["verdict"], "FAIL")

    def test_output_tamper_is_rejected(self):
        _, out = self.execute()
        (out / "checks/C01/stdout.bin").write_bytes(b'{"cents":0}')
        with self.assertRaises(qa.QAError):
            qa.verify(out)

    def test_rehashed_false_green_is_rejected_by_semantic_recheck(self):
        _, out = self.execute()
        (out / "checks/C01/stdout.bin").write_bytes(b'{"cents":0}')
        self.reseal(out)
        with self.assertRaises(qa.QAError):
            qa.verify(out)

    def test_missing_case_cannot_reduce_denominator(self):
        _, out = self.execute()
        value = qa.load(out / "result.json")
        value["checks"].pop()
        value["total"] -= 1
        (out / "result.json").write_bytes(qa.canonical(value))
        self.reseal(out)
        with self.assertRaises(qa.QAError):
            qa.verify(out)

    def test_unlisted_evidence_refused(self):
        _, out = self.execute()
        (out / "unlisted.txt").write_text("extra")
        with self.assertRaises(qa.QAError):
            qa.verify(out)

    def test_zero_cases_and_duplicate_case_ids_refused(self):
        for cases in ([], [self.task["checks"][0]] * 2):
            bad = copy.deepcopy(self.task)
            bad["checks"] = cases
            with self.assertRaises(qa.QAError):
                qa.validate(bad)

    def test_path_traversal_refused(self):
        with self.assertRaises(qa.QAError):
            qa.member(self.project, "../outside")

    def test_symlink_refused(self):
        try:
            (self.project / "link.py").symlink_to(self.project / "discount.py")
        except OSError:
            self.skipTest("symlink creation unavailable on this host")
        with self.assertRaises(qa.QAError):
            qa.regular(qa.member(self.project, "link.py"))

    def test_clean_copy_cli_works_in_unicode_space_path(self):
        destination = self.root / "乾淨下載 朋友"
        shutil.copytree(ROOT, destination, ignore=shutil.ignore_patterns(".git", "runs", "__pycache__", "work"))
        result = subprocess.run([sys.executable, "-B", str(destination / "qa.py"), "demo"],
                                cwd=self.root, capture_output=True, timeout=180 * TIMEOUT_SCALE)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        answer = json.loads(result.stdout)
        self.assertEqual(answer["total"], 5)
        self.assertEqual(answer["verdict"], "PASS")
        self.assertTrue(Path(answer["report"]).is_file())

    def test_report_bytes_do_not_use_windows_text_translation(self):
        original_open = Path.open

        def translated_open(path, *args, **kwargs):
            mode = args[0] if args else kwargs.get("mode", "r")
            if path.name == "REPORT.md" and mode == "x":
                kwargs["newline"] = "\r\n"
            return original_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", translated_open):
            _, out = self.execute()
        self.assertNotIn(b"\r\n", (out / "REPORT.md").read_bytes())
        self.assertEqual(qa.verify(out)["verification_status"], "VERIFIED")

    def test_cli_utf8_in_cp1252_and_cp936_environment(self):
        for codec in ("cp1252", "cp936"):
            with self.subTest(codec=codec):
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = codec
                for command, expected_rc, stream in (("doctor", 0, "stdout"), ("verify", 2, "stderr")):
                    argv = [sys.executable, "-B", str(ROOT / "qa.py"), command]
                    if command == "verify":
                        argv.append(str(self.root / "missing"))
                    result = subprocess.run(argv, capture_output=True, env=env, timeout=60 * TIMEOUT_SCALE)
                    self.assertEqual(result.returncode, expected_rc)
                    answer = json.loads(getattr(result, stream).decode("utf-8"))
                    self.assertIn("next" if expected_rc == 0 else "message", answer)

    def test_fail_reasons_distinguish_output_and_warning(self):
        cases = [("print('{\"cents\":91}')\n", "JSON_VALUE_MISMATCH"),
                 ("import sys\nprint('{\"cents\":90}')\nprint('warning',file=sys.stderr)\n", "STDERR_MISMATCH")]
        for index, (code, reason) in enumerate(cases):
            result, out = self.execute(self.custom(code), name="reason-" + str(index))
            self.assertEqual(result["checks"][0]["reason_codes"], [reason])
            self.assertIn(reason, (out / "REPORT.md").read_text(encoding="utf-8"))
            self.assertEqual(qa.verify(out)["verdict"], "FAIL")

    def test_expected_stderr_is_exact_not_blanket_ignore(self):
        for index, suffix in enumerate(("", "unexpected error\n")):
            code = "import sys\nprint('{\"cents\":90}')\nsys.stderr.write(" + repr("known warning\n" + suffix) + ")\n"
            task = self.custom(code)
            task["checks"][0]["expected_stderr"] = "known warning\r\n" if os.name == "nt" else "known warning\n"
            result, out = self.execute(task, name="stderr-" + str(index))
            self.assertEqual(result["verdict"], "FAIL" if suffix else "PASS")
            self.assertEqual(qa.verify(out)["verdict"], result["verdict"])

    def test_wrong_exit_is_reported_with_tail_but_no_inferred_case_count(self):
        task = self.custom("import sys\nprint('34 tests: 33 passed, 1 failed')\nsys.exit(1)\n")
        task["checks"][0]["mode"] = "exit_code"
        result, out = self.execute(task)
        row = result["checks"][0]
        self.assertEqual(row["actual_exit"], 1)
        self.assertEqual(row["reason_codes"], ["EXIT_CODE_MISMATCH"])
        self.assertIn("34 tests", row["stdout_tail"])
        self.assertEqual(result["total"], 1)
        self.assertIn("0 / 1", (out / "REPORT.md").read_text(encoding="utf-8"))

    def test_not_run_requirement_is_not_presented_as_verified(self):
        task = copy.deepcopy(self.task)
        task["checks"][0]["expected_json"] = {"cents": 91}
        result, out = self.execute(task)
        self.assertEqual(result["uncovered_requirement_ids"], [])
        self.assertEqual(result["unexecuted_requirement_ids"], ["R02"])
        self.assertEqual(result["unverified_requirement_ids"], ["R01", "R02"])
        self.assertIn("因所列檢查未執行而未驗證的需求：R02", (out / "REPORT.md").read_text(encoding="utf-8"))
        self.assertEqual(qa.verify(out)["verdict"], "FAIL")

    def test_verify_fail_exit_one_still_has_integrity_match(self):
        task = self.custom("print('{\"cents\":91}')\n")
        _, out = self.execute(task)
        result = subprocess.run([sys.executable, "-B", str(ROOT / "qa.py"), "verify", str(out)],
                                capture_output=True, timeout=60 * TIMEOUT_SCALE)
        self.assertEqual(result.returncode, 1)
        answer = json.loads(result.stdout)
        self.assertEqual(answer["verification_status"], "VERIFIED")
        self.assertEqual(answer["integrity"], "MATCH")
        self.assertEqual(answer["verdict"], "FAIL")

    def test_reason_and_preview_tamper_rejected_even_after_rehash(self):
        _, out = self.execute()
        result = qa.load(out / "result.json")
        result["checks"][0]["stdout_tail"] = "made up"
        (out / "result.json").write_bytes(qa.canonical(result))
        self.reseal(out)
        with self.assertRaises(qa.QAError):
            qa.verify(out)

    def test_untrusted_tail_cannot_inject_html_or_table_cells(self):
        task = self.custom("print('<script>alert(1)</script>|`\\x1b[31m')\n")
        task["checks"][0]["mode"] = "exit_code"
        _, out = self.execute(task)
        text = (out / "REPORT.md").read_text(encoding="utf-8")
        self.assertNotIn("<script>", text)
        self.assertNotIn("\x1b", text)
        self.assertIn("&#124;", text)
        qa.verify(out)

    @unittest.skipUnless(os.name == "nt", "native Windows flag check")
    def test_native_windows_no_console_window_flag_applied(self):
        result, out = self.execute(self.custom("print('{\"cents\":90}')\n"))
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(qa.load(out / "checks/C01/native.json")["creationflags"], subprocess.CREATE_NO_WINDOW)


if __name__ == "__main__":
    unittest.main()
