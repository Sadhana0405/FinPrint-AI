from PyPDF2 import PdfReader

MAX_EXTRACTED_CHARS = 100_000


def extract_pdf_pages(filepath):
    """Extract readable text by page, preserving one-based source page numbers."""
    try:
        reader = PdfReader(filepath, strict=True)
        pages = []
        remaining = MAX_EXTRACTED_CHARS
        truncated = False
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            if page_text.strip():
                page_text = page_text.strip()
                if len(page_text) > remaining:
                    page_text = page_text[:remaining]
                    truncated = True
                pages.append({"page": page_number, "text": page_text})
                remaining -= len(page_text)
                if remaining <= 0:
                    truncated = True
                    break

        if not pages:
            return [], "No readable text was found. Scanned/image-only PDFs are not OCR processed."
        if truncated:
            return pages, "Readable text extracted with a 100,000-character per-file limit."
        return pages, None
    except Exception:
        return [], "This PDF could not be read. Check that it is complete and not password-protected."


def extract_pdf_text(filepath):
    """Backward-compatible plain-text wrapper around :func:`extract_pdf_pages`."""
    pages, warning = extract_pdf_pages(filepath)
    return "\n".join(page["text"] for page in pages), warning
