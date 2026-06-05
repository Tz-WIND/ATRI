"""Shared document text extraction for uploads and chat attachments."""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterable
from pathlib import Path
from xml.etree import ElementTree


class DocumentTextError(ValueError):
    """Raised when a document cannot be converted into plain text."""


TEXT_EXTENSIONS = (".txt", ".md", ".json", ".csv", ".log", ".yaml", ".yml")
OOXML_EXTENSIONS = (".docx", ".pptx", ".xlsx")
PDF_EXTENSIONS = (".pdf",)
LEGACY_BINARY_OFFICE_EXTENSIONS = (".doc", ".ppt", ".xls")
SUPPORTED_DOCUMENT_EXTENSIONS = TEXT_EXTENSIONS + PDF_EXTENSIONS + OOXML_EXTENSIONS


def supported_document_accept() -> str:
    return ",".join(SUPPORTED_DOCUMENT_EXTENSIONS)


def extract_document_text(file_name: str, content: bytes) -> str:
    suffix = Path(str(file_name or "")).suffix.lower()
    if suffix in LEGACY_BINARY_OFFICE_EXTENSIONS:
        raise DocumentTextError(
            "legacy binary Office formats are not supported; save as .docx, .pptx, or .xlsx"
        )
    if not content:
        raise DocumentTextError("document content is empty")

    if suffix in TEXT_EXTENSIONS:
        text = _decode_text(content)
    elif suffix == ".docx":
        text = _extract_docx_text(content)
    elif suffix == ".pptx":
        text = _extract_pptx_text(content)
    elif suffix == ".xlsx":
        text = _extract_xlsx_text(content)
    elif suffix == ".pdf":
        text = _extract_pdf_text(content)
    else:
        raise DocumentTextError(f"unsupported document type: {suffix or 'unknown'}")

    text = _normalize_text(text)
    if not text:
        raise DocumentTextError("document contains no extractable text")
    return text


