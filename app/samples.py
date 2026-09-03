"""Демо-заявки: описания и список доступных файлов."""
from __future__ import annotations

from app.config import SAMPLES_DIR

# Порядок важен — в интерфейсе кнопки идут по нарастанию сложности случая.
SAMPLE_TITLES: dict[str, dict[str, str]] = {
    "01_full.txt": {
        "title": "Полная заявка",
        "hint": "Все обязательные поля на месте — вопросов быть не должно",
    },
    "02_incomplete.txt": {
        "title": "Неполная заявка",
        "hint": "Нет объёма и периода — два блокирующих вопроса",
    },
    "03_ambiguous.txt": {
        "title": "Неоднозначная заявка",
        "hint": "«из Москвы», «металл», ставка без НДС — уточнения с вариантами",
    },
    "04_letter.eml": {
        "title": "Письмо (.eml)",
        "hint": "Шапка письма, подпись и цитата предыдущего письма",
    },
    "05_zayavka.docx": {
        "title": "Word-заявка",
        "hint": "Таблица «поле / значение» внутри документа",
    },
    "06_plan.xlsx": {
        "title": "Excel-план отгрузок",
        "hint": "Помесячный план таблицей",
    },
    "07_scan.pdf": {
        "title": "PDF без текстового слоя",
        "hint": "Скан: система обязана сказать «нужен OCR», а не выдумать поля",
    },
}


def list_samples() -> list[dict[str, str]]:
    """Только реально существующие файлы — их создаёт samples/make_samples.py."""
    result = []
    for name, meta in SAMPLE_TITLES.items():
        if (SAMPLES_DIR / name).exists():
            result.append({"name": name, **meta})
    return result
