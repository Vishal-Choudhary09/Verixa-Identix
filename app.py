import os
import sqlite3
import uuid
import json
import time
import threading

from datetime import datetime, timedelta

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    send_from_directory,
    Response,
    stream_with_context
)

from werkzeug.security import check_password_hash
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from markcard_ocr import is_valid_markcard, read_markcard, read_student_id


# =========================================================
# PATHS
# =========================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

DB_PATH = os.path.join(
    BASE_DIR,
    "secureverify.db"
)

UPLOAD_FOLDER = os.path.join(
    BASE_DIR,
    "uploads"
)


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

app.secret_key = (
    "secureverify-college-project-secret-key"
)

# Keep the session alive until logout.
app.permanent_session_lifetime = timedelta(
    days=30
)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

app.config["MAX_CONTENT_LENGTH"] = (
    20 * 1024 * 1024
)

os.makedirs(
    UPLOAD_FOLDER,
    exist_ok=True
)


# =========================================================
# DATABASE
# =========================================================

_OCR_SCHEMA_LOCK = threading.Lock()
_OCR_SCHEMA_READY = False


def _ensure_document_ocr_column(conn):
    global _OCR_SCHEMA_READY

    if _OCR_SCHEMA_READY:
        return

    with _OCR_SCHEMA_LOCK:
        if _OCR_SCHEMA_READY:
            return

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(documents)")
        }
        if not columns:
            return

        if "ocr_result" not in columns:
            conn.execute(
                "ALTER TABLE documents ADD COLUMN ocr_result TEXT"
            )
            conn.commit()

        _OCR_SCHEMA_READY = True


def get_db():

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    conn.execute(
        "PRAGMA busy_timeout = 30000"
    )

    _ensure_document_ocr_column(conn)

    return conn


# =========================================================
# HELPERS
# =========================================================

def current_time():

    return datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def student_logged_in():

    return "student_id" in session


def admin_logged_in():

    return "admin_id" in session


def generate_verification_id(
    usn,
    dob
):

    usn = (
        usn
        .strip()
        .upper()
    )

    last_three = usn[-3:]

    try:

        dob_date = datetime.strptime(
            dob,
            "%Y-%m-%d"
        )

        return (
            f"{last_three}-"
            f"{dob_date.strftime('%d-%m-%y')}"
        )

    except ValueError:

        return last_three


ALLOWED_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg"
}


def allowed_file(filename):

    if not filename:
        return False

    if "." not in filename:
        return False

    extension = (
        filename
        .rsplit(".", 1)[1]
        .lower()
    )

    return extension in ALLOWED_EXTENSIONS


def _normalize_comparison_value(value):
    return "".join(
        character.casefold()
        for character in str(value or "")
        if character.isalnum()
    )


def _ocr_comparison_status(expected, detected):
    if not detected:
        return "NOT_DETECTED"

    return (
        "MATCH"
        if _normalize_comparison_value(expected)
        == _normalize_comparison_value(detected)
        else "MISMATCH"
    )


def _identity_comparisons(ocr_result, student, submitted):
    return {
        "name": {
            "student_record": _ocr_comparison_status(
                student["name"],
                ocr_result.get("name"),
            ),
            "application": _ocr_comparison_status(
                submitted["name"],
                ocr_result.get("name"),
            ),
        },
        "usn": {
            "student_record": _ocr_comparison_status(
                student["usn"],
                ocr_result.get("usn"),
            ),
            "application": _ocr_comparison_status(
                submitted["usn"],
                ocr_result.get("usn"),
            ),
        },
    }


def _markcard_comparisons(ocr_result, student, submitted):
    comparisons = _identity_comparisons(ocr_result, student, submitted)
    comparisons["father_name"] = {
        "student_record": "NOT_STORED",
        "application": _ocr_comparison_status(
            submitted["parent_name"],
            ocr_result.get("father_name"),
        ),
    }
    return comparisons


def _identity_matches(comparisons):
    return not any(
        comparisons[field][source] != "MATCH"
        for field in ("name", "usn")
        for source in ("student_record", "application")
    )


def _markcard_identity_matches(comparisons):
    return (
        _identity_matches(comparisons)
        and comparisons["father_name"]["application"] != "MISMATCH"
    )


# =========================================================
# HOME
# =========================================================

@app.route("/")
def index():

    return render_template(
        "index.html"
    )


# =========================================================
# STUDENT LOGIN
# =========================================================

@app.route(
    "/student/login",
    methods=["GET", "POST"]
)
def student_login():

    if student_logged_in():

        return redirect(
            url_for("student_dashboard")
        )

    error = None

    if request.method == "POST":

        usn = (
            request.form
            .get("usn", "")
            .strip()
            .upper()
        )

        password = request.form.get(
            "password",
            ""
        )

        db = get_db()

        student = db.execute(
            """
            SELECT *
            FROM students
            WHERE usn = ?
            """,
            (usn,)
        ).fetchone()

        db.close()

        if (
            student
            and check_password_hash(
                student["password_hash"],
                password
            )
        ):

            # IMPORTANT:
            # Do NOT session.clear().
            # This preserves an admin login in another tab.

            session.permanent = True

            session["student_id"] = (
                student["id"]
            )

            return redirect(
                url_for(
                    "student_dashboard"
                )
            )

        error = (
            "Invalid USN or password."
        )

    return render_template(
        "student_login.html",
        error=error
    )


