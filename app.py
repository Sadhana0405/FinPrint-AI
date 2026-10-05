import json
import logging
import math
import re
import secrets
from copy import deepcopy
from hmac import compare_digest

from flask import Flask, flash, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.utils import secure_filename

import config
from utils.analysis import analyze_financial_documents, compare_hospital_evidence, extract_document
from utils.auth import hash_password, is_logged_in, login_required, verify_password
from utils.db import close_connection, get_connection
from utils.files import analysis_directory, cleanup_analysis_files, save_upload, validate_upload
from utils.gemini import GeminiAnalysisError, analyze_financial_documents as gemini_financial_analysis

app = Flask(__name__)
if not config.has_strong_secret(config.SECRET_KEY):
    raise RuntimeError(
        "FLASK_SECRET_KEY must be configured with a random value of at least 32 characters."
    )
if (
    config.MAX_UPLOAD_SIZE_MB <= 0
    or config.MAX_REQUEST_SIZE_MB < config.MAX_UPLOAD_SIZE_MB
    or config.MAX_FILES_PER_REVIEW <= 0
):
    raise RuntimeError(
        "MAX_REQUEST_SIZE_MB must be at least MAX_UPLOAD_SIZE_MB, and both must be positive."
    )
app.secret_key = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH
app.config["MAX_UPLOAD_SIZE_MB"] = config.MAX_UPLOAD_SIZE_MB
app.config["MAX_REQUEST_SIZE_MB"] = config.MAX_REQUEST_SIZE_MB
app.config["MAX_FILES_PER_REVIEW"] = config.MAX_FILES_PER_REVIEW
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = config.SESSION_COOKIE_SECURE
app.config["MAX_UPLOAD_SIZE_BYTES"] = config.MAX_UPLOAD_SIZE_BYTES

logger = logging.getLogger(__name__)
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
QUANTITY_VALUE_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)\s*([A-Za-z]+)?$")
MODULE_LABELS = {
    "financial": "Financial Document Analyzer",
    "hospital": "Hospital Bill Verifier",
}


def current_user_id():
    return session.get("user_id")


def current_user_name():
    return session.get("user_name")


def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.before_request
def protect_csrf():
    if request.method != "POST":
        return None
    expected = session.get("_csrf_token")
    provided = request.form.get("_csrf_token", "")
    if not expected or not provided or not compare_digest(expected, provided):
        return "The form expired or could not be verified. Reload the page and try again.", 400
    return None


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def query_one(sql, values=None):
    connection = get_connection()
    if connection is None:
        return None, "The database is unavailable. Please try again later."

    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(sql, values or ())
        row = cursor.fetchone()
        cursor.close()
        return row, None
    except Exception:
        logger.exception("Database read-one query failed")
        return None, "The database request could not be completed. Please try again."
    finally:
        close_connection(connection)


def query_all(sql, values=None):
    connection = get_connection()
    if connection is None:
        return None, "The database is unavailable. Please try again later."

    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(sql, values or ())
        rows = cursor.fetchall()
        cursor.close()
        return rows, None
    except Exception:
        logger.exception("Database read-many query failed")
        return None, "The database request could not be completed. Please try again."
    finally:
        close_connection(connection)


def execute_query(sql, values=None):
    connection = get_connection()
    if connection is None:
        return None, "The database is unavailable. Please try again later."

    try:
        cursor = connection.cursor()
        cursor.execute(sql, values or ())
        connection.commit()
        last_id = cursor.lastrowid
        cursor.close()
        return last_id, None
    except Exception:
        logger.exception("Database write query failed")
        return None, "The database request could not be completed. Please try again."
    finally:
        close_connection(connection)


def named_files(field_name):
    return [
        item for item in request.files.getlist(field_name) if item and item.filename
    ]


def split_filenames(filenames):
    if not filenames:
        return []
    parts = []
    for chunk in filenames.replace(" | ", "\n").split("\n"):
        for piece in chunk.split(","):
            text = piece.strip(" ;")
            if text:
                parts.append(text)
    return parts


