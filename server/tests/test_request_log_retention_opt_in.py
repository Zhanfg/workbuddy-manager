"""Audit optional request-log retention without touching production state."""
from __future__ import annotations

import unittest
from server import db


class RequestLogRetentionPolicyTest(unittest.TestCase):
    def test_default_does_not_change(self):
        self.assertEqual(db._request_log_retention_days(None), 90)

    def test_explicit_opt_in_and_boundaries(self):
        for input_text, expected in (("7", 7), ("14", 14), ("90", 90),
                                     ("180", 180), ("365", 365)):
            with self.subTest(input_text=input_text):
                self.assertEqual(db._request_log_retention_days(input_text), expected)

    def test_bad_values_cannot_cause_accidental_prune(self):
        for input_text in ("", "0", "1", "-1", "366", "NaN", "1.2", "1000000000"):
            with self.subTest(input_text=input_text):
                self.assertEqual(db._request_log_retention_days(input_text), 90)


if __name__ == "__main__":
    unittest.main()