# =========================================================
# STUDENT LOGOUT
# =========================================================

@app.route("/student/logout")
def student_logout():

    # Only remove the student session.
    session.pop(
        "student_id",
        None
    )

    return redirect(
        url_for("index")
    )


# =========================================================
# STUDENT DASHBOARD
# =========================================================

@app.route("/student/dashboard")
def student_dashboard():

    if not student_logged_in():

        return redirect(
            url_for("student_login")
        )

    db = get_db()

    student = db.execute(
        """
        SELECT *
        FROM students
        WHERE id = ?
        """,
        (session["student_id"],)
    ).fetchone()

    if not student:

        db.close()

        session.pop(
            "student_id",
            None
        )

        return redirect(
            url_for("student_login")
        )

    scholarship = db.execute(
        """
        SELECT *
        FROM verifications
        WHERE student_id = ?
          AND purpose = 'SCHOLARSHIP'
        ORDER BY id DESC
        LIMIT 1
        """,
        (student["id"],)
    ).fetchone()

    admission = db.execute(
        """
        SELECT *
        FROM verifications
        WHERE student_id = ?
          AND purpose = 'HIGHER_SEMESTER_ADMISSION'
        ORDER BY id DESC
        LIMIT 1
        """,
        (student["id"],)
    ).fetchone()

    db.close()

    verification_id = generate_verification_id(
        student["usn"],
        student["dob"]
    )

    return render_template(
        "student_dashboard.html",
        student=student,
        scholarship=scholarship,
        admission=admission,
        verification_id=verification_id
    )


# =========================================================
# NORMAL STATUS API
# =========================================================

@app.route("/student/status")
def student_status():

    if not student_logged_in():

        response = jsonify({
            "success": False,
            "message": "Login required."
        })

        response.status_code = 401

        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )

        return response

    db = get_db()

    scholarship = db.execute(
        """
        SELECT
            id,
            verification_id,
            purpose,
            status,
            admin_message,
            submitted_at,
            reviewed_at,
            updated_at
        FROM verifications
        WHERE student_id = ?
          AND purpose = 'SCHOLARSHIP'
        ORDER BY id DESC
        LIMIT 1
        """,
        (session["student_id"],)
    ).fetchone()

    admission = db.execute(
        """
        SELECT
            id,
            verification_id,
            purpose,
            status,
            admin_message,
            submitted_at,
            reviewed_at,
            updated_at
        FROM verifications
        WHERE student_id = ?
          AND purpose = 'HIGHER_SEMESTER_ADMISSION'
        ORDER BY id DESC
        LIMIT 1
        """,
        (session["student_id"],)
    ).fetchone()

    db.close()

    response = jsonify({
        "success": True,

        "scholarship":
            row_to_status(scholarship),

        "admission":
            row_to_status(admission)
    })

    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0"
    )

    response.headers["Pragma"] = "no-cache"

    response.headers["Expires"] = "0"

    return response


def row_to_status(row):

    if not row:

        return {
            "id": None,
            "verification_id": "",
            "purpose": "",
            "status": "NOT_SUBMITTED",
            "admin_message": "",
            "submitted_at": "",
            "reviewed_at": "",
            "updated_at": ""
        }

    return {
        "id":
            row["id"],

        "verification_id":
            row["verification_id"],

        "purpose":
            row["purpose"],

        "status":
            row["status"],

        "admin_message":
            row["admin_message"]
            or "",

        "submitted_at":
            row["submitted_at"]
            or "",

        "reviewed_at":
            row["reviewed_at"]
            or "",

        "updated_at":
            row["updated_at"]
            or ""
    }


# =========================================================
# LIVE STATUS STREAM
#
# This replaces the old repeated browser polling.
#
# Browser opens one persistent connection.
# When updated_at/status/message changes, Flask immediately
# sends the new application status to that browser.
# =========================================================

@app.route(
    "/student/status/stream/<int:verification_id>"
)
def student_status_stream(
    verification_id
):

    if not student_logged_in():

        return (
            "Unauthorized",
            401
        )

    db = get_db()

    verification = db.execute(
        """
        SELECT
            v.*,
            s.name AS student_name
        FROM verifications v
        JOIN students s
            ON s.id = v.student_id
        WHERE v.id = ?
          AND v.student_id = ?
        """,
        (
            verification_id,
            session["student_id"]
        )
    ).fetchone()

    db.close()

    if not verification:

        return (
            "Application not found.",
            404
        )


    @stream_with_context
    def generate():

        last_state = None

        last_heartbeat = time.time()

        while True:

            try:

                db = get_db()

                row = db.execute(
                    """
                    SELECT
                        id,
                        verification_id,
                        purpose,
                        status,
                        admin_message,
                        submitted_at,
                        reviewed_at,
                        updated_at
                    FROM verifications
                    WHERE id = ?
                      AND student_id = ?
                    """,
                    (
                        verification_id,
                        session["student_id"]
                    )
                ).fetchone()

                db.close()


                if not row:
                    break


                payload = row_to_status(
                    row
                )


                state = json.dumps(
                    payload,
                    sort_keys=True
                )


                # Send only when something actually changed.
                if state != last_state:

                    yield (
                        "event: status_update\n"
                        "data: "
                        + state
                        + "\n\n"
                    )

                    last_state = state


                # Keep browser/proxy connection alive.
                if (
                    time.time()
                    - last_heartbeat
                    >= 10
                ):

                    yield ": heartbeat\n\n"

                    last_heartbeat = (
                        time.time()
                    )


                time.sleep(0.20)


            except GeneratorExit:

                break

            except Exception as error:

                print(
                    "Status stream error:",
                    error
                )

                break


    response = Response(
        generate(),
        mimetype="text/event-stream"
    )

    response.headers["Cache-Control"] = (
        "no-cache, no-store, must-revalidate"
    )

    response.headers["X-Accel-Buffering"] = "no"

    response.headers["Connection"] = "keep-alive"

    return response


