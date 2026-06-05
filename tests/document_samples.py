from __future__ import annotations

import io
import zipfile


def zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def docx_bytes(*texts: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in texts)
    return zip_bytes(
        {
            "[Content_Types].xml": "",
            "word/document.xml": (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:body>{body}</w:body></w:document>"
            ),
        }
    )


def pptx_bytes(*slides: tuple[str, ...]) -> bytes:
    files = {"[Content_Types].xml": ""}
    for index, texts in enumerate(slides, start=1):
        runs = "".join(f"<a:t>{text}</a:t>" for text in texts)
        files[f"ppt/slides/slide{index}.xml"] = (
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            f"<p:cSld><p:spTree>{runs}</p:spTree></p:cSld></p:sld>"
        )
    return zip_bytes(files)


def xlsx_bytes(rows: list[list[str]]) -> bytes:
    values = [value for row in rows for value in row]
    shared_strings = "".join(f"<si><t>{value}</t></si>" for value in values)
    cells = []
    value_index = 0
    for row_index, row in enumerate(rows, start=1):
        row_cells = []
        for col_index, _ in enumerate(row):
            column = chr(ord("A") + col_index)
            row_cells.append(f'<c r="{column}{row_index}" t="s"><v>{value_index}</v></c>')
            value_index += 1
        cells.append(f'<row r="{row_index}">{"".join(row_cells)}</row>')
    sheet = "".join(cells)
    return zip_bytes(
        {
            "[Content_Types].xml": "",
            "xl/sharedStrings.xml": (
                '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"{shared_strings}</sst>"
            ),
            "xl/worksheets/sheet1.xml": (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"<sheetData>{sheet}</sheetData></worksheet>"
            ),
        }
    )


def pdf_bytes(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        ),
    ]
    parts = [b"%PDF-1.4\n"]
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(sum(len(part) for part in parts))
        parts.append(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref_offset = sum(len(part) for part in parts)
    xref = [b"xref\n0 6\n", b"0000000000 65535 f \n"]
    xref.extend(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets[1:])
    parts.extend(
        [
            *xref,
            b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n",
            str(xref_offset).encode("ascii"),
            b"\n%%EOF\n",
        ]
    )
    return b"".join(parts)
