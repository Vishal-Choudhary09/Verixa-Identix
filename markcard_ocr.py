import re


_USN_PATTERN = re.compile(
    r"\b[1-9]\s*[A-Z]{2,3}\s*\d{2}\s*[A-Z]{2,3}\s*\d{2,4}\b",
    re.IGNORECASE,
)
_STUDENT_ID_USN_PATTERN = re.compile(
    r"(?<![A-Z0-9])(?P<prefix>\d{1,2})\s*"
    r"(?P<college>[A-Z]{2,3})\s*"
    r"(?P<year>[0-9OQD]{2})\s*"
    r"(?P<branch>[A-Z]{2,3})\s*"
    r"(?P<number>[0-9OQDIL|]{2,4})"
    r"(?P<suffix>\s*[-–]\s*[A-Z])?(?![A-Z0-9])",
    re.IGNORECASE,
)
_SUBJECT_CODE_PATTERN = re.compile(
    r"^\s*(?:\d{1,2}\s+)?(?P<code>\d{2}[A-Z]{2,8}\d{2,4}[A-Z]?\d{0,2})"
    r"\s+(?P<details>.+?)\s*$",
    re.IGNORECASE,
)
_SUBJECT_CODE_LINE_PATTERN = re.compile(
    r"^\d{2}[A-Z]{2,8}\d{2,4}[A-Z]?\d{0,2}$",
    re.IGNORECASE,
)
_SUBJECT_STATUS_PATTERN = re.compile(
    r"\b(?P<status>PASS(?:ED)?|FAIL(?:ED)?|NOT\s+ELIGIBLE|ABSENT|AB|NE|PP|"
    r"O|A\+?|B\+?|C\+?|D\+?|E\+?|P|F)(?:\s+\d+(?:\.\d+)?)*\s*$",
    re.IGNORECASE,
)


def _label_value(lines, labels):
    label_pattern = re.compile(
        r"^\s*(?P<label>" + labels + r")\s*(?:[:\-]\s*)?(?P<value>.*)$",
        re.IGNORECASE,
    )

    for index, line in enumerate(lines):
        match = label_pattern.match(line)
        if not match:
            continue

        value = match.group("value").strip()
        if value:
            return value

        if index + 1 < len(lines):
            return lines[index + 1].strip()

    return None


def _subject_name(details):
    status_match = _SUBJECT_STATUS_PATTERN.search(details)
    if status_match:
        details = details[:status_match.start()].strip()

    marks_columns = re.search(
        r"\s+\d{1,3}(?:\.\d+)?(?:\s+\d{1,3}(?:\.\d+)?){1,}(?:\s|$)",
        details,
    )
    if marks_columns:
        details = details[:marks_columns.start()]
    return details.strip(" \t:|-") or None


def _subject_status(details):
    status_match = _SUBJECT_STATUS_PATTERN.search(details)
    if not status_match:
        return "NOT DETECTED"

    status_token = re.sub(
        r"\s+",
        " ",
        status_match.group("status").upper(),
    )
    return (
        "FAIL"
        if status_token in {
            "FAIL",
            "FAILED",
            "F",
            "AB",
            "ABSENT",
            "NE",
            "NOT ELIGIBLE",
        }
        else "PASS"
    )


