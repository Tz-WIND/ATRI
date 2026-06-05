from __future__ import annotations

import pytest

from core.document_text import (
    DocumentTextError,
    extract_document_text,
    supported_document_accept,
)
from tests.document_samples import docx_bytes, pdf_bytes, pptx_bytes, xlsx_bytes


def test_extract_document_text_decodes_plain_text_formats():
    text = extract_document_text("notes.md", b"# Title\n\nAlice works at Acme.")

    assert text == "# Title\n\nAlice works at Acme."


def test_extract_document_text_reads_docx_text_runs():
    text = extract_document_text("brief.docx", docx_bytes("Alice", "works at Acme"))

    assert "Alice" in text
    assert "works at Acme" in text


def test_extract_document_text_reads_pptx_slides():
    text = extract_document_text(
        "deck.pptx",
        pptx_bytes(("Launch Plan", "Alice owns rollout"), ("Budget", "Q3")),
    )

    assert "[Slide 1]" in text
    assert "Launch Plan" in text
    assert "[Slide 2]" in text
    assert "Budget" in text


def test_extract_document_text_reads_xlsx_shared_strings():
    text = extract_document_text(
        "sheet.xlsx",
        xlsx_bytes([["Name", "Role"], ["Alice", "Investor"]]),
    )

    assert "[Sheet 1]" in text
    assert "Name\tRole" in text
    assert "Alice\tInvestor" in text


def test_extract_document_text_reads_pdf_pages():
    text = extract_document_text("facts.pdf", pdf_bytes("Alice PDF fact"))

    assert "Alice PDF fact" in text


def test_extract_document_text_rejects_legacy_binary_office_formats():
    with pytest.raises(DocumentTextError, match="legacy binary Office"):
        extract_document_text("slides.ppt", b"not an OOXML file")


def test_supported_document_accept_lists_common_manual_ingest_formats():
    accept = supported_document_accept()

    assert ".pdf" in accept
    assert ".pptx" in accept
    assert ".xlsx" in accept
    assert ".docx" in accept
