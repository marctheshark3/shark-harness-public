import unittest

from measure_controls import summarize


class ControlsMeasurementTests(unittest.TestCase):
    def test_threshold_requires_enough_samples_and_counts_dropped_taps(self):
        samples = [{"kind": "Response", "releaseToDisplayMs": 100 + i} for i in range(20)]
        self.assertEqual(summarize(samples, {}, {}, [])["status"], "pass")
        self.assertEqual(summarize(samples[:2], {}, {}, [])["status"], "insufficient_samples")
        self.assertEqual(summarize(samples, {}, {"tapDrops": 1}, [])["status"], "fail")
        samples[-1]["releaseToDisplayMs"] = 600
        self.assertEqual(summarize(samples, {}, {}, [])["status"], "fail")

    def test_ignored_controls_fail_but_center_taps_are_not_navigation(self):
        sample = dict(kind="Input", gesture=1, x=233, y=240, action=None)
        self.assertEqual(summarize([sample], {}, {}, [])["missedInputs"], 0)
        sample["x"] = 413
        self.assertEqual(summarize([sample], {}, {}, [])["missedInputs"], 1)
        sample["action"] = "next"
        self.assertEqual(summarize([sample], {}, {}, [])["missedInputs"], 0)
