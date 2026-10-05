import re
from collections import defaultdict

QUANTITY_PATTERNS = (
    re.compile(
        r"^\s*(?P<item>.+?)\s*(?:[-–—,:]\s*)?"
        r"(?:qty|quantity)\s*:?\s*(?P<quantity>\d+(?:\.\d+)?)"
        r"(?:\s*(?P<unit>[a-zA-Z]+))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?P<item>.+?)\s+(?:x\s*)?(?P<quantity>\d+(?:\.\d+)?)"
        r"\s*(?P<unit>tablets?|tabs?|capsules?|caps?|vials?|units?|"
        r"strips?|bottles?|pieces?|pcs?)\b",
        re.IGNORECASE,
    ),
)
PRICE_PATTERN = re.compile(r"(?:₹|INR\b|Rs\.?\s*)\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)
UNIT_ALIASES = {
    "tabs": "tablet",
    "tab": "tablet",
    "tablets": "tablet",
    "caps": "capsule",
    "cap": "capsule",
    "capsules": "capsule",
    "vials": "vial",
    "units": "unit",
    "strips": "strip",
    "bottles": "bottle",
    "pieces": "piece",
    "pcs": "piece",
}
FINANCIAL_TERMS = {
    "Fees and charges": re.compile(
        r"\b(fee|fees|charge|charges|commission|expense ratio|exit load|"
        r"stamp duty|transaction cost|premium)\b",
        re.IGNORECASE,
    ),
    "Lock-in and withdrawals": re.compile(
        r"\b(lock[\s-]?in|withdraw|redemption|surrender|premature|exit load)\b",
        re.IGNORECASE,
    ),
    "Risks": re.compile(
        r"\b(risk|market risk|credit risk|volatility|loss of capital|"
        r"not guaranteed|no guarantee)\b",
        re.IGNORECASE,
    ),
    "Important conditions": re.compile(
        r"\b(subject to|condition|exclusion|waiting period|eligibility|"
        r"penalty|penalties|limitation)\b",
        re.IGNORECASE,
    ),
}


def extract_document(saved_file, document_type):
    """Extract source text where supported without claiming OCR for images."""
    document = {
        "filename": saved_file["original_name"],
        "type": document_type,
        "format": saved_file["extension"],
        "pages": [],
        "text_available": False,
        "message": "",
        "storage_key": saved_file["storage_key"],
    }
    if saved_file["extension"] != "pdf":
        document["message"] = "Image accepted as evidence; image text is not extracted (OCR unavailable)."
        return document

    from utils.pdf import extract_pdf_pages

    pages, warning = extract_pdf_pages(saved_file["saved_path"])
    document["pages"] = pages
    document["text_available"] = bool(pages)
    document["message"] = warning or "Text extracted from PDF."
    return document


def analyze_financial_documents(documents, instruction="", ai_review=None):
    """Return source-backed document observations and optional model interpretation."""
    evidence = []
    text_sections = []
    for document in documents:
        for page in document.get("pages", []):
            text = page.get("text", "")
            text_sections.append(text)
            for label, pattern in FINANCIAL_TERMS.items():
                for match in pattern.finditer(text):
                    start = max(0, match.start() - 100)
                    end = min(len(text), match.end() + 160)
                    evidence.append({
                        "category": label,
                        "quote": re.sub(r"\s+", " ", text[start:end]).strip(),
                        "source_document": document["filename"],
                        "page": page["page"],
                        "matched_term": match.group(0),
                    })

    text_available = bool(text_sections)
    findings = []
    seen_evidence = set()
    for item in evidence:
        key = (item["category"], item["source_document"], item["page"], item["quote"])
        if key not in seen_evidence:
            seen_evidence.add(key)
            findings.append(item)
            if len(findings) >= 100:
                break

    categories_found = {item["category"] for item in findings}
    not_found = [
        category for category in FINANCIAL_TERMS
        if category not in categories_found
    ]
    document_checks = [
        {
            "field": category,
            "status": (
                "FACT FOUND IN DOCUMENT"
                if category in categories_found
                else "NOT FOUND IN EXTRACTED TEXT"
            ),
            "evidence": [
                item for item in findings if item["category"] == category
            ],
        }
        for category in FINANCIAL_TERMS
    ]
    if ai_review:
        summary = ai_review["summary"]
    elif text_available:
        summary = (
            "Readable PDF text was extracted. The items below are keyword-based "
            "source excerpts, not a financial recommendation or a complete review."
        )
    else:
        summary = (
            "No readable document text was available for analysis. Images and "
            "scanned PDFs are stored as evidence only; OCR is not available."
        )

    return {
        "module_type": "financial",
        "status": "AI reviewed" if ai_review else ("Text extracted only" if text_available else "Insufficient text"),
        "summary": summary,
        "instruction": instruction,
        "key_findings": findings,
        "document_checks": document_checks,
        "fees_and_charges": [item for item in findings if item["category"] == "Fees and charges"],
        "important_conditions": [item for item in findings if item["category"] == "Important conditions"],
        "risks": [item for item in findings if item["category"] == "Risks"],
        "positive_points": [],
        "items_requiring_review": not_found,
        "questions_to_check": [],
        "ai_interpretation": ai_review,
        "documents": documents,
        "limitations": [
            "Keyword excerpts do not establish that a term is beneficial or harmful.",
            "Scanned PDFs and images are not OCR processed.",
            "No investment, legal, or tax advice is provided.",
        ],
    }


def _normalize_name(value):
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _normalize_unit(value):
    unit = (value or "unspecified").lower()
    return UNIT_ALIASES.get(unit, unit)


def _extract_line_item(line, page_number, filename, role):
    for pattern in QUANTITY_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue
        name = match.group("item").strip(" \t-–—,:;.|")
        if not name or len(name) > 100:
            continue
        quantity = float(match.group("quantity"))
        unit = _normalize_unit(match.groupdict().get("unit"))
        price_match = PRICE_PATTERN.search(line)
        price = None
        if price_match:
            try:
                price = float(price_match.group(1).replace(",", ""))
            except ValueError:
                price = None
        return {
            "name": name,
            "normalized_name": _normalize_name(name),
            "quantity": quantity,
            "unit": unit,
            "price": price,
            "source_document": filename,
            "source_type": role,
            "page": page_number,
            "quote": re.sub(r"\s+", " ", line).strip()[:300],
        }
    return None


def extract_hospital_items(documents, manual_medicines=None):
    items = []
    for document in documents:
        if not document.get("text_available"):
            continue
        for page in document.get("pages", []):
            for line in page.get("text", "").splitlines():
                item = _extract_line_item(
                    line, page["page"], document["filename"], document["type"]
                )
                if item:
                    items.append(item)

    for medicine in manual_medicines or []:
        item = _extract_line_item(
            f"{medicine['name']} quantity {medicine['quantity']} {medicine.get('unit', '')}",
            None,
            "Manual entry",
            "manual",
        )
        if item:
            items.append(item)
        else:
            items.append({
                "name": medicine["name"],
                "normalized_name": _normalize_name(medicine["name"]),
                "quantity": float(medicine["quantity"]),
                "unit": _normalize_unit(medicine.get("unit", "user-entered")),
                "price": None,
                "source_document": "Manual entry",
                "source_type": "manual",
                "page": None,
                "quote": f"{medicine['name']} — {medicine['quantity']}",
            })
    return items


def compare_hospital_evidence(documents, manual_medicines=None):
    """Compare only line items that can be deterministically parsed from text."""
    items = extract_hospital_items(documents, manual_medicines)
    groups = defaultdict(list)
    for item in items:
        groups[item["normalized_name"]].append(item)

    bills = [item for item in items if item["source_type"] == "Hospital bill"]
    prescriptions = [
        item for item in items
        if item["source_type"] in {"Prescription", "Manual entry"}
    ]
    purchases = [
        item for item in items if item["source_type"] == "Medicine purchase proof"
    ]
    findings = []

    for bill_item in bills:
        support = next(
            (item for item in prescriptions
             if item["normalized_name"] == bill_item["normalized_name"]),
            None,
        )
        purchase = next(
            (item for item in purchases
             if item["normalized_name"] == bill_item["normalized_name"]),
            None,
        )
        if support is None:
            findings.append({
                "type": "Medicine not found in prescription",
                "severity": "REVIEW",
                "status": "Supporting evidence not found",
                "item": bill_item["name"],
                "bill_value": f"{bill_item['quantity']} {bill_item['unit']}",
                "supporting_value": "Not found in extracted prescription/manual entries",
                "difference": None,
                "source_documents": [bill_item["source_document"]],
                "source_pages": [bill_item["page"]] if bill_item["page"] else [],
                "explanation": "This bill item was not matched to readable prescription or manual-entry evidence.",
            })
        elif bill_item["unit"] != support["unit"]:
            findings.append({
                "type": "Quantity unit mismatch",
                "severity": "WARNING",
                "status": "Needs review",
                "item": bill_item["name"],
                "bill_value": f"{bill_item['quantity']} {bill_item['unit']}",
                "supporting_value": f"{support['quantity']} {support['unit']}",
                "difference": None,
                "source_documents": [bill_item["source_document"], support["source_document"]],
                "source_pages": [page for page in (bill_item["page"], support["page"]) if page],
                "explanation": "The quantities use different or unspecified units and cannot be safely compared.",
            })
        elif bill_item["unit"] == support["unit"] and bill_item["quantity"] != support["quantity"]:
            difference = bill_item["quantity"] - support["quantity"]
            findings.append({
                "type": "Quantity mismatch",
                "severity": "WARNING",
                "status": "Mismatch detected",
                "item": bill_item["name"],
                "bill_value": f"{bill_item['quantity']} {bill_item['unit']}",
                "supporting_value": f"{support['quantity']} {support['unit']}",
                "difference": difference,
                "source_documents": [bill_item["source_document"], support["source_document"]],
                "source_pages": [page for page in (bill_item["page"], support["page"]) if page],
                "explanation": "The extracted billed quantity differs from the extracted supporting quantity. Verify the original documents.",
            })
        elif support["source_type"] == "manual":
            findings.append({
                "type": "Manual reference comparison",
                "severity": "INFO",
                "status": "Compared with user-entered information",
                "item": bill_item["name"],
                "bill_value": f"{bill_item['quantity']} {bill_item['unit']}",
                "supporting_value": f"{support['quantity']} {support['unit']}",
                "difference": None,
                "source_documents": [bill_item["source_document"], support["source_document"]],
                "source_pages": [bill_item["page"]] if bill_item["page"] else [],
                "explanation": "Manual input is user-provided and was not independently verified.",
            })
        elif support:
            findings.append({
                "type": "Quantity comparison",
                "severity": "INFO",
                "status": "Consistent with supporting evidence",
                "item": bill_item["name"],
                "bill_value": f"{bill_item['quantity']} {bill_item['unit']}",
                "supporting_value": f"{support['quantity']} {support['unit']}",
                "difference": 0,
                "source_documents": [bill_item["source_document"], support["source_document"]],
                "source_pages": [page for page in (bill_item["page"], support["page"]) if page],
                "explanation": "The extracted quantities and units match.",
            })

        if purchase and bill_item["price"] is not None and purchase["price"] is not None:
            if bill_item["price"] != purchase["price"]:
                findings.append({
                    "type": "Price discrepancy",
                    "severity": "REVIEW",
                    "status": "Needs review",
                    "item": bill_item["name"],
                    "bill_value": bill_item["price"],
                    "supporting_value": purchase["price"],
                    "difference": round(bill_item["price"] - purchase["price"], 2),
                    "source_documents": [bill_item["source_document"], purchase["source_document"]],
                    "source_pages": [page for page in (bill_item["page"], purchase["page"]) if page],
                    "explanation": "Extracted prices differ; confirm that the amounts refer to the same medicine, quantity, and billing basis.",
                })
        if (
            purchase
            and purchase["unit"] == bill_item["unit"]
            and purchase["quantity"] != bill_item["quantity"]
        ):
            findings.append({
                "type": "Purchase quantity mismatch",
                "severity": "WARNING",
                "status": "Mismatch detected",
                "item": bill_item["name"],
                "bill_value": f"{bill_item['quantity']} {bill_item['unit']}",
                "supporting_value": f"{purchase['quantity']} {purchase['unit']}",
                "difference": round(bill_item["quantity"] - purchase["quantity"], 4),
                "source_documents": [bill_item["source_document"], purchase["source_document"]],
                "source_pages": [page for page in (bill_item["page"], purchase["page"]) if page],
                "explanation": "The extracted billed and purchased quantities differ; verify that both sources refer to the same item and period.",
            })

    bill_names = {item["normalized_name"] for item in bills}
    for prescription_item in prescriptions:
        if (
            prescription_item["source_type"] == "Prescription"
            and prescription_item["normalized_name"] not in bill_names
        ):
            findings.append({
                "type": "Prescription medicine not found in bill",
                "severity": "REVIEW",
                "status": "Needs review",
                "item": prescription_item["name"],
                "bill_value": "Not found in extracted bill lines",
                "supporting_value": f"{prescription_item['quantity']} {prescription_item['unit']}",
                "difference": None,
                "source_documents": [prescription_item["source_document"]],
                "source_pages": [prescription_item["page"]] if prescription_item["page"] else [],
                "explanation": "This prescription item was not matched to a readable hospital bill line.",
            })

    for normalized_name, entries in groups.items():
        bill_entries = [item for item in entries if item["source_type"] == "Hospital bill"]
        if len(bill_entries) > 1:
            first = bill_entries[0]
            findings.append({
                "type": "Potential duplicate billing item",
                "severity": "REVIEW",
                "status": "Needs review",
                "item": first["name"],
                "bill_value": f"{len(bill_entries)} similar bill lines",
                "supporting_value": None,
                "difference": None,
                "source_documents": list(dict.fromkeys(item["source_document"] for item in bill_entries)),
                "source_pages": list(dict.fromkeys(item["page"] for item in bill_entries if item["page"])),
                "explanation": "Repeated extracted descriptions may be duplicates or legitimate separate charges; verify the original bill.",
            })

    has_bill = any(document["type"] == "Hospital bill" for document in documents)
    has_readable_support = any(
        document.get("text_available")
        and document["type"] in {"Prescription", "Medicine purchase proof"}
        for document in documents
    ) or bool(manual_medicines)
    if has_bill and not has_readable_support:
        findings.append({
            "type": "Missing supporting evidence",
            "severity": "REVIEW",
            "status": "Insufficient supporting evidence",
            "item": "Supporting prescription or purchase evidence",
            "bill_value": None,
            "supporting_value": "No readable supporting document or manual medicine entry",
            "difference": None,
            "source_documents": [],
            "source_pages": [],
            "explanation": "A bill was supplied, but no readable supporting evidence was available for comparison.",
        })

    for finding in findings:
        normalized_name = _normalize_name(finding.get("item", ""))
        source_names = set(finding.get("source_documents", []))
        evidence_items = [
            item for item in items
            if item["normalized_name"] == normalized_name
            and item["source_document"] in source_names
        ]
        finding["evidence"] = [
            {
                "source_type": item["source_type"],
                "source_document": item["source_document"],
                "page": item["page"],
                "value": f"{item['quantity']} {item['unit']}",
                "price": item["price"],
                "quote": item["quote"],
            }
            for item in evidence_items
        ]

    if any(item["severity"] == "WARNING" for item in findings):
        status = "Mismatch detected"
    elif any(item["severity"] == "REVIEW" for item in findings) or any(
        item["source_type"] == "manual" for item in items
    ):
        status = "Needs review"
    elif findings:
        status = "Consistent with supporting evidence"
    else:
        status = "Insufficient supporting evidence"
    return {
        "module_type": "hospital",
        "status": status,
        "summary": (
            "Deterministic comparisons were made only for clearly extracted line items. "
            "These checks do not establish fraud or medical necessity."
        ),
        "findings": findings,
        "extracted_items": items,
        "documents": documents,
        "limitations": [
            "Line-item extraction is heuristic; inspect the cited source before drawing conclusions.",
            "Scanned PDFs and images are not OCR processed.",
            "Price comparison is not performed unless both sources expose a comparable extracted amount.",
            "Diagnostic tests, procedures, dates, and non-medicine charges are not automatically reconciled.",
            "This tool does not determine fraud, medical necessity, or legal liability.",
        ],
    }