def parse_markcard_text(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    usn_match = _USN_PATTERN.search(text.upper())

    cgpa_match = re.search(
        r"\bCGPA\b\s*[:=\-]?\s*(\d{1,2}(?:[.,]\d{1,2})?)",
        text,
        re.IGNORECASE,
    )
    sgpa_match = re.search(
        r"\bSGPA\b\s*[:=\-]?\s*(\d{1,2}(?:[.,]\d{1,2})?)",
        text,
        re.IGNORECASE,
    )

    name = _label_value(
        lines,
        r"STUDENT\s+NAME|NAME\s+OF\s+STUDENT|NAME",
    )
    father_name = _label_value(
        lines,
        r"FATHER(?:['’]S)?\s*/\s*MOTHER(?:['’]S)?\s+NAME|"
        r"FATHER(?:['’]S)?\s+NAME|"
        r"MOTHER(?:['’]S)?\s+NAME|NAME\s+OF\s+(?:FATHER|PARENT)|"
        r"PARENT\s+NAME|FATHER(?:['’]S)?",
    )

    subjects = []
    seen_subjects = set()

    def add_subject(code, subject_name, status):
        subject = {
            "code": code.upper(),
            "name": subject_name or code.upper(),
            "status": status,
        }
        subject_key = (
            subject["code"],
            subject["name"].casefold(),
            subject["status"],
        )
        if subject_key not in seen_subjects:
            subjects.append(subject)
            seen_subjects.add(subject_key)

    for line in lines:
        match = _SUBJECT_CODE_PATTERN.match(line)
        if not match:
            continue

        details = match.group("details")
        add_subject(
            match.group("code"),
            _subject_name(details),
            _subject_status(details),
        )

    subject_code_indexes = [
        index
        for index, line in enumerate(lines)
        if _SUBJECT_CODE_LINE_PATTERN.fullmatch(line)
    ]
    for code_position, line_index in enumerate(subject_code_indexes):
        next_line_index = (
            subject_code_indexes[code_position + 1]
            if code_position + 1 < len(subject_code_indexes)
            else len(lines)
        )
        row_lines = lines[line_index + 1:next_line_index]
        if not row_lines:
            continue

        grade = next(
            (
                row_line
                for row_line in row_lines[1:]
                if _SUBJECT_STATUS_PATTERN.fullmatch(row_line)
            ),
            "",
        )
        add_subject(
            lines[line_index],
            row_lines[0],
            _subject_status(grade),
        )

    failed_subjects = [
        subject["code"] + " " + subject["name"]
        for subject in subjects
        if subject["status"] == "FAIL"
    ]

    if failed_subjects:
        overall_status = "FAIL"
    elif subjects and all(
        subject["status"] == "PASS"
        for subject in subjects
    ):
        overall_status = "PASS"
    else:
        overall_status = "REVIEW"

    return {
        "name": name,
        "usn": re.sub(r"\s+", "", usn_match.group(0)) if usn_match else None,
        "father_name": father_name,
        "cgpa": cgpa_match.group(1).replace(",", ".") if cgpa_match else None,
        "sgpa": sgpa_match.group(1).replace(",", ".") if sgpa_match else None,
        "subjects": subjects,
        "failed_subjects": failed_subjects,
        "overall_status": overall_status,
        "is_markcard": bool(
            re.search(
                r"\b(?:PROVISIONAL\s+)?GRADE\s+CARD\b|\bMARKS?\s+CARD\b",
                text,
                re.IGNORECASE,
            )
        ),
        "ocr_status": "READ" if text.strip() else "NO_TEXT",
        "error": None if text.strip() else "No readable text was detected.",
    }


def parse_student_id_text(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    name = _label_value(
        lines,
        r"STUDENT\s+NAME|NAME\s+OF\s+STUDENT|NAME",
    )
    usn_label = re.search(r"\bUSN\b", text, re.IGNORECASE)
    usn_search_text = text[usn_label.end():] if usn_label else text
    usn_match = _STUDENT_ID_USN_PATTERN.search(usn_search_text)
    if usn_match:
        digit_translation = str.maketrans({
            "O": "0",
            "Q": "0",
            "D": "0",
            "I": "1",
            "L": "1",
            "|": "1",
        })
        usn = (
            usn_match.group("prefix")
            + usn_match.group("college").upper()
            + usn_match.group("year").upper().translate(digit_translation)
            + usn_match.group("branch").upper()
            + usn_match.group("number").upper().translate(digit_translation)
        )
        suffix = usn_match.group("suffix")
        if suffix:
            usn += re.sub(r"\s+", "", suffix).replace("–", "-").upper()
    else:
        usn = None

    if not name and usn_label:
        usn_line_index = next(
            (
                index
                for index, line in enumerate(lines)
                if re.search(r"\bUSN\b", line, re.IGNORECASE)
            ),
            None,
        )
        if usn_line_index:
            candidate = lines[usn_line_index - 1]
            if (
                len(candidate.split()) >= 2
                and re.fullmatch(r"[A-Z][A-Z .'-]*", candidate, re.IGNORECASE)
            ):
                name = candidate.strip(" .-")

    return {
        "name": name,
        "usn": usn,
        "ocr_status": "READ" if text.strip() else "NO_TEXT",
        "error": None if name and usn else (
            "Could not read both the student name and USN."
            if text.strip()
            else "No readable text was detected."
        ),
    }


def is_valid_markcard(result):
    return bool(
        result.get("is_markcard")
        and result.get("name")
        and result.get("usn")
        and result.get("cgpa")
        and result.get("sgpa")
        and result.get("subjects")
    )


def _configure_tesseract(pytesseract):
    import os
    import shutil

    if shutil.which(pytesseract.pytesseract.tesseract_cmd):
        return

    if os.name != "nt":
        return

    for program_files in (
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
    ):
        if not program_files:
            continue

        executable = os.path.join(
            program_files,
            "Tesseract-OCR",
            "tesseract.exe",
        )
        if os.path.isfile(executable):
            pytesseract.pytesseract.tesseract_cmd = executable
            return


def _ocr_pdf_pages(pdf_data):
    import pymupdf
    import pytesseract
    from PIL import Image

    _configure_tesseract(pytesseract)

    page_text = []
    with pymupdf.open(stream=pdf_data, filetype="pdf") as document:
        for page in document:
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(2, 2),
                colorspace=pymupdf.csRGB,
                alpha=False,
            )
            image = Image.frombytes(
                "RGB",
                (pixmap.width, pixmap.height),
                pixmap.samples,
            )
            page_text.append(pytesseract.image_to_string(image))
    return "\n".join(page_text)


def _read_student_id_image(image, pytesseract):
    from PIL import Image, ImageOps

    text = pytesseract.image_to_string(image)
    result = parse_student_id_text(text)
    if result["name"] and result["usn"]:
        return result

    width, height = image.size
    identity_crop = image.crop((
        int(width * 0.02),
        int(height * 0.17),
        int(width * 0.80),
        int(height * 0.48),
    ))
    with identity_crop:
        with identity_crop.convert("L") as grayscale:
            with ImageOps.autocontrast(grayscale) as enhanced:
                scaled = enhanced.resize(
                    (enhanced.width * 2, enhanced.height * 2),
                    Image.Resampling.LANCZOS,
                )
                with scaled:
                    for config in ("--psm 6", "--psm 11"):
                        candidate = parse_student_id_text(
                            pytesseract.image_to_string(
                                scaled,
                                config=config,
                            )
                        )
                        if candidate["name"] and candidate["usn"]:
                            return candidate
                        result["name"] = result["name"] or candidate["name"]
                        result["usn"] = result["usn"] or candidate["usn"]
                        if result["name"] and result["usn"]:
                            result["ocr_status"] = "READ"
                            result["error"] = None
                            return result

    return result


def read_markcard(file_storage):
    try:
        extension = file_storage.filename.rsplit(".", 1)[-1].lower()
        file_storage.stream.seek(0)

        if extension == "pdf":
            import pymupdf

            pdf_data = file_storage.stream.read()
            with pymupdf.open(stream=pdf_data, filetype="pdf") as document:
                text = "\n".join(page.get_text("text") for page in document)

            result = parse_markcard_text(text)
            if (
                not result["usn"]
                or not result["subjects"]
                or not result["sgpa"]
                or not result["cgpa"]
            ):
                try:
                    ocr_text = _ocr_pdf_pages(pdf_data)
                except Exception as error:
                    if not text.strip():
                        raise
                    result["ocr_status"] = "PARTIAL"
                    result["error"] = (
                        "Some details were not available as PDF text, and scanned "
                        "content could not be OCR'd. Review the mark card manually."
                    )
                    result["error_type"] = type(error).__name__
                    return result

                result = parse_markcard_text(text + "\n" + ocr_text)
        else:
            import pytesseract
            from PIL import Image

            _configure_tesseract(pytesseract)

            with Image.open(file_storage.stream) as image:
                text = pytesseract.image_to_string(image)

            result = parse_markcard_text(text)
        if result["error"]:
            result["error"] = (
                "No readable text was detected. Review this document manually."
            )
        return result
    except ImportError as error:
        return {
            **parse_markcard_text(""),
            "ocr_status": "UNAVAILABLE",
            "error": "PDF text extraction or OCR dependencies are unavailable.",
            "error_type": type(error).__name__,
        }
    except Exception as error:
        message = (
            "Tesseract OCR is not installed or configured."
            if type(error).__name__ == "TesseractNotFoundError"
            else "OCR could not read this document. Review it manually."
        )
        return {
            **parse_markcard_text(""),
            "ocr_status": "FAILED",
            "error": message,
            "error_type": type(error).__name__,
        }
    finally:
        file_storage.stream.seek(0)


def read_student_id(file_storage):
    try:
        extension = file_storage.filename.rsplit(".", 1)[-1].lower()
        file_storage.stream.seek(0)

        if extension == "pdf":
            import pymupdf

            pdf_data = file_storage.stream.read()
            with pymupdf.open(stream=pdf_data, filetype="pdf") as document:
                text = "\n".join(page.get_text("text") for page in document)

            result = parse_student_id_text(text)
            if not result["name"] or not result["usn"]:
                ocr_text = _ocr_pdf_pages(pdf_data)
                result = parse_student_id_text(text + "\n" + ocr_text)
        else:
            import pytesseract
            from PIL import Image

            _configure_tesseract(pytesseract)
            with Image.open(file_storage.stream) as image:
                result = _read_student_id_image(image, pytesseract)

        identity = {
            "name": result["name"],
            "usn": result["usn"],
            "ocr_status": result["ocr_status"],
            "error": None,
        }
        if not identity["name"] or not identity["usn"]:
            identity["ocr_status"] = (
                "PARTIAL" if result["ocr_status"] == "READ" else "NO_TEXT"
            )
            identity["error"] = "Could not read both the student name and USN."
        return identity
    except ImportError as error:
        return {
            "name": None,
            "usn": None,
            "ocr_status": "UNAVAILABLE",
            "error": "PDF text extraction or OCR dependencies are unavailable.",
            "error_type": type(error).__name__,
        }
    except Exception as error:
        message = (
            "Tesseract OCR is not installed or configured."
            if type(error).__name__ == "TesseractNotFoundError"
            else "OCR could not read this ID card."
        )
        return {
            "name": None,
            "usn": None,
            "ocr_status": "FAILED",
            "error": message,
            "error_type": type(error).__name__,
        }
    finally:
        file_storage.stream.seek(0)
