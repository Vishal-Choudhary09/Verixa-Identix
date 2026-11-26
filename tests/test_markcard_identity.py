import unittest

from app import _markcard_identity_matches


class MarkcardIdentityTests(unittest.TestCase):
    def test_parent_name_mismatch_rejects_matching_student_identity(self):
        comparisons = {
            "name": {
                "student_record": "MATCH",
                "application": "MATCH",
            },
            "usn": {
                "student_record": "MATCH",
                "application": "MATCH",
            },
            "father_name": {
                "student_record": "NOT_STORED",
                "application": "MISMATCH",
            },
        }

        self.assertFalse(_markcard_identity_matches(comparisons))

    def test_undetected_parent_name_does_not_reject_matching_student_identity(self):
        comparisons = {
            "name": {
                "student_record": "MATCH",
                "application": "MATCH",
            },
            "usn": {
                "student_record": "MATCH",
                "application": "MATCH",
            },
            "father_name": {
                "student_record": "NOT_STORED",
                "application": "NOT_DETECTED",
            },
        }

        self.assertTrue(_markcard_identity_matches(comparisons))

    def test_name_or_usn_mismatch_still_rejects_card(self):
        comparisons = {
            "name": {
                "student_record": "MATCH",
                "application": "MATCH",
            },
            "usn": {
                "student_record": "MATCH",
                "application": "MISMATCH",
            },
            "father_name": {
                "student_record": "NOT_STORED",
                "application": "MATCH",
            },
        }

        self.assertFalse(_markcard_identity_matches(comparisons))


if __name__ == "__main__":
    unittest.main()
