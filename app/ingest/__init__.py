"""Приведение любого входящего формата к plain text.

Единая точка входа: detect_and_extract(filename, data) -> (text, SourceInfo).
Дальше по конвейеру идёт только текст — извлечение полей ничего не знает
о том, пришла заявка письмом, вордом или таблицей.
"""
from __future__ import annotations

from pathlib import Path

from app.models import SourceInfo

from . import docx as docx_ingest
from . import eml as eml_ingest
from . import pdf as pdf_ingest
from . import text as text_ingest
from . import xlsx as xlsx_ingest

SUPPORTED = {".txt", ".md", ".eml", ".msg", ".docx", ".pdf", ".xlsx", ".xlsm", ".csv"}


def detect_and_extract(filename: str | None, data: bytes) -> tuple[str, SourceInfo]:
    suffix = Path(filename or "").suffix.lower()
    notes: list[str] = []

    if suffix == ".eml" or suffix == ".msg":
        text, extra = eml_ingest.extract(data)
        kind = "eml"
    elif suffix == ".docx":
        text, extra = docx_ingest.extract(data)
        kind = "docx"
    elif suffix == ".pdf":
        text, extra = pdf_ingest.extract(data)
        kind = "pdf"
    elif suffix in (".xlsx", ".xlsm"):
        text, extra = xlsx_ingest.extract(data)
        kind = "xlsx"
    elif suffix == ".doc":
        raise ValueError(
            "Старый формат .doc не поддерживается прототипом — пересохраните в .docx"
        )
    else:
        text, extra = text_ingest.extract(data)
        kind = "text"
        if suffix and suffix not in SUPPORTED:
            notes.append(f"Расширение {suffix} неизвестно, файл прочитан как текст")

    notes.extend(extra)
    text = text.strip()

    # Скан / пустой документ: осмысленных данных нет, и это надо сказать честно,
    # а не отдавать пустую заявку как «ничего не указано».
    is_scan = kind in ("pdf", "docx") and len(text) < 40
    if is_scan:
        notes.append(
            "В документе почти нет текстового слоя — похоже на скан или картинку. "
            "Нужен OCR либо исходный файл в текстовом виде."
        )

    info = SourceInfo(
        filename=filename,
        kind=kind,
        chars=len(text),
        is_scan=is_scan,
        notes=notes,
    )
    return text, info