# =========================================================
# PARTICULAR APPLICATION STATUS
# =========================================================

@app.route(
    "/student/check-status"
)
def student_check_status():

    if not student_logged_in():

        return redirect(
            url_for("student_login")
        )

    db = get_db()

    scholarship = db.execute(
        """
        SELECT *
        FROM verifications
        WHERE student_id = ?
          AND purpose = 'SCHOLARSHIP'
        ORDER BY id DESC
        LIMIT 1
        """,
        (session["student_id"],)
    ).fetchone()

    admission = db.execute(
        """
        SELECT *
        FROM verifications
        WHERE student_id = ?
          AND purpose = 'HIGHER_SEMESTER_ADMISSION'
        ORDER BY id DESC
        LIMIT 1
        """,
        (session["student_id"],)
    ).fetchone()

    db.close()

    return render_template(
        "student_status.html",
        scholarship=scholarship,
        admission=admission
    )


# =========================================================
# INDIVIDUAL APPLICATION STATUS
# =========================================================

@app.route(
    "/student/check-status/<int:verification_id>"
)
def student_application_status(
    verification_id
):

    if not student_logged_in():

        return redirect(
            url_for("student_login")
        )

    db = get_db()

    verification = db.execute(
        """
        SELECT *
        FROM verifications
        WHERE id = ?
          AND student_id = ?
        """,
        (
            verification_id,
            session["student_id"]
        )
    ).fetchone()

    if not verification:

        db.close()

        return (
            "Application not found.",
            404
        )

    if (
        verification["purpose"]
        ==
        "SCHOLARSHIP"
    ):

        submitted = db.execute(
            """
            SELECT *
            FROM scholarship_details
            WHERE verification_id = ?
            """,
            (verification_id,)
        ).fetchone()

    else:

        submitted = db.execute(
            """
            SELECT *
            FROM admission_details
            WHERE verification_id = ?
            """,
            (verification_id,)
        ).fetchone()

    documents = db.execute(
        """
        SELECT *
        FROM documents
        WHERE verification_id = ?
        ORDER BY id ASC
        """,
        (verification_id,)
    ).fetchall()

    db.close()

    return render_template(
        "student_application_status.html",
        verification=verification,
        submitted=submitted,
        documents=documents
    )


# =========================================================
# STUDENT VERIFICATION
# =========================================================

