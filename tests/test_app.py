import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import session
from PyPDF2 import PdfWriter
from werkzeug.datastructures import FileStorage
from werkzeug.security import generate_password_hash
from PIL import Image

import app as web
from utils import analysis as analysis_utils
from utils import files as file_utils
from utils import gemini
from utils.pdf import extract_pdf_pages


def pdf_upload(name="sample.pdf"):
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(output)
    output.seek(0)
    return (output, name)


class FinPrintTests(unittest.TestCase):
    def setUp(self):
        self.client = web.app.test_client()
        self.original_testing = web.app.config["TESTING"]
        web.app.config["TESTING"] = True

    def tearDown(self):
        web.app.config["TESTING"] = self.original_testing

    def csrf(self, path="/login"):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as current_session:
            return current_session["_csrf_token"]

    def authenticate(self):
        with self.client.session_transaction() as current_session:
            current_session["user_id"] = 7
            current_session["user_name"] = "Test User"
            current_session["_csrf_token"] = "test-token"
        return "test-token"

    def test_public_pages_and_static_files(self):
        for path in (
            "/",
            "/login",
            "/register",
            "/static/css/style.css",
            "/static/js/main.js",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                response.close()

    def test_dashboard_copy_and_review_titles_render_cleanly(self):
        self.authenticate()
        records = [
            {
                "id": 12,
                "module_type": "hospital",
                "filenames": "Hospital bill: invoice.pdf | Prescription: private-details.pdf",
                "title": "Manual medicines: Paracetamol 650 mg",
                "created_at": None,
            },
            {
                "id": 13,
                "module_type": "financial",
                "filenames": "investment.pdf, supporting-document.pdf",
                "title": "investment.pdf",
                "created_at": None,
            },
        ]
        with patch.object(web, "query_all", return_value=(records, None)):
            response = self.client.get("/dashboard")
        rendered = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("View All Reviews", rendered)
        self.assertIn("Hospital bill", rendered)
        self.assertNotIn("Manual medicines: Paracetamol", rendered)
        self.assertIn("Analyze Document", rendered)
        self.assertIn("Verify Bill", rendered)
        self.assertIn("Financial Reviews", rendered)
        self.assertIn("Hospital Reviews", rendered)
        self.assertNotIn("private-details.pdf", rendered)
        self.assertNotIn("Workspace", rendered)
        self.assertNotIn("Member", rendered)
        self.assertNotIn("Team", rendered)
        self.assertNotIn("reviewssvg", rendered.lower())
        self.assertNotIn("svgMutual", rendered)
        self.assertNotIn("**", rendered)

    def test_dashboard_empty_state_uses_current_copy(self):
        self.authenticate()
        with patch.object(web, "query_all", return_value=([], None)):
            response = self.client.get("/dashboard")
        rendered = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Start by analyzing a financial document or reviewing a hospital bill.",
            rendered,
        )
        self.assertNotIn(
            "Start by analyzing a financial disclosure or reviewing hospital billing evidence.",
            rendered,
        )

    def test_anonymous_access_is_redirected(self):
        for path in (
            "/dashboard",
            "/financial",
            "/hospital",
            "/result/1",
            "/history",
            "/settings",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 302)

    def test_csrf_required_for_state_changing_routes(self):
        for path in (
            "/register",
            "/login",
            "/logout",
            "/financial",
            "/hospital",
            "/settings/change-password",
            "/analysis/1/edit-title",
            "/analysis/1/delete",
            "/history/delete-all",
        ):
            with self.subTest(path=path):
                response = self.client.post(path)
                self.assertEqual(response.status_code, 400)

    def test_registration_and_login_with_mocked_database(self):
        token = self.csrf("/register")
        with patch.object(web, "query_one", return_value=(None, None)), patch.object(
            web, "execute_query", return_value=(1, None)
        ):
            response = self.client.post(
                "/register",
                data={
                    "_csrf_token": token,
                    "name": "Test User",
                    "email": "test@example.invalid",
                    "password": "long-password",
                    "confirm_password": "long-password",
                },
            )
            self.assertEqual(response.status_code, 302)

        token = self.csrf("/login")
        user = {
            "id": 7,
            "name": "Test User",
            "email": "test@example.invalid",
            "password_hash": generate_password_hash("long-password"),
        }
        with patch.object(web, "query_one", return_value=(user, None)):
            response = self.client.post(
                "/login",
                data={
                    "_csrf_token": token,
                    "email": user["email"],
                    "password": "long-password",
                },
            )
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as current_session:
            self.assertEqual(current_session["user_id"], 7)

    def test_logout_is_post_and_clears_session(self):
        token = self.authenticate()
        self.assertEqual(self.client.get("/logout").status_code, 405)
        response = self.client.post("/logout", data={"_csrf_token": token})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as current_session:
            self.assertNotIn("user_id", current_session)

    def test_password_hashing_and_password_change(self):
        password_hash = web.hash_password("long-password")
        self.assertNotEqual(password_hash, "long-password")
        self.assertTrue(web.verify_password(password_hash, "long-password"))
        token = self.authenticate()
        user = {"password_hash": generate_password_hash("old-password")}
        with patch.object(web, "query_one", return_value=(user, None)), patch.object(
            web, "execute_query", return_value=(1, None)
        ) as execute:
            response = self.client.post(
                "/settings/change-password",
                data={
                    "_csrf_token": token,
                    "current_password": "old-password",
                    "new_password": "new-password",
                    "confirm_password": "new-password",
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(execute.called)
        self.assertNotIn("new-password", repr(execute.call_args))

    def test_secret_configuration_rejects_weak_values(self):
        self.assertFalse(web.config.has_strong_secret(""))
        self.assertFalse(web.config.has_strong_secret("a" * 64))
        self.assertFalse(web.config.has_strong_secret("replace-with-a-random-value-of-at-least-32-characters"))
        self.assertTrue(web.config.has_strong_secret("Aa9!randomized-test-key-value-0123456789"))

    def test_duplicate_source_filenames_are_disambiguated(self):
        documents = [
            {"filename": "bill.pdf"},
            {"filename": "bill.pdf"},
            {"filename": "bill (2).pdf"},
        ]
        web.disambiguate_source_names(documents)
        self.assertEqual(
            [document["filename"] for document in documents],
            ["bill.pdf", "bill (2).pdf", "bill (2) (2).pdf"],
        )

    def test_database_errors_are_not_returned_to_callers(self):
        cursor = Mock()
        cursor.execute.side_effect = RuntimeError("private-database-detail")
        connection = Mock()
        connection.cursor.return_value = cursor
        with patch.object(web, "get_connection", return_value=connection), patch.object(
            web, "close_connection"
        ), self.assertLogs(web.logger, level="ERROR"):
            row, error = web.query_one("SELECT id FROM users WHERE id = %s", (1,))
        self.assertIsNone(row)
        self.assertNotIn("private-database-detail", error)

    def test_upload_validation_pdf_image_size_and_extension(self):
        valid_pdf, pdf_name = pdf_upload()
        self.assertIsNone(
            file_utils.validate_upload(FileStorage(valid_pdf, filename=pdf_name))
        )
        self.assertIsNotNone(
            file_utils.validate_upload(FileStorage(io.BytesIO(b"fake"), filename="fake.pdf"))
        )
        self.assertIsNotNone(
            file_utils.validate_upload(FileStorage(io.BytesIO(b""), filename="empty.pdf"))
        )
        self.assertIsNotNone(
            file_utils.validate_upload(FileStorage(io.BytesIO(b"x"), filename="bad.exe"))
        )

        image_stream = io.BytesIO()
        Image.new("RGB", (1, 1)).save(image_stream, format="PNG")
        image_stream.seek(0)
        self.assertIsNone(
            file_utils.validate_upload(FileStorage(image_stream, filename="small.png"))
        )
        self.assertIsNotNone(
            file_utils.validate_upload(FileStorage(io.BytesIO(b"not an image"), filename="fake.png"))
        )
        with patch.object(
            file_utils.Image,
            "open",
            side_effect=file_utils.Image.DecompressionBombError("unsafe dimensions"),
        ):
            error = file_utils.validate_upload(
                FileStorage(io.BytesIO(b"image bytes"), filename="large.png")
            )
        self.assertIn("safe dimension limits", error)
        with patch.object(file_utils.config, "MAX_UPLOAD_SIZE_BYTES", 4):
            self.assertIsNotNone(
                file_utils.validate_upload(
                    FileStorage(io.BytesIO(b"12345"), filename="large.jpg")
                )
            )

    def test_partial_upload_cleanup(self):
        valid_one, name_one = pdf_upload("one.pdf")
        valid_two, name_two = pdf_upload("two.pdf")
        saved = {
            "original_name": name_one,
            "stored_name": "one_unique.pdf",
            "storage_key": "7/31/one_unique.pdf",
            "saved_path": "not-written",
            "extension": "pdf",
        }
        with web.app.test_request_context("/financial", method="POST"):
            session["user_id"] = 7
            with patch.object(
                web,
                "save_upload",
                side_effect=[saved, OSError("private technical detail")],
            ), patch.object(web, "cleanup_analysis_files", return_value=True) as cleanup:
                with self.assertLogs(web.logger, level="ERROR"):
                    result, error = web.save_labeled_files(
                        [
                            ("Document", FileStorage(valid_one, filename=name_one)),
                            ("Document", FileStorage(valid_two, filename=name_two)),
                        ],
                        "financial",
                        31,
                    )
        self.assertIsNone(result)
        self.assertIn("could not be stored", error)
        cleanup.assert_called_once_with("financial", 7, 31)

    def test_analysis_storage_is_isolated_by_owner_and_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module = root / "financial"
            owned = module / "7" / "31"
            other = module / "8" / "31"
            owned.mkdir(parents=True)
            other.mkdir(parents=True)
            (owned / "owned.pdf").write_bytes(b"owned")
            (other / "other.pdf").write_bytes(b"other")
            with patch.dict(file_utils.MODULE_FOLDERS, {"financial": module}):
                self.assertTrue(file_utils.cleanup_analysis_files("financial", 7, 31))
            self.assertFalse(owned.exists())
            self.assertTrue((other / "other.pdf").exists())

    def test_pdf_extraction_preserves_page_numbers(self):
        fake_reader = Mock()
        fake_reader.pages = [
            Mock(extract_text=Mock(return_value="first page")),
            Mock(extract_text=Mock(return_value="second page")),
        ]
        with patch("utils.pdf.PdfReader", return_value=fake_reader):
            pages, warning = extract_pdf_pages("not-read")
        self.assertIsNone(warning)
        self.assertEqual(pages, [
            {"page": 1, "text": "first page"},
            {"page": 2, "text": "second page"},
        ])

    def test_financial_excerpts_are_source_backed_and_gemini_is_optional(self):
        documents = [{
            "filename": "policy.pdf",
            "type": "Financial document",
            "pages": [{"page": 4, "text": "An exit load fee of 1% may apply."}],
            "text_available": True,
            "message": "Text extracted.",
            "storage_key": "7/31/policy.pdf",
        }]
        result = analysis_utils.analyze_financial_documents(documents)
        self.assertEqual(result["key_findings"][0]["page"], 4)
        self.assertEqual(result["key_findings"][0]["source_document"], "policy.pdf")
        self.assertIsNone(result["ai_interpretation"])
        self.assertEqual(result["status"], "Text extracted only")
        self.assertEqual(
            result["document_checks"][0]["status"],
            "FACT FOUND IN DOCUMENT",
        )
        self.assertTrue(
            any(
                item["status"] == "NOT FOUND IN EXTRACTED TEXT"
                for item in result["document_checks"]
            )
        )

    def test_gemini_missing_key_malformed_response_and_api_failure(self):
        with patch.object(gemini.config, "GEMINI_API_KEY", ""):
            with self.assertRaises(gemini.GeminiUnavailable):
                gemini.analyze_financial_documents([("doc.pdf", 1, "fee")])

        fake_model = Mock()
        fake_model.models.generate_content.return_value.text = "not json"
        with patch.object(gemini, "_initialize_client", return_value=fake_model):
            with self.assertRaises(gemini.GeminiAnalysisError):
                gemini.analyze_financial_documents([("doc.pdf", 1, "fee")])

        fake_model.models.generate_content.return_value.text = json.dumps({
            "summary": "A fee is stated.",
            "document_type": "Policy",
            "product_or_fund_name": "Not found",
            "investment_amount": "Not found",
            "key_findings": [],
            "fees_and_charges": [],
            "important_conditions": [],
            "risks": [],
            "positive_points": [],
            "items_requiring_review": [],
            "questions_to_check": [],
            "evidence_references": [
                {"source_document": "doc.pdf", "page": 1, "quote": "Fee of 1% applies"},
                {"source_document": "other.pdf", "page": 9, "quote": "fabricated"},
            ],
        })
        with patch.object(gemini, "_initialize_client", return_value=fake_model):
            response = gemini.analyze_financial_documents([
                ("doc.pdf", 1, "The Fee of 1% applies today."),
            ])
        self.assertEqual(len(response["evidence_references"]), 1)
        self.assertEqual(response["evidence_references"][0]["page"], 1)

        fake_model.models.generate_content.side_effect = RuntimeError("private API error")
        with patch.object(gemini, "_initialize_client", return_value=fake_model):
            with self.assertLogs(gemini.logger, level="ERROR"):
                with self.assertRaisesRegex(gemini.GeminiAnalysisError, "temporarily unavailable"):
                    gemini.analyze_financial_documents([("doc.pdf", 1, "fee")])

    def test_hospital_comparison_calculates_quantity_difference_with_pages(self):
        documents = [
            {
                "filename": "bill.pdf",
                "type": "Hospital bill",
                "text_available": True,
                "pages": [{"page": 1, "text": "Medicine A quantity 10 tablets"}],
            },
            {
                "filename": "prescription.pdf",
                "type": "Prescription",
                "text_available": True,
                "pages": [{"page": 2, "text": "Medicine A quantity 6 tablets"}],
            },
        ]
        result = analysis_utils.compare_hospital_evidence(documents)
        mismatch = next(item for item in result["findings"] if item["type"] == "Quantity mismatch")
        self.assertEqual(mismatch["difference"], 4)
        self.assertEqual(mismatch["source_pages"], [1, 2])
        self.assertNotIn("FRAUD", mismatch["status"].upper())
        self.assertEqual(
            [(item["source_document"], item["page"]) for item in mismatch["evidence"]],
            [("bill.pdf", 1), ("prescription.pdf", 2)],
        )

    def test_hospital_comparison_agreement_and_missing_evidence_cases(self):
        bill = {
            "filename": "bill.pdf",
            "type": "Hospital bill",
            "text_available": True,
            "pages": [{"page": 1, "text": "Paracetamol | quantity 10 tablets"}],
        }
        prescription = {
            "filename": "prescription.pdf",
            "type": "Prescription",
            "text_available": True,
            "pages": [{"page": 2, "text": "Paracetamol | quantity 10 tabs"}],
        }
        agreement = analysis_utils.compare_hospital_evidence([bill, prescription])
        consistent = next(
            item for item in agreement["findings"]
            if item["type"] == "Quantity comparison"
        )
        self.assertEqual(consistent["status"], "Consistent with supporting evidence")
        self.assertEqual(consistent["difference"], 0)

        missing_prescription_item = {
            **prescription,
            "pages": [{"page": 2, "text": "A different medicine quantity 2 tablets"}],
        }
        bill_without_support = analysis_utils.compare_hospital_evidence(
            [bill, missing_prescription_item]
        )
        missing = next(
            item for item in bill_without_support["findings"]
            if item["item"] == "Paracetamol"
        )
        self.assertEqual(missing["status"], "Supporting evidence not found")
        self.assertEqual(missing["source_documents"], ["bill.pdf"])
        self.assertEqual(missing["evidence"][0]["page"], 1)

        prescription_without_bill = analysis_utils.compare_hospital_evidence(
            [prescription]
        )
        not_billed = next(
            item for item in prescription_without_bill["findings"]
            if item["type"] == "Prescription medicine not found in bill"
        )
        self.assertEqual(not_billed["bill_value"], "Not found in extracted bill lines")
        self.assertEqual(not_billed["source_documents"], ["prescription.pdf"])

    def test_hospital_comparison_flags_duplicate_looking_bill_lines(self):
        bill = {
            "filename": "bill.pdf",
            "type": "Hospital bill",
            "text_available": True,
            "pages": [{
                "page": 1,
                "text": (
                    "Paracetamol quantity 5 tablets\n"
                    "paracetamol quantity 5 tablets"
                ),
            }],
        }
        result = analysis_utils.compare_hospital_evidence([bill])
        duplicate = next(
            item for item in result["findings"]
            if item["type"] == "Potential duplicate billing item"
        )
        self.assertEqual(duplicate["status"], "Needs review")
        self.assertEqual(duplicate["bill_value"], "2 similar bill lines")
        self.assertEqual(len(duplicate["evidence"]), 2)

    def test_hospital_manual_route_stores_deterministic_result(self):
        token = self.authenticate()
        with patch.object(web, "save_analysis", return_value=(44, None)), patch.object(
            web, "execute_query", return_value=(1, None)
        ) as execute:
            response = self.client.post(
                "/hospital",
                data={
                    "_csrf_token": token,
                    "medicine_name": ["Medicine A"],
                    "quantity": ["6 tablets"],
                },
            )
        self.assertEqual(response.status_code, 302)
        result = json.loads(execute.call_args.args[1][2])
        self.assertEqual(result["extracted_items"][0]["name"], "Medicine A")
        self.assertEqual(result["extracted_items"][0]["quantity"], 6)

    def test_financial_route_rejects_fake_pdf_before_creating_record(self):
        token = self.authenticate()
        with patch.object(web, "save_analysis") as save_analysis:
            response = self.client.post(
                "/financial",
                data={
                    "_csrf_token": token,
                    "documents": (io.BytesIO(b"not a pdf"), "fake.pdf"),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        save_analysis.assert_not_called()

    def test_result_storage_stays_within_existing_mysql_text_limit(self):
        with web.app.test_request_context("/"):
            session["user_id"] = 7
            with patch.object(web, "execute_query", return_value=(1, None)) as execute:
                error = web.store_analysis_result(
                    "financial",
                    31,
                    "review.pdf",
                    "Review",
                    {"summary": "evidence " * 20_000, "limitations": []},
                )
        self.assertIsNone(error)
        payload = execute.call_args.args[1][2]
        self.assertLessEqual(len(payload.encode("utf-8")), 60_000)
        self.assertIn("shortened", json.loads(payload)["limitations"][0])

    def test_result_write_failure_cleans_new_upload_directory(self):
        with web.app.test_request_context("/"):
            session["user_id"] = 7
            with patch.object(
                web,
                "execute_query",
                side_effect=[(None, "safe database error"), (None, None)],
            ), patch.object(web, "cleanup_analysis_files", return_value=True) as cleanup:
                error = web.store_analysis_result(
                    "financial", 31, "policy.pdf", "Review", {"summary": "Extracted"}
                )
        self.assertEqual(error, "safe database error")
        cleanup.assert_called_once_with("financial", 7, 31)

    def test_route_rendering_and_result_ownership(self):
        token = self.authenticate()
        user = {"name": "Test User", "email": "test@example.invalid", "created_at": None}
        record = {
            "id": 31,
            "user_id": 7,
            "module_type": "hospital",
            "filenames": "bill.pdf",
            "title": "Review",
            "result": json.dumps({
                "module_type": "hospital",
                "status": "Needs review",
                "summary": "One source excerpt.",
                "findings": [],
                "documents": [],
                "limitations": [],
            }),
            "created_at": None,
        }
        with patch.object(web, "query_all", return_value=([], None)), patch.object(
            web, "query_one", return_value=(user, None)
        ):
            for path in ("/dashboard", "/financial", "/hospital", "/history", "/settings"):
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200)
                    response.close()

        with patch.object(web, "query_one", return_value=(record, None)) as query:
            response = self.client.get("/result/31")
        self.assertEqual(response.status_code, 200)
        self.assertIn("One source excerpt.", response.get_data(as_text=True))
        self.assertIn("user_id = %s", query.call_args.args[0])
        self.assertEqual(query.call_args.args[1][-1], 7)

    def test_history_crud_controls_and_authenticated_file_access(self):
        self.authenticate()
        row = {
            "id": 31,
            "module_type": "financial",
            "filenames": "policy.pdf",
            "title": "Policy review",
            "created_at": None,
        }
        with patch.object(web, "query_all", return_value=([row], None)):
            response = self.client.get("/history")
        body = response.get_data(as_text=True)
        self.assertIn("data-edit-title", body)
        self.assertIn("data-delete-analysis", body)
        self.assertIn("data-delete-all", body)

        file_record = {
            "module_type": "financial",
            "result": json.dumps({
                "documents": [{
                    "storage_key": "7/31/policy_unique.pdf",
                }],
            }),
        }
        with patch.object(web, "query_one", return_value=(file_record, None)), patch.object(
            web, "send_from_directory", return_value=web.app.response_class(b"file")
        ) as send:
            with patch.object(
                web,
                "analysis_directory",
                return_value=Path("uploads") / "financial" / "7" / "31",
            ):
                with patch.object(Path, "is_file", return_value=True):
                    response = self.client.get("/analysis/31/files/policy_unique.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(str(send.call_args.args[0]).endswith(str(Path("financial") / "7" / "31")))

        with patch.object(web, "query_one", return_value=(None, None)):
            response = self.client.get("/analysis/32/files/policy_unique.pdf")
        self.assertEqual(response.status_code, 404)

    def test_history_crud_and_delete_all_keep_owner_predicates(self):
        token = self.authenticate()
        with patch.object(web, "query_one", return_value=({"id": 31}, None)), patch.object(
            web, "execute_query", return_value=(1, None)
        ) as execute:
            response = self.client.post(
                "/analysis/31/edit-title",
                data={"_csrf_token": token, "title": "New title"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("user_id = %s", execute.call_args.args[0])

        record = {"id": 31, "module_type": "financial"}
        with patch.object(web, "query_one", return_value=(record, None)), patch.object(
            web, "execute_query", return_value=(1, None)
        ) as execute, patch.object(web, "cleanup_analysis_files", return_value=True):
            response = self.client.post(
                "/analysis/31/delete", data={"_csrf_token": token}
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("user_id = %s", execute.call_args.args[0])

        with patch.object(web, "query_all", return_value=([record], None)), patch.object(
            web, "execute_query", return_value=(1, None)
        ) as execute, patch.object(web, "cleanup_analysis_files", return_value=True):
            response = self.client.post(
                "/history/delete-all", data={"_csrf_token": token}
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("user_id = %s", execute.call_args.args[0])

    def test_financial_route_uses_mocked_gemini_and_saves_structured_result(self):
        token = self.authenticate()
        mock_saved = {
            "original_name": "sample.pdf",
            "stored_name": "sample_unique.pdf",
            "storage_key": "7/31/sample_unique.pdf",
            "saved_path": "not-written",
            "extension": "pdf",
        }
        extracted = {
            "filename": "sample.pdf",
            "type": "Financial document",
            "pages": [{"page": 3, "text": "Expense ratio 1%."}],
            "text_available": True,
            "message": "Text extracted.",
            "storage_key": mock_saved["storage_key"],
        }
        ai_result = {
            "summary": "Mocked interpretation.",
            "key_findings": [],
            "fees_and_charges": [],
            "important_conditions": [],
            "risks": [],
            "positive_points": [],
            "items_requiring_review": [],
            "questions_to_check": [],
        }
        with patch.object(web, "save_analysis", return_value=(31, None)), patch.object(
            web, "save_upload", return_value=mock_saved
        ), patch.object(web, "extract_document", return_value=extracted), patch.object(
            web.config, "GEMINI_API_KEY", "mock-configured"
        ), patch.object(web, "gemini_financial_analysis", return_value=ai_result) as mocked_ai, patch.object(
            web, "execute_query", return_value=(1, None)
        ) as execute:
            stream, filename = pdf_upload()
            response = self.client.post(
                "/financial",
                data={
                    "_csrf_token": token,
                    "documents": (stream, filename),
                    "instruction": "Check charges",
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 302)
        mocked_ai.assert_called_once()
        saved_json = execute.call_args.args[1][2]
        self.assertEqual(json.loads(saved_json)["ai_interpretation"]["summary"], "Mocked interpretation.")


if __name__ == "__main__":
    unittest.main()
