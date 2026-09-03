from __future__ import annotations

import io


def extract(data: bytes) -> tuple[str, list[str]]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    notes: list[str] = []
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 — прототип не должен падать на битом PDF
            notes.append(f"Страница PDF не прочиталась: {exc}")
    return "\n".join(pages), notes
