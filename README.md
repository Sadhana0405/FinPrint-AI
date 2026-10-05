# FinPrint AI

FinPrint AI is a Flask and MySQL document-review demo for financial PDFs and hospital billing evidence. It stores each review under its owner, extracts readable PDF text with PyPDF2, highlights selected source excerpts, and performs conservative quantity comparisons where line items can be parsed.

It does not determine fraud, medical necessity, investment suitability, or legal liability. Automated excerpts and comparisons are heuristic and must be checked against the original documents.

## Features and current limits

- Registration, login, password hashing, signed sessions, CSRF checks, and per-user analysis ownership.
- Financial PDF text extraction with source page references, keyword-based excerpts, and explicit found/not-found checks for fees, lock-in/withdrawal terms, risks, and conditions. These checks are limited to extracted text and are not exhaustive.
- Optional Gemini financial interpretation when `GEMINI_API_KEY` is configured. Gemini output is separated from extracted source evidence.
- Hospital evidence intake for bills, prescriptions, purchase proofs, and manual medicine quantities.
- Deterministic comparison of clearly formatted medicine name/quantity lines. Potential duplicate bill lines and missing support are labelled for review, not fraud.
- Per-analysis upload directories, type checks, per-file and per-request size limits, history actions, and password changes.

Images and scanned PDFs are not OCR processed. Image files are content-validated and retained as evidence only. Financial term matching and hospital line-item parsing are not comprehensive; unsupported layouts may yield no findings. AI interpretation may be inaccurate.

## Architecture

- `app.py`: Flask routes, authentication requirements, CSRF checks, safe database helpers, analysis storage.
- `config.py`: environment and upload/session settings.
- `utils/auth.py`: password hashing and login guard.
- `utils/db.py`: MySQL connections.
- `utils/files.py`: validated uploads and analysis-scoped cleanup.
- `utils/pdf.py`: page-aware PyPDF2 extraction.
- `utils/analysis.py`: source excerpting and deterministic hospital comparisons.
- `utils/gemini.py`: optional Gemini integration for financial interpretations.
- `database/schema.sql`: initial MySQL schema.
- `templates/`: Jinja pages.
- `static/css/style.css`, `static/js/main.js`: responsive UI and browser interactions.
- `uploads/financial/<user_id>/<analysis_id>/` and `uploads/hospital/<user_id>/<analysis_id>/`: private-by-route local storage paths; the server does not provide direct upload URLs.

## Setup

Use Python 3.9 or later and a running MySQL server. Install the dependencies in `requirements.txt` into a virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`. Replace the Flask key placeholder with a cryptographically random value of at least 32 characters. Do not commit `.env`.

Required configuration:

| Variable | Purpose |
| --- | --- |
| `FLASK_SECRET_KEY` | Required random session-signing key, at least 32 characters. App startup fails if missing or weak. |
| `MYSQL_HOST` | MySQL server host (default `localhost`). |
| `MYSQL_PORT` | MySQL server port (default `3306`). |
| `MYSQL_USER` | MySQL account name. |
| `MYSQL_PASSWORD` | MySQL account password. |
| `MYSQL_DATABASE` | Database name (default `finprint_ai`). |
| `MAX_UPLOAD_SIZE_MB` | Maximum size of each upload (default `10`). |
| `MAX_REQUEST_SIZE_MB` | Maximum complete HTTP request size including all files (default `50`; must be at least the per-file size). |
| `MAX_FILES_PER_REVIEW` | Maximum total uploaded files in one review (default `20`). |
| `SESSION_COOKIE_SECURE` | Set to `true` when serving over HTTPS. Keep `false` for local HTTP development. |
| `GEMINI_API_KEY` | Optional. Without it, PDF extraction and deterministic checks still work, but AI interpretation is unavailable. |

Create the database schema using a MySQL client:

```powershell
mysql -u <user> -p < database/schema.sql
```

Then start the local application:

```powershell
python app.py
```

The built-in development server runs with debug mode disabled. Use a production WSGI server and HTTPS for deployment.

## Evidence comparison

Hospital comparison is deterministic, not a model-generated verdict. It compares quantities only when the parser recognizes a medicine name and a numeric quantity/unit on a readable PDF line or in a manual entry. A difference is displayed with the extracted values and source pages. Similar repeated bill lines are marked “Potential duplicate” and need human review. Missing parseable support is labelled as insufficient evidence; it is not proof that a charge is incorrect.

Financial excerpts are selected using keyword matches and include a short quote, source filename, and PDF page. Optional Gemini output is shown separately as interpretation, not as source evidence. No live API call is made during local tests.

## Security notes

- Keep `.env` and the contents of `uploads/` out of version control.
- Choose a unique, strong `FLASK_SECRET_KEY`; replacing it invalidates existing signed sessions.
- State-changing requests, including logout, require the CSRF token rendered into forms.
- Database queries use bound parameters; analysis reads, edits, and deletes are constrained by the session user ID.
- Uploaded names are sanitized, file contents are checked against supported formats, and saved files are isolated by user and analysis ID.
- Configure `SESSION_COOKIE_SECURE=true` behind HTTPS. Do not expose development/debug services.
- Uploaded documents may contain sensitive personal information. Restrict access to the machine and backups that store the project.

## Testing

Run the local test suite without contacting Gemini or modifying existing database records:

```powershell
python -m unittest discover -s tests -v
```
