import os

from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "finprint_ai")

MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "10"))
MAX_REQUEST_SIZE_MB = int(os.getenv("MAX_REQUEST_SIZE_MB", "50"))
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
MAX_CONTENT_LENGTH = MAX_REQUEST_SIZE_MB * 1024 * 1024
MAX_FILES_PER_REVIEW = int(os.getenv("MAX_FILES_PER_REVIEW", "20"))
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"


def has_strong_secret(value):
    if len(value) < 32 or len(set(value)) < 8:
        return False
    lowered = value.lower()
    return not any(
        marker in lowered
        for marker in ("change-this", "replace-with", "your-secret", "secret-key")
    )
