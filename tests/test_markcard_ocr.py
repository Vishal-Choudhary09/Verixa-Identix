import unittest

from markcard_ocr import (
    is_valid_markcard,
    parse_markcard_text,
    parse_student_id_text,
    _read_student_id_image,
)


class ParseMarkcardTextTests(unittest.TestCase):
    def test_extracts_identity_gpas_and_failed_subjects(self):
        result = parse_markcard_text(
            """
            Name: Ananya Rao
            Father Name: Ramesh Rao
            USN: 3BR25CS401
            CGPA: 8.42
            SGPA: 7,91
            21CS41 Data Structures 25 36 61 P
            21CS42 Algorithms 10 05 15 F
            """
        )

        self.assertEqual(result["name"], "Ananya Rao")
        self.assertEqual(result["father_name"], "Ramesh Rao")
        self.assertEqual(result["usn"], "3BR25CS401")
        self.assertEqual(result["cgpa"], "8.42")
        self.assertEqual(result["sgpa"], "7.91")
        self.assertEqual(result["overall_status"], "FAIL")
        self.assertEqual(result["failed_subjects"], ["21CS42 Algorithms"])
        self.assertEqual(
            [subject["status"] for subject in result["subjects"]],
            ["PASS", "FAIL"],
        )

    def test_keeps_uncertain_subject_results_for_admin_review(self):
        result = parse_markcard_text(
            """
            21CS41 Data Structures
            """
        )

        self.assertEqual(result["overall_status"], "REVIEW")
        self.assertEqual(result["subjects"][0]["status"], "NOT DETECTED")
        self.assertEqual(result["failed_subjects"], [])

    def test_reads_letter_grades_as_subject_results(self):
        result = parse_markcard_text(
            """
            21CS41 Data Structures A+ 9
            21CS42 Algorithms F 0.00
            """
        )

        self.assertEqual(
            [subject["status"] for subject in result["subjects"]],
            ["PASS", "FAIL"],
        )
        self.assertEqual(result["failed_subjects"], ["21CS42 Algorithms"])

    def test_reads_separate_grade_card_rows_and_labels(self):
        result = parse_markcard_text(
            """
            Provisional Grade Card
            USN:
            3BR25CS401
            NAME:
            Sample Student
            Father:
            Example Parent
            Course Code
            Course Name
            Grade
            22CS42
            Data Structures
            44.00
            38.00
            4.00
            4.00
            A+
            9.00
            22MATDIP41
            Additional Mathematics
            55.00
            0.00
            0.00
            0.00
            F
            0.00
            SGPA
            7.50
            CGPA
            8.23
            """
        )

        self.assertEqual(result["name"], "Sample Student")
        self.assertEqual(result["father_name"], "Example Parent")
        self.assertEqual(result["usn"], "3BR25CS401")
        self.assertEqual(result["sgpa"], "7.50")
        self.assertEqual(result["cgpa"], "8.23")
        self.assertEqual(
            [subject["status"] for subject in result["subjects"]],
            ["PASS", "FAIL"],
        )
        self.assertEqual(
            result["failed_subjects"],
            ["22MATDIP41 Additional Mathematics"],
        )
        self.assertTrue(is_valid_markcard(result))

    def test_rejects_documents_without_a_grade_card_signature(self):
        result = parse_markcard_text(
            """
            Name: Sample Student
            USN: 3BR25CS401
            CGPA: 8.23
            SGPA: 7.50
            22CS42 Data Structures A+
            """
        )

        self.assertFalse(is_valid_markcard(result))

    def test_does_not_read_father_name_as_student_name(self):
        result = parse_markcard_text(
            """
            Father Name: Ramesh Rao
            """
        )

        self.assertIsNone(result["name"])
        self.assertEqual(result["father_name"], "Ramesh Rao")


class ParseStudentIdTextTests(unittest.TestCase):
    def test_extracts_unlabeled_name_and_two_digit_prefix_usn(self):
        result = parse_student_id_text(
            """
            BITM
            ESTD : 1997
            H SAMPLESTUDENT
            USN : 22BI25CS412-T
            B.E - CS & Engg
            """
        )

        self.assertEqual(result["name"], "H SAMPLESTUDENT")
        self.assertEqual(result["usn"], "22BI25CS412-T")
        self.assertEqual(result["ocr_status"], "READ")
        self.assertIsNone(result["error"])

    def test_corrects_ocr_confusions_in_usn_digits(self):
        result = parse_student_id_text(
            """
            BITM
            H MALLIKARJUNA
            USN : 3BR25CS4I1
            B.E - C S & Engg
            """
        )

        self.assertEqual(result["name"], "H MALLIKARJUNA")
        self.assertEqual(result["usn"], "3BR25CS411")
        self.assertIsNone(result["error"])

    def test_retries_student_id_ocr_on_enhanced_identity_crop(self):
        class FakePytesseract:
            def __init__(self):
                self.configs = []

            def image_to_string(self, image, config=None):
                self.configs.append(config)
                if config is None:
                    return "Name: Incorrect OCR\nUSN:"
                return "H SAMPLE STUDENT\nUSN: 3BR25CS411"

        from PIL import Image

        pytesseract = FakePytesseract()
        with Image.new("RGB", (200, 300)) as image:
            result = _read_student_id_image(image, pytesseract)

        self.assertEqual(result["name"], "H SAMPLE STUDENT")
        self.assertEqual(result["usn"], "3BR25CS411")
        self.assertIsNone(result["error"])
        self.assertEqual(pytesseract.configs, [None, "--psm 6"])


if __name__ == "__main__":
    unittest.main()