def _decode_text(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentTextError("text documents must be UTF-8 encoded") from exc


def _extract_docx_text(content: bytes) -> str:
    parts: list[str] = []
    with _open_zip(content, "DOCX") as archive:
        names = [
            "word/document.xml",
            *sorted(name for name in archive.namelist() if re.match(r"word/header\d+\.xml$", name)),
            *sorted(name for name in archive.namelist() if re.match(r"word/footer\d+\.xml$", name)),
        ]
        for name in names:
            try:
                root = _xml_root(archive.read(name), name)
            except KeyError:
                continue
            parts.extend(_word_paragraphs(root))
    return "\n".join(parts)


def _extract_pptx_text(content: bytes) -> str:
    sections: list[str] = []
    with _open_zip(content, "PPTX") as archive:
        slide_names = sorted(
            (name for name in archive.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", name)),
            key=_numeric_part_key,
        )
        for index, name in enumerate(slide_names, start=1):
            root = _xml_root(archive.read(name), name)
            lines = _presentation_lines(root)
            if lines:
                sections.append(f"[Slide {index}]\n" + "\n".join(lines))
    return "\n\n".join(sections)


def _extract_xlsx_text(content: bytes) -> str:
    sections: list[str] = []
    with _open_zip(content, "XLSX") as archive:
        shared_strings = _xlsx_shared_strings(archive)
        sheet_names = sorted(
            (
                name
                for name in archive.namelist()
                if re.match(r"xl/worksheets/sheet\d+\.xml$", name)
            ),
            key=_numeric_part_key,
        )
        for index, name in enumerate(sheet_names, start=1):
            root = _xml_root(archive.read(name), name)
            rows = _xlsx_rows(root, shared_strings)
            if rows:
                sections.append(f"[Sheet {index}]\n" + "\n".join(rows))
    return "\n\n".join(sections)


def _extract_pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentTextError("PDF extraction requires the pypdf package") from exc

    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise DocumentTextError(f"failed to extract PDF text: {exc}") from exc
    return "\n\n".join(page.strip() for page in pages if page.strip())


def _open_zip(content: bytes, label: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise DocumentTextError(f"{label} file is not a valid OOXML archive") from exc


def _xml_root(content: bytes, name: str) -> ElementTree.Element:
    lowered = content[:4096].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise DocumentTextError(f"unsupported XML entity markup in {name}")
    try:
        return ElementTree.fromstring(content)  # noqa: S314 - DTD/entity markup is rejected above.
    except ElementTree.ParseError as exc:
        raise DocumentTextError(f"failed to parse XML part {name}") from exc


def _word_paragraphs(root: ElementTree.Element) -> list[str]:
    paragraphs: list[str] = []
    for paragraph in _iter_local(root, "p"):
        fragments: list[str] = []
        for element in paragraph.iter():
            name = _local_name(element.tag)
            if name == "t" and element.text:
                fragments.append(element.text)
            elif name == "tab":
                fragments.append("\t")
            elif name in {"br", "cr"}:
                fragments.append("\n")
        text = _normalize_text("".join(fragments))
        if text:
            paragraphs.append(text)
    if paragraphs:
        return paragraphs
    return _text_nodes(root)


def _presentation_lines(root: ElementTree.Element) -> list[str]:
    lines: list[str] = []
    for paragraph in _iter_local(root, "p"):
        text = "".join(
            element.text or "" for element in paragraph.iter() if _local_name(element.tag) == "t"
        )
        text = _normalize_text(text)
        if text:
            lines.append(text)
    return lines or _text_nodes(root)


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = _xml_root(archive.read("xl/sharedStrings.xml"), "xl/sharedStrings.xml")
    except KeyError:
        return []
    strings: list[str] = []
    for item in _iter_local(root, "si"):
        strings.append(_normalize_text("".join(_text_values(item))))
    return strings


def _xlsx_rows(root: ElementTree.Element, shared_strings: list[str]) -> list[str]:
    rows: list[str] = []
    for row in _iter_local(root, "row"):
        values_by_column: dict[int, str] = {}
        for cell in (child for child in list(row) if _local_name(child.tag) == "c"):
            column = _xlsx_cell_column(cell.get("r", ""))
            if column < 1:
                column = len(values_by_column) + 1
            values_by_column[column] = _xlsx_cell_text(cell, shared_strings)
        if not values_by_column:
            continue
        max_column = max(values_by_column)
        values = [values_by_column.get(index, "") for index in range(1, max_column + 1)]
        line = "\t".join(values).rstrip()
        if line:
            rows.append(line)
    return rows


def _xlsx_cell_text(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.get("t", "")
    if cell_type == "inlineStr":
        return _normalize_text("".join(_text_values(cell)))

    value = _first_local_text(cell, "v")
    if cell_type == "s":
        try:
            index = int(value)
        except ValueError:
            return ""
        if 0 <= index < len(shared_strings):
            return shared_strings[index]
        return ""
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE" if value == "0" else value
    return _normalize_text(value)


def _xlsx_cell_column(reference: str) -> int:
    column = 0
    for char in reference:
        if not char.isalpha():
            break
        column = column * 26 + (ord(char.upper()) - ord("A") + 1)
    return column


def _first_local_text(root: ElementTree.Element, name: str) -> str:
    for element in root.iter():
        if _local_name(element.tag) == name:
            return str(element.text or "")
    return ""


def _text_nodes(root: ElementTree.Element) -> list[str]:
    values = [_normalize_text(text) for text in _text_values(root)]
    return [value for value in values if value]


def _text_values(root: ElementTree.Element) -> Iterable[str]:
    for element in root.iter():
        if _local_name(element.tag) == "t" and element.text:
            yield element.text


def _iter_local(root: ElementTree.Element, name: str) -> Iterable[ElementTree.Element]:
    for element in root.iter():
        if _local_name(element.tag) == name:
            yield element


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _numeric_part_key(name: str) -> tuple[str, int]:
    match = re.search(r"(\d+)(?=\.xml$)", name)
    prefix = re.sub(r"\d+(?=\.xml$)", "", name)
    return (prefix, int(match.group(1)) if match else 0)


def _normalize_text(text: str) -> str:
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()