@app.route(
    "/student/verification",
    methods=["GET", "POST"]
)
def student_verification():

    if not student_logged_in():

        return redirect(
            url_for("student_login")
        )

    db = get_db()

    student = db.execute(
        """
        SELECT *
        FROM students
        WHERE id = ?
        """,
        (session["student_id"],)
    ).fetchone()

    if not student:

        db.close()

        session.pop(
            "student_id",
            None
        )

        return redirect(
            url_for("student_login")
        )

    verification_id = generate_verification_id(
        student["usn"],
        student["dob"]
    )


    # =====================================================
    # GET
    # =====================================================

    if request.method == "GET":

        selected_purpose = (
            request.args
            .get(
                "purpose",
                ""
            )
            .strip()
            .upper()
        )

        if selected_purpose not in [
            "SCHOLARSHIP",
            "HIGHER_SEMESTER_ADMISSION"
        ]:

            selected_purpose = ""

        resubmission_required = False
        if selected_purpose:
            latest_request = db.execute(
                """
                SELECT status
                FROM verifications
                WHERE student_id = ?
                  AND purpose = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    student["id"],
                    selected_purpose
                )
            ).fetchone()
            resubmission_required = (
                latest_request is not None
                and latest_request["status"] == "CORRECTION_REQUIRED"
            )

        db.close()

        return render_template(
            "student_verification.html",
            student=student,
            verification_id=verification_id,
            selected_purpose=selected_purpose,
            resubmission_required=resubmission_required
        )


    # =====================================================
    # POST
    # =====================================================

    purpose = (
        request.form
        .get(
            "purpose",
            ""
        )
        .strip()
        .upper()
    )

    if purpose not in [
        "SCHOLARSHIP",
        "HIGHER_SEMESTER_ADMISSION"
    ]:

        db.close()

        return (
            "Please select a valid verification service.",
            400
        )

    def render_admission_upload_error(message, status=400):
        latest_request = db.execute(
            """
            SELECT status
            FROM verifications
            WHERE student_id = ?
              AND purpose = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                student["id"],
                purpose
            )
        ).fetchone()
        resubmission_required = (
            latest_request is not None
            and latest_request["status"] == "CORRECTION_REQUIRED"
        )
        db.close()
        return (
            render_template(
                "student_verification.html",
                student=student,
                verification_id=verification_id,
                selected_purpose="HIGHER_SEMESTER_ADMISSION",
                resubmission_required=resubmission_required,
                form_error=message,
            ),
            status,
        )


    # =====================================================
    # ACTIVE REQUEST CHECK
    # =====================================================

    active_request = db.execute(
        """
        SELECT id
        FROM verifications
        WHERE student_id = ?
          AND purpose = ?
          AND status = 'UNDER_REVIEW'
        LIMIT 1
        """,
        (
            student["id"],
            purpose
        )
    ).fetchone()

    if active_request:

        db.close()

        return (
            "You already have an active request under review for this service.",
            400
        )


    # =====================================================
    # COMMON
    # =====================================================

    submitted_name = (
        request.form
        .get(
            "name",
            ""
        )
        .strip()
    )

    submitted_usn = (
        request.form
        .get(
            "usn",
            ""
        )
        .strip()
        .upper()
    )


    if not submitted_name:

        db.close()

        return (
            "Name is required.",
            400
        )


    if not submitted_usn:

        db.close()

        return (
            "USN is required.",
            400
        )


    # =====================================================
    # SCHOLARSHIP
    # =====================================================

    if purpose == "SCHOLARSHIP":

        ssp_id = (
            request.form
            .get(
                "ssp_id",
                ""
            )
            .strip()
        )

        mobile = (
            request.form
            .get(
                "mobile",
                ""
            )
            .strip()
        )

        nsp_otr = (
            request.form
            .get(
                "nsp_otr",
                ""
            )
            .strip()
        )

        acknowledgement = request.files.get(
            "acknowledgement"
        )


        if not ssp_id:

            db.close()

            return (
                "SSP ID is required.",
                400
            )


        if (
            not mobile.isdigit()
            or len(mobile) != 10
        ):

            db.close()

            return (
                "Please enter a valid 10-digit mobile number.",
                400
            )


        if not nsp_otr:

            db.close()

            return (
                "NSP OTR number is required.",
                400
            )


        if (
            not acknowledgement
            or not acknowledgement.filename
        ):

            db.close()

            return (
                "Scholarship acknowledgement is required.",
                400
            )


        if not allowed_file(
            acknowledgement.filename
        ):

            db.close()

            return (
                "Acknowledgement must be PDF, JPG, JPEG or PNG.",
                400
            )


        name_match = (
            submitted_name
            .strip()
            .lower()
            ==
            student["name"]
            .strip()
            .lower()
        )


        usn_match = (
            submitted_usn
            ==
            student["usn"]
            .strip()
            .upper()
        )


        match_status = (
            "MATCH"
            if name_match and usn_match
            else "MISMATCH"
        )


        now = current_time()


        cursor = db.execute(
            """
            INSERT INTO verifications (
                student_id,
                verification_id,
                purpose,
                status,
                admin_message,
                ocr_name,
                ocr_usn,
                match_status,
                submitted_at,
                reviewed_at,
                reviewed_by,
                updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                student["id"],
                verification_id,
                purpose,
                "UNDER_REVIEW",
                None,
                None,
                None,
                match_status,
                now,
                None,
                None,
                now
            )
        )


        verification_db_id = (
            cursor.lastrowid
        )


        # Scholarship details
        db.execute(
            """
            INSERT INTO scholarship_details (
                verification_id,
                name,
                usn,
                ssp_id,
                mobile,
                nsp_otr
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                verification_db_id,
                submitted_name,
                submitted_usn,
                ssp_id,
                mobile,
                nsp_otr
            )
        )


        # Scholarship acknowledgement
        original_name = secure_filename(
            acknowledgement.filename
        )

        stored_name = (
            uuid.uuid4().hex
            + "_"
            + original_name
        )

        acknowledgement.save(
            os.path.join(
                UPLOAD_FOLDER,
                stored_name
            )
        )


        db.execute(
            """
            INSERT INTO documents (
                verification_id,
                doc_type,
                filename,
                original_name
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                verification_db_id,
                "SCHOLARSHIP_ACKNOWLEDGEMENT",
                stored_name,
                acknowledgement.filename
            )
        )


        # Audit
        db.execute(
            """
            INSERT INTO audit_logs (
                verification_id,
                action,
                details,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                verification_db_id,
                "SUBMITTED",
                "Scholarship verification with acknowledgement submitted.",
                now
            )
        )


        db.commit()

        db.close()


        return redirect(
            url_for(
                "student_application_status",
                verification_id=verification_db_id
            )
        )


    # =====================================================
    # HIGHER SEMESTER ADMISSION
    # =====================================================

    parent_name = (
        request.form
        .get(
            "parent_name",
            ""
        )
        .strip()
    )

    course = (
        request.form
        .get(
            "course",
            ""
        )
        .strip()
    )

    branch = (
        request.form
        .get(
            "branch",
            ""
        )
        .strip()
    )

    semester = (
        request.form
        .get(
            "semester",
            ""
        )
        .strip()
    )

    seat_category = (
        request.form
        .get(
            "seat_category",
            ""
        )
        .strip()
    )

    address = (
        request.form
        .get(
            "address",
            ""
        )
        .strip()
    )

    mobile = (
        request.form
        .get(
            "mobile",
            ""
        )
        .strip()
    )

    marks_cards = [
        file
        for file in request.files.getlist(
            "marks_cards"
        )
        if file
        and file.filename
    ]

    id_card = request.files.get(
        "id_card"
    )


    validations = [
        (
            parent_name,
            "Father/Mother name is required."
        ),
        (
            course,
            "Course is required."
        ),
        (
            branch,
            "Branch is required."
        ),
        (
            semester,
            "Semester is required."
        ),
        (
            seat_category,
            "Seat allotment category is required."
        ),
        (
            address,
            "Address is required."
        )
    ]


    for value, message in validations:

        if not value:

            db.close()

            return (
                message,
                400
            )


    if (
        not mobile.isdigit()
        or len(mobile) != 10
    ):

        db.close()

        return (
            "Please enter a valid 10-digit mobile number.",
            400
        )


    if not marks_cards:
        return render_admission_upload_error(
            "MARKS CARD ERROR: Please upload at least one marks card."
        )


    if (
        not id_card
        or not id_card.filename
    ):
        return render_admission_upload_error(
            "ID CARD ERROR: Please upload the student ID card."
        )


    for file in marks_cards:

        if not allowed_file(
            file.filename
        ):
            return render_admission_upload_error(
                "MARKS CARD ERROR: Upload a PDF, JPG, JPEG or PNG file."
            )


    if not allowed_file(
        id_card.filename
    ):
        return render_admission_upload_error(
            "ID CARD ERROR: Upload a PDF, JPG, JPEG or PNG file."
        )

    markcard_ocr_results = []
    for file in marks_cards:
        ocr_result = read_markcard(file)
        error_type = ocr_result.pop("error_type", None)
        if error_type:
            app.logger.warning(
                "Marks-card OCR failed (%s).",
                error_type,
            )

            if ocr_result.get("ocr_status") == "UNAVAILABLE":
                return render_admission_upload_error(
                    "MARKS CARD OCR ERROR: Verification is unavailable because PDF/OCR dependencies are missing. Install the project requirements and restart the app.",
                    503
                )

            if error_type == "TesseractNotFoundError":
                return render_admission_upload_error(
                    "MARKS CARD OCR ERROR: This file needs image OCR, but Tesseract is not installed. Install Tesseract or upload a searchable PDF.",
                    503
                )

        if not is_valid_markcard(ocr_result):
            return render_admission_upload_error(
                "MARKS CARD ERROR: This file is not a readable valid grade card. Upload a marks card showing the student's name, USN, CGPA, SGPA, and subjects.",
            )

        comparisons = _markcard_comparisons(
            ocr_result,
            student,
            {
                "name": submitted_name,
                "usn": submitted_usn,
                "parent_name": parent_name,
            },
        )
        if not _markcard_identity_matches(comparisons):
            return render_admission_upload_error(
                "MARKS CARD MISMATCH: The name, USN, or parent name does not match the student record and application. Upload the correct student's marks card.",
            )

        ocr_result["comparisons"] = comparisons
        file.stream.seek(0)
        markcard_ocr_results.append(ocr_result)


    id_card_ocr_result = read_student_id(id_card)
    error_type = id_card_ocr_result.pop("error_type", None)
    if error_type:
        app.logger.warning("Student ID OCR failed (%s).", error_type)

        if id_card_ocr_result.get("ocr_status") == "UNAVAILABLE":
            return render_admission_upload_error(
                "ID CARD OCR ERROR: Verification is unavailable because PDF/OCR dependencies are missing. Install the project requirements and restart the app.",
                503
            )

        if error_type == "TesseractNotFoundError":
            return render_admission_upload_error(
                "ID CARD OCR ERROR: This file needs image OCR, but Tesseract is not installed. Install Tesseract or upload a searchable PDF.",
                503
            )

    if not id_card_ocr_result.get("name") or not id_card_ocr_result.get("usn"):
        return render_admission_upload_error(
            "ID CARD ERROR: Could not read both the student name and USN. Upload a clear ID card showing both details.",
        )

    id_card_ocr_result["comparisons"] = _identity_comparisons(
        id_card_ocr_result,
        student,
        {
            "name": submitted_name,
            "usn": submitted_usn,
        },
    )
    if not _identity_matches(id_card_ocr_result["comparisons"]):
        return render_admission_upload_error(
            "ID CARD MISMATCH: The name or USN does not match the student record and application. Upload the correct student's ID card.",
        )

    ocr_name = id_card_ocr_result["name"]
    ocr_usn = id_card_ocr_result["usn"]
    match_status = "MATCH"


    # =====================================================
    # SAVE ADMISSION
    # =====================================================

    now = current_time()


    cursor = db.execute(
        """
        INSERT INTO verifications (
            student_id,
            verification_id,
            purpose,
            status,
            admin_message,
            ocr_name,
            ocr_usn,
            match_status,
            submitted_at,
            reviewed_at,
            reviewed_by,
            updated_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            student["id"],
            verification_id,
            purpose,
            "UNDER_REVIEW",
            None,
            ocr_name or None,
            ocr_usn or None,
            match_status,
            now,
            None,
            None,
            now
        )
    )


    verification_db_id = (
        cursor.lastrowid
    )


    db.execute(
        """
        INSERT INTO admission_details (
            verification_id,
            name,
            usn,
            parent_name,
            course,
            branch,
            semester,
            seat_category,
            address,
            mobile
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            verification_db_id,
            submitted_name,
            submitted_usn,
            parent_name,
            course,
            branch,
            semester,
            seat_category,
            address,
            mobile
        )
    )


    # =====================================================
    # MARKS CARDS
    # =====================================================

    for file, ocr_result in zip(marks_cards, markcard_ocr_results):

        file.stream.seek(0)

        original_name = secure_filename(
            file.filename
        )

        stored_name = (
            uuid.uuid4().hex
            + "_"
            + original_name
        )

        file.save(
            os.path.join(
                UPLOAD_FOLDER,
                stored_name
            )
        )


        db.execute(
            """
            INSERT INTO documents (
                verification_id,
                doc_type,
                filename,
                original_name,
                ocr_result
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                verification_db_id,
                "MARKS_CARD",
                stored_name,
                file.filename,
                json.dumps(ocr_result),
            )
        )


    # =====================================================
    # ID CARD
    # =====================================================

    id_card.stream.seek(0)

    original_name = secure_filename(
        id_card.filename
    )

    stored_name = (
        uuid.uuid4().hex
        + "_"
        + original_name
    )


    id_card.save(
        os.path.join(
            UPLOAD_FOLDER,
            stored_name
        )
    )


    db.execute(
        """
        INSERT INTO documents (
            verification_id,
            doc_type,
            filename,
            original_name,
            ocr_result
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            verification_db_id,
            "ID_CARD",
            stored_name,
            id_card.filename,
            json.dumps(id_card_ocr_result),
        )
    )


    # =====================================================
    # AUDIT
    # =====================================================

    db.execute(
        """
        INSERT INTO audit_logs (
            verification_id,
            action,
            details,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            verification_db_id,
            "SUBMITTED",
            "Higher semester admission submitted.",
            now
        )
    )


    db.commit()

    db.close()


    return redirect(
        url_for(
            "student_application_status",
            verification_id=verification_db_id
        )
    )


# =========================================================
# ADMIN LOGIN
# =========================================================

@app.route(
    "/admin/login",
    methods=["GET", "POST"]
)
def admin_login():

    if admin_logged_in():

        return redirect(
            url_for("admin_dashboard")
        )


    error = None


    if request.method == "POST":

        admin_id = (
            request.form
            .get(
                "admin_id",
                ""
            )
            .strip()
            .upper()
        )

        password = request.form.get(
            "password",
            ""
        )


        db = get_db()


        admin = db.execute(
            """
            SELECT *
            FROM admins
            WHERE admin_id = ?
            """,
            (admin_id,)
        ).fetchone()


        db.close()


        if (
            admin
            and check_password_hash(
                admin["password_hash"],
                password
            )
        ):

            # Do NOT clear student session.
            session.permanent = True

            session["admin_id"] = (
                admin["id"]
            )

            return redirect(
                url_for("admin_dashboard")
            )


        error = (
            "Invalid administrator credentials."
        )


    return render_template(
        "admin_login.html",
        error=error
    )


# =========================================================
# ADMIN LOGOUT
# =========================================================

@app.route("/admin/logout")
def admin_logout():

    # Only remove admin session.
    session.pop(
        "admin_id",
        None
    )

    return redirect(
        url_for("index")
    )


# =========================================================
# ADMIN DASHBOARD
# =========================================================

@app.route("/admin/dashboard")
def admin_dashboard():

    if not admin_logged_in():

        return redirect(
            url_for("admin_login")
        )


    db = get_db()


    stats = {}


    stats["total"] = db.execute(
        """
        SELECT COUNT(*)
        FROM verifications
        """
    ).fetchone()[0]


    stats["under_review"] = db.execute(
        """
        SELECT COUNT(*)
        FROM verifications
        WHERE status = 'UNDER_REVIEW'
        """
    ).fetchone()[0]


    stats["correction_required"] = db.execute(
        """
        SELECT COUNT(*)
        FROM verifications
        WHERE status = 'CORRECTION_REQUIRED'
        """
    ).fetchone()[0]


    stats["verified"] = db.execute(
        """
        SELECT COUNT(*)
        FROM verifications
        WHERE status = 'VERIFIED'
        """
    ).fetchone()[0]


    stats["rejected"] = db.execute(
        """
        SELECT COUNT(*)
        FROM verifications
        WHERE status = 'REJECTED'
        """
    ).fetchone()[0]


    stats["scholarship_pending"] = db.execute(
        """
        SELECT COUNT(*)
        FROM verifications
        WHERE purpose = 'SCHOLARSHIP'
          AND status = 'UNDER_REVIEW'
        """
    ).fetchone()[0]


    stats["admission_pending"] = db.execute(
        """
        SELECT COUNT(*)
        FROM verifications
        WHERE purpose = 'HIGHER_SEMESTER_ADMISSION'
          AND status = 'UNDER_REVIEW'
        """
    ).fetchone()[0]


    db.close()


    return render_template(
        "admin_dashboard.html",
        stats=stats
    )


# =========================================================
# ADMIN SCHOLARSHIP QUEUE
# =========================================================

@app.route("/admin/scholarships")
def admin_scholarships():

    return admin_queue(
        "SCHOLARSHIP"
    )


# =========================================================
# ADMIN ADMISSION QUEUE
# =========================================================

@app.route("/admin/admissions")
def admin_admissions():

    return admin_queue(
        "HIGHER_SEMESTER_ADMISSION"
    )


# =========================================================
# ADMIN QUEUE
# =========================================================

def admin_queue(purpose):

    if not admin_logged_in():

        return redirect(
            url_for("admin_login")
        )


    db = get_db()


    verifications = db.execute(
        """
        SELECT
            v.id,
            v.verification_id,
            v.purpose,
            v.status,
            v.match_status,
            v.submitted_at,
            v.updated_at,
            s.name AS student_name,
            s.usn AS student_usn
        FROM verifications v
        JOIN students s
            ON s.id = v.student_id
        WHERE v.purpose = ?
        ORDER BY
            CASE
                WHEN v.status = 'UNDER_REVIEW'
                THEN 0
                ELSE 1
            END,
            v.id DESC
        """,
        (purpose,)
    ).fetchall()


    db.close()


    return render_template(
        "queue.html",
        purpose=purpose,
        verifications=verifications
    )


# =========================================================
# ADMIN REVIEW DETAIL
# =========================================================

@app.route(
    "/admin/verification/<int:verification_id>"
)
def admin_verification_detail(
    verification_id
):

    if not admin_logged_in():

        return redirect(
            url_for("admin_login")
        )


    db = get_db()


    verification = db.execute(
        """
        SELECT *
        FROM verifications
        WHERE id = ?
        """,
        (verification_id,)
    ).fetchone()


    if not verification:

        db.close()

        return (
            "Verification request not found.",
            404
        )


    student = db.execute(
        """
        SELECT *
        FROM students
        WHERE id = ?
        """,
        (verification["student_id"],)
    ).fetchone()


    if (
        verification["purpose"]
        ==
        "SCHOLARSHIP"
    ):

        submitted = db.execute(
            """
            SELECT *
            FROM scholarship_details
            WHERE verification_id = ?
            """,
            (verification_id,)
        ).fetchone()

    else:

        submitted = db.execute(
            """
            SELECT *
            FROM admission_details
            WHERE verification_id = ?
            """,
            (verification_id,)
        ).fetchone()


    documents = db.execute(
        """
        SELECT *
        FROM documents
        WHERE verification_id = ?
        ORDER BY id ASC
        """,
        (verification_id,)
    ).fetchall()

    verification = dict(verification)
    reviewed_documents = []
    for row in documents:
        document = dict(row)
        raw_ocr_result = document.get("ocr_result")
        ocr_result = None
        should_read_legacy_id = (
            document["doc_type"] == "ID_CARD"
            and not raw_ocr_result
            and verification["purpose"] == "HIGHER_SEMESTER_ADMISSION"
        )

        if raw_ocr_result:
            try:
                decoded_result = json.loads(raw_ocr_result)
            except json.JSONDecodeError:
                app.logger.warning(
                    "Invalid stored OCR result for document %s.",
                    document["id"],
                )
                decoded_result = None

            if isinstance(decoded_result, dict):
                ocr_result = decoded_result
            else:
                ocr_result = {
                    "error": "Saved OCR results are unavailable.",
                    "ocr_status": "FAILED",
                }

        if should_read_legacy_id:
            id_card_path = os.path.join(
                UPLOAD_FOLDER,
                os.path.basename(document["filename"]),
            )
            if os.path.isfile(id_card_path):
                with open(id_card_path, "rb") as stream:
                    ocr_result = read_student_id(
                        FileStorage(
                            stream=stream,
                            filename=document["original_name"],
                        )
                    )
            else:
                ocr_result = {
                    "name": None,
                    "usn": None,
                    "error": "The saved ID-card file is not available for OCR.",
                    "ocr_status": "FAILED",
                }

            error_type = ocr_result.pop("error_type", None)
            if error_type:
                app.logger.warning(
                    "Legacy student ID OCR failed (%s).",
                    error_type,
                )

            db.execute(
                "UPDATE documents SET ocr_result = ? WHERE id = ?",
                (json.dumps(ocr_result), document["id"]),
            )

        if ocr_result is not None and submitted:
            if document["doc_type"] == "MARKS_CARD":
                ocr_result["comparisons"] = _markcard_comparisons(
                    ocr_result,
                    student,
                    submitted,
                )
            elif document["doc_type"] == "ID_CARD":
                ocr_result["comparisons"] = _identity_comparisons(
                    ocr_result,
                    student,
                    submitted,
                )

        if (
            document["doc_type"] == "ID_CARD"
            and verification["purpose"] == "HIGHER_SEMESTER_ADMISSION"
            and ocr_result is not None
        ):
            verification["ocr_name"] = ocr_result.get("name")
            verification["ocr_usn"] = ocr_result.get("usn")
            if (
                ocr_result.get("name")
                and ocr_result.get("usn")
                and ocr_result.get("comparisons")
            ):
                verification["match_status"] = (
                    "MATCH"
                    if _identity_matches(ocr_result["comparisons"])
                    else "MISMATCH"
                )
            else:
                verification["match_status"] = "NOT_DETECTED"

            db.execute(
                """
                UPDATE verifications
                SET ocr_name = ?, ocr_usn = ?, match_status = ?
                WHERE id = ?
                """,
                (
                    verification["ocr_name"],
                    verification["ocr_usn"],
                    verification["match_status"],
                    verification_id,
                ),
            )

        document["ocr_result"] = ocr_result
        reviewed_documents.append(document)

    documents = reviewed_documents
    db.commit()


    audit_logs = db.execute(
        """
        SELECT *
        FROM audit_logs
        WHERE verification_id = ?
        ORDER BY id DESC
        """,
        (verification_id,)
    ).fetchall()


    db.close()


    return render_template(
        "admin_verification_detail.html",
        verification=verification,
        student=student,
        submitted=submitted,
        documents=documents,
        audit_logs=audit_logs
    )


# =========================================================
# ADMIN DECISION
# =========================================================

@app.route(
    "/admin/verification/<int:verification_id>/decision",
    methods=["POST"]
)
def admin_decision(
    verification_id
):

    if not admin_logged_in():

        if request.is_json:

            return jsonify({
                "success": False,
                "message":
                    "Admin login required."
            }), 401

        return redirect(
            url_for("admin_login")
        )


    # =====================================================
    # JSON OR FORM
    # =====================================================

    if request.is_json:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

    else:

        data = request.form.to_dict()


    decision = (
        data
        .get(
            "decision",
            ""
        )
        .strip()
        .upper()
    )


    admin_message = (
        data
        .get(
            "admin_message",
            ""
        )
        .strip()
    )


    allowed_decisions = {
        "VERIFIED",
        "REJECTED",
        "CORRECTION_REQUIRED"
    }


    if decision not in allowed_decisions:

        if request.is_json:

            return jsonify({
                "success": False,
                "message":
                    "Please select a valid decision."
            }), 400

        return (
            "Please select a valid decision.",
            400
        )


    if (
        decision in {
            "REJECTED",
            "CORRECTION_REQUIRED"
        }
        and not admin_message
    ):

        if request.is_json:

            return jsonify({
                "success": False,
                "message":
                    "Please enter a message for the student."
            }), 400

        return (
            "Please enter a message for the student.",
            400
        )


    # =====================================================
    # GET APPLICATION
    # =====================================================

    db = get_db()


    verification = db.execute(
        """
        SELECT *
        FROM verifications
        WHERE id = ?
        """,
        (verification_id,)
    ).fetchone()


    if not verification:

        db.close()

        if request.is_json:

            return jsonify({
                "success": False,
                "message":
                    "Verification request not found."
            }), 404

        return (
            "Verification request not found.",
            404
        )

    if verification["status"] != "UNDER_REVIEW":
        db.close()
        message = (
            "A decision has already been recorded for this request. "
            "It cannot be changed."
        )
        if request.is_json:
            return jsonify({
                "success": False,
                "message": message,
            }), 409
        return message, 409


    # =====================================================
    # AUTO SUCCESS MESSAGE
    # =====================================================

    if decision == "VERIFIED":

        if (
            verification["purpose"]
            ==
            "SCHOLARSHIP"
        ):

            final_message = (
                "Scholarship verification successful."
            )

        else:

            final_message = (
                "Admission is successful."
            )

    else:

        final_message = admin_message


    now = current_time()


    # =====================================================
    # UPDATE APPLICATION
    # =====================================================

    cursor = db.execute(
        """
        UPDATE verifications
        SET
            status = ?,
            admin_message = ?,
            reviewed_at = ?,
            reviewed_by = ?,
            updated_at = ?
        WHERE id = ?
          AND status = 'UNDER_REVIEW'
        """,
        (
            decision,
            final_message,
            now,
            session["admin_id"],
            now,
            verification_id
        )
    )


    if cursor.rowcount != 1:

        db.rollback()

        db.close()

        message = (
            "A decision has already been recorded for this request. "
            "It cannot be changed."
        )
        if request.is_json:

            return jsonify({
                "success": False,
                "message": message,
            }), 409

        return (
            message,
            409
        )


    # =====================================================
    # AUDIT
    # =====================================================

    db.execute(
        """
        INSERT INTO audit_logs (
            verification_id,
            action,
            details,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            verification_id,
            decision,
            final_message,
            now
        )
    )


    # =====================================================
    # COMMIT FIRST
    #
    # The student live stream reads this committed value.
    # =====================================================

    db.commit()


    # =====================================================
    # VERIFY SAVED RECORD
    # =====================================================

    saved = db.execute(
        """
        SELECT
            id,
            verification_id,
            purpose,
            status,
            admin_message,
            submitted_at,
            reviewed_at,
            updated_at
        FROM verifications
        WHERE id = ?
        """,
        (verification_id,)
    ).fetchone()


    db.close()


    # =====================================================
    # CORRECT ADMIN QUEUE
    # =====================================================

    if (
        verification["purpose"]
        ==
        "SCHOLARSHIP"
    ):

        redirect_url = url_for(
            "admin_scholarships"
        )

    else:

        redirect_url = url_for(
            "admin_admissions"
        )


    # =====================================================
    # AJAX
    # =====================================================

    if request.is_json:

        response = jsonify({

            "success": True,

            "message":
                saved["admin_message"],

            "status":
                saved["status"],

            "verification_id":
                saved["verification_id"],

            "updated_at":
                saved["updated_at"],

            "redirect_url":
                redirect_url

        })

        response.headers["Cache-Control"] = (
            "no-store"
        )

        return response


    # =====================================================
    # NORMAL FORM
    # =====================================================

    return redirect(
        redirect_url
    )


# =========================================================
# ADMIN DOCUMENT
# =========================================================

@app.route(
    "/admin/document/<path:filename>"
)
def admin_document(filename):

    if not admin_logged_in():

        return (
            "Unauthorized.",
            401
        )

    return send_from_directory(
        UPLOAD_FOLDER,
        filename
    )


# =========================================================
# FILE SIZE ERROR
# =========================================================

@app.errorhandler(413)
def file_too_large(error):

    return (
        "Uploaded files cannot exceed 20 MB.",
        413
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    print("=" * 60)
    print("SecureVerify")
    print("Ballari Institute of Technology and Management")
    print("http://127.0.0.1:5000")
    print("=" * 60)

    app.run(
        debug=True,
        threaded=True
    )