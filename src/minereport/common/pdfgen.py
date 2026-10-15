"""Dependency-free generator for small, real (pdfplumber-readable) PDF files.

Used only to materialise the repository's *synthetic* fixture. Real reports are
always fetched from their registered public URL.
"""

from __future__ import annotations


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_text_pdf(
    lines: list[str],
    *,
    font_size: int = 9,
    leading: int = 12,
    margin: int = 40,
    page_size: tuple[int, int] = (612, 792),
) -> bytes:
    width, height = page_size
    per_page = max(1, int((height - 2 * margin) // leading))
    chunks = [lines[i : i + per_page] for i in range(0, len(lines), per_page)] or [[]]

    n_pages = len(chunks)
    page_ids = [4 + i for i in range(n_pages)]
    content_ids = [4 + n_pages + i for i in range(n_pages)]

    objects: dict[int, bytes] = {}
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode("latin-1")
    objects[3] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )

    for index, page_lines in enumerate(chunks):
        pid, cid = page_ids[index], content_ids[index]
        objects[pid] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {cid} 0 R >>"
        ).encode("latin-1")

        stream = ["BT", f"/F1 {font_size} Tf", f"{leading} TL", f"1 0 0 1 {margin} {height - margin} Tm"]
        for line in page_lines:
            stream.append(f"({_escape(line)}) Tj T*")
        stream.append("ET")
        content = "\n".join(stream).encode("latin-1", "replace")
        objects[cid] = (
            b"<< /Length " + str(len(content)).encode("latin-1") + b" >>\nstream\n" + content + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for oid in sorted(objects):
        offsets[oid] = len(out)
        out += f"{oid} 0 obj\n".encode("latin-1") + objects[oid] + b"\nendobj\n"

    xref_offset = len(out)
    max_id = max(objects)
    out += f"xref\n0 {max_id + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for oid in range(1, max_id + 1):
        out += f"{offsets[oid]:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)