def save_analysis(module_type, filenames, result, title=None):
    return execute_query(
        """
        INSERT INTO analyses (user_id, module_type, filenames, title, result)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (current_user_id(), module_type, filenames, title, result),
    )


def secure_upload_name(filename):
    return secure_filename(filename) or "upload"


def disambiguate_source_names(documents):
    seen = set()
    counts = {}
    for document in documents:
        original = document["filename"]
        occurrence = counts.get(original, 0) + 1
        candidate = original
        if occurrence > 1:
            stem, extension = original.rsplit(".", 1) if "." in original else (original, "")
            suffix = f" ({occurrence})"
            candidate = f"{stem}{suffix}.{extension}" if extension else f"{stem}{suffix}"
        while candidate in seen:
            occurrence += 1
            stem, extension = original.rsplit(".", 1) if "." in original else (original, "")
            suffix = f" ({occurrence})"
            candidate = f"{stem}{suffix}.{extension}" if extension else f"{stem}{suffix}"
        counts[original] = occurrence
        document["filename"] = candidate
        seen.add(candidate)


def validate_labeled_files(items):
    for label, file_storage in items:
        error = validate_upload(file_storage)
        if error:
            return f"{label}: {error}"
    return None


def save_labeled_files(items, module_type, analysis_id):
    saved_items = []
    try:
        for label, file_storage in items:
            saved = save_upload(
                file_storage,
                module_type,
                current_user_id(),
                analysis_id,
            )
            saved_items.append((label, saved))
    except Exception:
        logger.exception("Could not store an uploaded document")
        if not cleanup_analysis_files(module_type, current_user_id(), analysis_id):
            logger.error("Partial-upload cleanup failed for analysis %s", analysis_id)
        return None, "A document could not be stored. Please try again."
    return saved_items, None


def rollback_analysis(module_type, analysis_id):
    if not cleanup_analysis_files(module_type, current_user_id(), analysis_id):
        logger.error("Upload cleanup failed for rolled-back analysis %s", analysis_id)
    _, error = execute_query(
        "DELETE FROM analyses WHERE id = %s AND user_id = %s",
        (analysis_id, current_user_id()),
    )
    if error:
        logger.error("Database rollback failed for analysis %s", analysis_id)


def store_analysis_result(module_type, analysis_id, filenames, title, result):
    stored_result = deepcopy(result)
    serialized = json.dumps(stored_result, ensure_ascii=False)
    shortened = False
    while len(serialized.encode("utf-8")) > 58_000:
        strings = []

        def collect_strings(value):
            if isinstance(value, dict):
                for child in value.values():
                    collect_strings(child)
            elif isinstance(value, list):
                for child in value:
                    collect_strings(child)
            elif isinstance(value, str) and len(value) > 100:
                strings.append(value)

        collect_strings(stored_result)
        if not strings:
            rollback_analysis(module_type, analysis_id)
            return "The analysis result was too large to store safely."
        largest = max(strings, key=len)
        shortened_value = largest[: max(100, len(largest) // 2)]

        def shorten(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if child is largest:
                        value[key] = shortened_value
                    else:
                        shorten(child)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    if child is largest:
                        value[index] = shortened_value
                    else:
                        shorten(child)

        shorten(stored_result)
        serialized = json.dumps(stored_result, ensure_ascii=False)
        shortened = True
    if shortened and isinstance(stored_result.get("limitations"), list):
        stored_result["limitations"].append(
            "Some long text was shortened to fit the existing database result column."
        )
        serialized = json.dumps(stored_result, ensure_ascii=False)
    _, error = execute_query(
        """
        UPDATE analyses
        SET filenames = %s, title = %s, result = %s
        WHERE id = %s AND user_id = %s
        """,
        (filenames, title, serialized, analysis_id, current_user_id()),
    )
    if error:
        rollback_analysis(module_type, analysis_id)
        return error
    return None


def enrich_records(records):
    enriched = []
    for record in records or []:
        item = dict(record)
        item["module_label"] = MODULE_LABELS.get(record["module_type"], record["module_type"])
        item["status"] = "Stored"
        dt = item.get("created_at")
        if dt and hasattr(dt, "strftime"):
            item["formatted_date"] = dt.strftime("%b %d, %Y · %I:%M %p")
            item["date_only"] = dt.strftime("%b %d, %Y")
            item["time_only"] = dt.strftime("%I:%M %p")
            item["iso_date"] = dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            item["formatted_date"] = str(dt or "")
            item["date_only"] = str(dt or "")
            item["time_only"] = ""
            item["iso_date"] = ""
        item["doc_list"] = split_filenames(item.get("filenames"))
        item["display_title"] = item.get("title") or item.get("filenames") or "Document Review"
        item["title"] = item.get("title") or ""
        enriched.append(item)
    return enriched


def parse_analysis_result(raw_result):
    """Parse structured results while preserving readable legacy records."""
    if not raw_result:
        return {}
    try:
        parsed = json.loads(raw_result)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return {
        "module_type": "legacy",
        "status": "Legacy record",
        "summary": "This review predates structured analysis and cannot be reinterpreted safely.",
        "documents": [],
        "findings": [],
        "key_findings": [],
        "legacy_text": re.sub(r"(?m)^Storage:\s*[^\r\n]*\r?\n?", "", raw_result).strip(),
        "limitations": ["Legacy content is shown as unstructured text, not as verified findings."],
    }


@app.errorhandler(413)
def file_too_large(_error):
    flash(
        f"The total upload request exceeds {config.MAX_REQUEST_SIZE_MB} MB. "
        f"Each individual file must be no larger than {config.MAX_UPLOAD_SIZE_MB} MB.",
        "error",
    )
    return redirect(url_for("dashboard"))


@app.context_processor
def inject_auth():
    return {
        "logged_in": is_logged_in(),
        "user_name": current_user_name(),
        "active_page": request.endpoint,
        "csrf_token": csrf_token,
    }


@app.route("/")
def index():
    if is_logged_in():
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if is_logged_in():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        if not name or len(name) > 100 or not email or len(email) > 255 or not password or not confirm_password:
            flash("All fields are required.", "error")
            return render_template("register.html")

        if not EMAIL_PATTERN.match(email):
            flash("Please enter a valid email address.", "error")
            return render_template("register.html")

        if len(password) < 8 or len(password) > 128:
            flash("Password must be between 8 and 128 characters.", "error")
            return render_template("register.html")

        if password != confirm_password:
            flash("Password and confirm password must match.", "error")
            return render_template("register.html")

        existing, error = query_one(
            "SELECT id FROM users WHERE email = %s",
            (email,),
        )
        if error:
            flash(error, "error")
            return render_template("register.html")
        if existing:
            flash("An account with this email already exists.", "error")
            return render_template("register.html")

        _, error = execute_query(
            """
            INSERT INTO users (name, email, password_hash)
            VALUES (%s, %s, %s)
            """,
            (name, email, hash_password(password)),
        )
        if error:
            flash(error, "error")
            return render_template("register.html")

        flash("Your account is ready. Please sign in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if is_logged_in():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        if not email or len(email) > 255 or not password or len(password) > 128:
            flash("Email and password are required.", "error")
            return render_template("login.html")

        user, error = query_one(
            "SELECT id, name, email, password_hash FROM users WHERE email = %s",
            (email,),
        )
        if error:
            flash(error, "error")
            return render_template("login.html")

        if user is None or not verify_password(user["password_hash"], password):
            flash("Invalid email or password.", "error")
            return render_template("login.html")

        session.clear()
        session["user_id"] = user["id"]
        session["user_name"] = user["name"]
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    records, error = query_all(
        """
        SELECT id, module_type, filenames, title, created_at
        FROM analyses
        WHERE user_id = %s
        ORDER BY created_at DESC
        """,
        (current_user_id(),),
    )
    if error:
        flash(error, "error")
        records = []

    financial_count = sum(1 for item in records if item["module_type"] == "financial")
    hospital_count = sum(1 for item in records if item["module_type"] == "hospital")
    
    recent_activity = "No activity yet"
    if records and records[0].get("created_at"):
        dt = records[0]["created_at"]
        if hasattr(dt, "strftime"):
            recent_activity = dt.strftime("%b %d, %Y")
        else:
            recent_activity = str(dt)

    stats = {
        "total": len(records),
        "financial": financial_count,
        "hospital": hospital_count,
        "recent_activity": recent_activity,
    }
    return render_template(
        "dashboard.html",
        records=enrich_records(records[:5]),
        stats=stats,
    )


@app.route("/financial", methods=["GET", "POST"])
@login_required
def financial():
    if request.method == "POST":
        documents = named_files("documents")
        instruction = (request.form.get("instruction") or "").strip()
        if len(instruction) > 2000:
            flash("Please limit the review focus to 2,000 characters.", "error")
            return render_template("financial.html")
        if not documents:
            flash("Please add at least one financial document.", "error")
            return render_template("financial.html")
        if len(documents) > config.MAX_FILES_PER_REVIEW:
            flash(f"Please upload no more than {config.MAX_FILES_PER_REVIEW} files per review.", "error")
            return render_template("financial.html")

        labeled = [("Financial document", item) for item in documents]
        validation_error = validate_labeled_files(labeled)
        if validation_error:
            flash(validation_error, "error")
            return render_template("financial.html")

        original_names = [secure_upload_name(item.filename) for item in documents]
        first_title = original_names[0] if original_names else "Financial document review"
        analysis_id, error = save_analysis(
            "financial",
            ", ".join(original_names),
            "",
            title=first_title,
        )
        if error:
            flash(error, "error")
            return render_template("financial.html")

        saved_items, error = save_labeled_files(labeled, "financial", analysis_id)
        if error:
            rollback_analysis("financial", analysis_id)
            flash(error, "error")
            return render_template("financial.html")

        extracted_documents = [
            extract_document(saved, "Financial document")
            for _label, saved in saved_items
        ]
        disambiguate_source_names(extracted_documents)
        ai_review = None
        ai_message = "AI analysis is not configured; showing extracted document evidence only."
        text_parts = [
            (document["filename"], page["page"], page["text"])
            for document in extracted_documents
            for page in document["pages"]
        ]
        if config.GEMINI_API_KEY and text_parts:
            try:
                ai_review = gemini_financial_analysis(text_parts, instruction)
                ai_message = "Gemini provided an interpretation; source excerpts remain separate below."
            except GeminiAnalysisError as error:
                logger.warning(
                    "Gemini analysis unavailable for analysis %s (%s)",
                    analysis_id,
                    type(error).__name__,
                )
                ai_message = "AI analysis is temporarily unavailable; extracted evidence is shown without an AI interpretation."
        elif config.GEMINI_API_KEY:
            ai_message = "No readable PDF text was available for AI analysis. Images and scanned PDFs are not OCR processed."

        result_data = analyze_financial_documents(extracted_documents, instruction, ai_review)
        result_data["ai_status"] = ai_message
        stored_filenames = " | ".join(
            f"{label}: {saved['original_name']}" for label, saved in saved_items
        )
        error = store_analysis_result(
            "financial", analysis_id, stored_filenames, first_title, result_data
        )
        if error:
            flash(error, "error")
            return render_template("financial.html")
        return redirect(url_for("result", analysis_id=analysis_id))

    return render_template("financial.html")


@app.route("/hospital", methods=["GET", "POST"])
@login_required
def hospital():
    if request.method == "POST":
        bills = named_files("hospital_bill")
        prescriptions = named_files("prescription")
        proofs = named_files("medicine_proof")
        instruction = (request.form.get("instruction") or "").strip()
        if len(instruction) > 2000:
            flash("Please limit the verification focus to 2,000 characters.", "error")
            return render_template("hospital.html")

        medicine_names = request.form.getlist("medicine_name")
        quantities = request.form.getlist("quantity")
        if len(medicine_names) > 50:
            flash("Please enter no more than 50 manual medicine rows.", "error")
            return render_template("hospital.html")
        if len(medicine_names) != len(quantities):
            flash("Medicine names and quantities must be provided in matching rows.", "error")
            return render_template("hospital.html")
        if len(bills) + len(prescriptions) + len(proofs) > config.MAX_FILES_PER_REVIEW:
            flash(f"Please upload no more than {config.MAX_FILES_PER_REVIEW} files per review.", "error")
            return render_template("hospital.html")

        manual_medicines = []
        for name, quantity in zip(medicine_names, quantities):
            name = (name or "").strip()
            quantity = (quantity or "").strip()
            if name or quantity:
                if not name or not quantity:
                    flash(
                        "Each medicine row needs both a name and a quantity.",
                        "error",
                    )
                    return render_template("hospital.html")
                match = QUANTITY_VALUE_PATTERN.fullmatch(quantity)
                quantity_value = float(match.group(1)) if match else 0
                if (
                    not match
                    or not math.isfinite(quantity_value)
                    or quantity_value <= 0
                    or quantity_value > 1_000_000_000
                    or len(name) > 100
                ):
                    flash("Enter a medicine name and a positive numeric quantity (a unit is optional).", "error")
                    return render_template("hospital.html")
                manual_medicines.append({
                    "name": name,
                    "quantity": quantity_value,
                    "unit": (match.group(2) or "unspecified").lower(),
                })

        if not bills and not prescriptions and not proofs and not manual_medicines:
            flash(
                "Add at least one document or enter medicines to start a review.",
                "error",
            )
            return render_template("hospital.html")

        labeled = []
        labeled.extend(("Hospital bill", item) for item in bills)
        labeled.extend(("Prescription", item) for item in prescriptions)
        labeled.extend(("Medicine purchase proof", item) for item in proofs)

        validation_error = validate_labeled_files(labeled)
        if validation_error:
            flash(validation_error, "error")
            return render_template("hospital.html")

        original_names = [
            f"{label}: {secure_upload_name(item.filename)}"
            for label, item in labeled
        ]
        if manual_medicines:
            original_names.extend(
                f"Manual medicine: {item['name']} x {item['quantity']} {item['unit']}"
                for item in manual_medicines
            )
        analysis_id, error = save_analysis(
            "hospital",
            " | ".join(original_names) if original_names else "Manual medicines only",
            "",
            title="Hospital Billing Review",
        )
        if error:
            flash(error, "error")
            return render_template("hospital.html")

        saved_items, error = save_labeled_files(labeled, "hospital", analysis_id)
        if error:
            rollback_analysis("hospital", analysis_id)
            flash(error, "error")
            return render_template("hospital.html")

        extracted_documents = [
            extract_document(saved, label)
            for label, saved in saved_items
        ]
        disambiguate_source_names(extracted_documents)
        result_data = compare_hospital_evidence(extracted_documents, manual_medicines)
        result_data["instruction"] = instruction
        stored_filenames = " | ".join(
            f"{label}: {saved['original_name']}" for label, saved in saved_items
        )
        if manual_medicines:
            manual_names = " | ".join(
                f"Manual medicine: {item['name']} x {item['quantity']} {item['unit']}"
                for item in manual_medicines
            )
            stored_filenames = " | ".join(
                part for part in (stored_filenames, manual_names) if part
            )
        stored_filenames = stored_filenames or "Manual medicines only"
        error = store_analysis_result(
            "hospital",
            analysis_id,
            stored_filenames,
            "Hospital Billing Review",
            result_data,
        )
        if error:
            flash(error, "error")
            return render_template("hospital.html")
        return redirect(url_for("result", analysis_id=analysis_id))

    return render_template("hospital.html")


@app.route("/result/<int:analysis_id>")
@login_required
def result(analysis_id):
    analysis, error = query_one(
        """
        SELECT id, user_id, module_type, filenames, title, result, created_at
        FROM analyses
        WHERE id = %s AND user_id = %s
        """,
        (analysis_id, current_user_id()),
    )
    if error:
        flash(error, "error")
        return redirect(url_for("history"))
    if analysis is None:
        flash("That review was not found.", "error")
        return redirect(url_for("history"))

    dt = analysis.get("created_at")
    formatted_date = dt.strftime("%b %d, %Y · %I:%M %p") if dt and hasattr(dt, "strftime") else str(dt or "")
    parsed_result = parse_analysis_result(analysis["result"])
    display_title = analysis.get("title") or analysis.get("filenames") or "Document Review"

    return render_template(
        "result.html",
        analysis=analysis,
        module_label=MODULE_LABELS.get(analysis["module_type"], analysis["module_type"]),
        status=parsed_result.get("status", "Legacy record"),
        documents=parsed_result.get("documents", []),
        formatted_date=formatted_date,
        parsed=parsed_result,
        display_title=display_title,
    )


@app.route("/analysis/<int:analysis_id>/files/<path:filename>")
@login_required
def analysis_file(analysis_id, filename):
    analysis, error = query_one(
        "SELECT module_type, result FROM analyses WHERE id = %s AND user_id = %s",
        (analysis_id, current_user_id()),
    )
    if error or not analysis or analysis["module_type"] not in MODULE_LABELS:
        return "File not found.", 404
    result_data = parse_analysis_result(analysis.get("result"))
    files = result_data.get("documents", [])
    safe_filename = secure_filename(filename)
    authorized = any(
        document.get("storage_key") == (
            f"{current_user_id()}/{analysis_id}/{safe_filename}"
        )
        for document in files
    )
    if not safe_filename or safe_filename != filename or not authorized:
        return "File not found.", 404

    folder = analysis_directory(
        analysis["module_type"], current_user_id(), analysis_id
    )
    if not (folder / safe_filename).is_file():
        return "File not found.", 404
    return send_from_directory(
        str(folder),
        safe_filename,
        as_attachment=True,
        download_name=safe_filename,
    )


@app.route("/analysis/<int:analysis_id>/edit-title", methods=["POST"])
@login_required
def edit_title(analysis_id):
    new_title = (request.form.get("title") or "").strip()
    if not new_title:
        flash("Review title cannot be empty.", "error")
        return redirect(url_for("history"))

    if len(new_title) > 255:
        flash("Title must be 255 characters or fewer.", "error")
        return redirect(url_for("history"))

    existing, error = query_one(
        "SELECT id FROM analyses WHERE id = %s AND user_id = %s",
        (analysis_id, current_user_id()),
    )
    if error or not existing:
        flash("Review not found or unauthorized.", "error")
        return redirect(url_for("history"))

    _, error = execute_query(
        "UPDATE analyses SET title = %s WHERE id = %s AND user_id = %s",
        (new_title, analysis_id, current_user_id()),
    )
    if error:
        flash(error, "error")
    else:
        flash("Review title updated successfully.", "success")
    return redirect(url_for("history"))


@app.route("/analysis/<int:analysis_id>/delete", methods=["POST"])
@login_required
def delete_analysis(analysis_id):
    record, error = query_one(
        "SELECT id, module_type FROM analyses WHERE id = %s AND user_id = %s",
        (analysis_id, current_user_id()),
    )
    if error or not record:
        flash("Review not found or unauthorized.", "error")
        return redirect(url_for("history"))

    _, error = execute_query(
        "DELETE FROM analyses WHERE id = %s AND user_id = %s",
        (analysis_id, current_user_id()),
    )
    if error:
        flash(error, "error")
    elif not cleanup_analysis_files(
        record["module_type"], current_user_id(), record["id"]
    ):
        logger.error("Upload cleanup failed for deleted analysis %s", analysis_id)
        flash("Review deleted, but some stored files could not be removed.", "error")
    else:
        flash("Review and associated uploaded files were deleted.", "success")
    return redirect(url_for("history"))


@app.route("/history")
@login_required
def history():
    records, error = query_all(
        """
        SELECT id, module_type, filenames, title, created_at
        FROM analyses
        WHERE user_id = %s
        ORDER BY created_at DESC
        """,
        (current_user_id(),),
    )
    if error:
        flash(error, "error")
        records = []

    return render_template("history.html", records=enrich_records(records))


@app.route("/history/delete-all", methods=["POST"])
@login_required
def delete_all_history():
    records, error = query_all(
        "SELECT id, module_type FROM analyses WHERE user_id = %s",
        (current_user_id(),),
    )
    if error:
        flash(error, "error")
        return redirect(url_for("history"))

    _, error = execute_query(
        "DELETE FROM analyses WHERE user_id = %s",
        (current_user_id(),),
    )
    if error:
        flash(error, "error")
    else:
        cleanup_failures = [
            record["id"] for record in records or []
            if not cleanup_analysis_files(
                record["module_type"], current_user_id(), record["id"]
            )
        ]
        if cleanup_failures:
            logger.error("Upload cleanup failed for deleted analyses: %s", cleanup_failures)
            flash("Review history was deleted, but some stored files could not be removed.", "error")
        else:
            flash("All review history and associated files have been cleared.", "success")
    return redirect(url_for("history"))


@app.route("/settings")
@login_required
def settings():
    user, error = query_one(
        "SELECT name, email, created_at FROM users WHERE id = %s",
        (current_user_id(),),
    )
    if error:
        flash(error, "error")
        user = {"name": current_user_name(), "email": "", "created_at": None}

    formatted_date = "—"
    if user and user.get("created_at") and hasattr(user["created_at"], "strftime"):
        formatted_date = user["created_at"].strftime("%B %d, %Y")
    elif user and user.get("created_at"):
        formatted_date = str(user["created_at"])

    return render_template(
        "settings.html",
        user=user,
        formatted_date=formatted_date,
        database_status="Available" if not error else "Unavailable",
        max_upload_size_mb=config.MAX_UPLOAD_SIZE_MB,
        max_request_size_mb=config.MAX_REQUEST_SIZE_MB,
        max_files_per_review=config.MAX_FILES_PER_REVIEW,
        gemini_configured=bool(config.GEMINI_API_KEY),
    )


@app.route("/settings/change-password", methods=["POST"])
@login_required
def change_password():
    current_password = request.form.get("current_password") or ""
    new_password = request.form.get("new_password") or ""
    confirm_password = request.form.get("confirm_password") or ""

    if not current_password or not new_password or not confirm_password:
        flash("All password fields are required.", "error")
        return redirect(url_for("settings"))

    if len(new_password) < 8 or len(new_password) > 128:
        flash("New password must be between 8 and 128 characters.", "error")
        return redirect(url_for("settings"))

    if new_password != confirm_password:
        flash("New password and confirmation do not match.", "error")
        return redirect(url_for("settings"))

    user, error = query_one(
        "SELECT password_hash FROM users WHERE id = %s",
        (current_user_id(),),
    )
    if error or not user:
        flash("User record could not be verified.", "error")
        return redirect(url_for("settings"))

    if not verify_password(user["password_hash"], current_password):
        flash("Current password is incorrect.", "error")
        return redirect(url_for("settings"))

    _, error = execute_query(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (hash_password(new_password), current_user_id()),
    )
    if error:
        flash(error, "error")
    else:
        flash("Your password has been changed successfully.", "success")
    return redirect(url_for("settings"))


if __name__ == "__main__":
    app.run(debug=False)
