import os
import shutil
import uuid
import warnings
from pathlib import Path

import config
from PIL import Image, UnidentifiedImageError
from PyPDF2 import PdfReader
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}
IMAGE_FORMATS = {
    "jpg": {"JPEG"},
    "jpeg": {"JPEG"},
    "png": {"PNG"},
}

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_ROOT = BASE_DIR / "uploads"
MODULE_FOLDERS = {
    "financial": UPLOAD_ROOT / "financial",
    "hospital": UPLOAD_ROOT / "hospital",
}


def allowed_extension(filename):
    if not filename or "." not in filename:
        return False
    return filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_extension(filename):
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1].lower()


def validate_upload(file_storage):
    """Validate the submitted filename, size, and basic file signature."""
    if file_storage is None or not file_storage.filename:
        return "Please choose a file."

    original_name = file_storage.filename
    safe_name = secure_filename(original_name)
    if not safe_name or safe_name in {".", ".."}:
        return "The filename is not valid."

    extension = get_extension(safe_name)
    if extension not in ALLOWED_EXTENSIONS:
        return "Only PDF, JPG, JPEG, and PNG files are allowed."

    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)

    if size == 0:
        return "The selected file is empty."
    if size > config.MAX_UPLOAD_SIZE_BYTES:
        return f"Each file must be no larger than {config.MAX_UPLOAD_SIZE_MB} MB."

    if extension == "pdf":
        if file_storage.read(5) != b"%PDF-":
            file_storage.seek(0)
            return "This file does not appear to be a valid PDF."
        file_storage.seek(0)
        try:
            reader = PdfReader(file_storage.stream, strict=True)
            if reader.is_encrypted or not reader.pages:
                file_storage.seek(0)
                return "This PDF is empty or password-protected and cannot be processed."
        except Exception:
            file_storage.seek(0)
            return "This file does not appear to be a complete, readable PDF."
        file_storage.seek(0)
    else:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(file_storage.stream) as image:
                    if image.format not in IMAGE_FORMATS[extension]:
                        file_storage.seek(0)
                        return "The image content does not match its file extension."
                    image.verify()
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ):
            file_storage.seek(0)
            return "This image is invalid or exceeds safe dimension limits."
        finally:
            file_storage.seek(0)

    return None


def make_unique_filename(original_filename):
    """Create a safe unique storage name without retaining user path components."""
    safe_name = secure_filename(original_filename)
    if not safe_name:
        safe_name = "upload"
    name, extension = os.path.splitext(safe_name)
    return f"{name}_{uuid.uuid4().hex}{extension.lower()}"


def analysis_directory(module_type, user_id, analysis_id):
    folder = MODULE_FOLDERS.get(module_type)
    if folder is None or not str(user_id).isdigit() or not str(analysis_id).isdigit():
        raise ValueError("Unknown upload module or invalid analysis owner.")
    target = (folder / str(user_id) / str(analysis_id)).resolve()
    if os.path.commonpath((str(folder.resolve()), str(target))) != str(folder.resolve()):
        raise ValueError("Upload directory is outside its module folder.")
    return target


def save_upload(file_storage, module_type, user_id, analysis_id):
    """Save a validated file under its owning user's analysis directory."""
    folder = analysis_directory(module_type, user_id, analysis_id)
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = make_unique_filename(file_storage.filename)
    saved_path = folder / stored_name
    file_storage.save(saved_path)
    return {
        "original_name": secure_filename(file_storage.filename),
        "stored_name": stored_name,
        "storage_key": f"{user_id}/{analysis_id}/{stored_name}",
        "saved_path": str(saved_path),
        "extension": get_extension(file_storage.filename),
    }


def cleanup_analysis_files(module_type, user_id, analysis_id):
    """Remove only a single analysis directory after verifying its owner path."""
    if module_type not in MODULE_FOLDERS:
        return False
    folder = analysis_directory(module_type, user_id, analysis_id)
    if not folder.exists():
        return True
    try:
        shutil.rmtree(folder)
        parent = folder.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
        return True
    except OSError:
        return False
