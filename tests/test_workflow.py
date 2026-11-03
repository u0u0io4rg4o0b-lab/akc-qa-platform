import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("akc_qa", ROOT / "qa.py")
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


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
        task = self.custom("import time\nprint('{\"cents\":90}', flush=True)\ntime.sleep(10)\n")
        task["checks"][0]["timeout_seconds"] = 0.2
        result, out = self.execute(task)
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertIn(b"cents", (out / "checks/C01/stdout.bin").read_bytes())
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

    def test_symlink_and_path_traversal_refused(self):
        with self.assertRaises(qa.QAError):
            qa.member(self.project, "../outside")
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
                                cwd=self.root, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        answer = json.loads(result.stdout)
        self.assertEqual(answer["total"], 5)
        self.assertEqual(answer["verdict"], "PASS")
        self.assertTrue(Path(answer["report"]).is_file())


if __name__ == "__main__":
    unittest.main()
