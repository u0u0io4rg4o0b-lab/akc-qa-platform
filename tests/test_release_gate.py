import importlib.util
import io
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_gate", ROOT / "tools/validate_release.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def fixture(outcome="pass", first_only=False):
    class Required(unittest.TestCase):
        def test_a(self):
            self.assertEqual(2 + 2, 4)

        def test_b(self):
            if outcome == "skip":
                self.skipTest("fixture platform exception")
            if outcome == "xfail":
                self.fail("fixture failure must not qualify for release")
            self.assertEqual(3 + 3, 6)

    if outcome == "xfail":
        Required.test_b = unittest.expectedFailure(Required.test_b)
    expected = [Required("test_a").id(), Required("test_b").id()]
    suite = unittest.TestSuite([Required("test_a")]) if first_only else unittest.defaultTestLoader.loadTestsFromTestCase(Required)
    result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=gate.CensusResult).run(suite)
    return expected, result


class ReleaseGateTests(unittest.TestCase):
    def test_discovery_rejects_missing_wrong_target_duplicates_and_zero(self):
        expected = ["suite.Required.a", "suite.Required.b"]
        for actual in (expected[:1], ["other.a", "other.b"], [expected[0]] * 2, []):
            with self.subTest(actual=actual):
                self.assertTrue(gate.discovery_problems(actual, expected))
        self.assertTrue(gate.discovery_problems([], []))
        self.assertTrue(gate.discovery_problems(expected, [expected[0]] * 2))

    def test_real_runner_skip_is_successful_but_release_is_blocked(self):
        expected, result = fixture("skip")
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.testsRun, 2)
        self.assertTrue(gate.execution_problems(result, expected, {}))

    def test_only_named_skip_with_exact_reason_is_allowed(self):
        expected, result = fixture("skip")
        self.assertFalse(gate.execution_problems(result, expected, {expected[1]: "fixture platform exception"}))
        self.assertTrue(gate.execution_problems(result, expected, {expected[1]: "different reason"}))
        self.assertTrue(gate.execution_problems(result, expected, {expected[0]: "fixture platform exception"}))

    def test_expected_failure_and_early_stop_cannot_qualify_for_release(self):
        expected, result = fixture("xfail")
        self.assertTrue(result.wasSuccessful())
        self.assertTrue(gate.execution_problems(result, expected, {}))
        expected, result = fixture(first_only=True)
        self.assertEqual(result.testsRun, 1)
        self.assertTrue(gate.execution_problems(result, expected, {}))

    def test_complete_real_runner_results_are_accepted(self):
        expected, result = fixture()
        self.assertEqual(result.testsRun, 2)
        self.assertEqual(len(result.passed_ids), 2)
        self.assertFalse(gate.execution_problems(result, expected, {}))


if __name__ == "__main__":
    unittest.main()
