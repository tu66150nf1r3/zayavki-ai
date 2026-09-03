"""Применение ответов менеджера к разобранной заявке.

Замыкает цикл «вопрос → ответ → пересчёт». К модели повторно не обращаемся:
ответ живого человека надёжнее любого извлечения, поэтому такие поля помечаются
answered_by_user и не проходят проверку цитатой.
"""
from __future__ import annotations

import re

from app.models import FieldStatus, ProcessResult
from app.pipeline import finalize
from app.rules.fallback import extract_with_rules

# Варианты ответа приходят из UI в виде «Ховрино (код 192300, МСК, Москва)».
OPTION_SUFFIX_RE = re.compile(r"\s*\((?:код|ЕСР|ЕТСНГ)\s+[^)]*\)\s*$")

COMPOSITE_FIELDS = {"volume", "period", "budget"}


def _clean_option(answer: str) -> str:
    return OPTION_SUFFIX_RE.sub("", answer).strip()


def apply_answers(result: ProcessResult, answers: dict[str, str]) -> ProcessResult:
    draft = result.draft.model_copy(deep=True)

    for name, raw_answer in answers.items():
        answer = (raw_answer or "").strip()
        if not answer or name == "__source__":
            continue
        try:
            field = draft.get(name)
        except AttributeError:
            continue

        field.candidates = []
        field.answered_by_user = True
        field.confidence = 1.0
        field.evidence = None
        field.raw = answer
        field.comment = "Уточнено менеджером"

        if name in COMPOSITE_FIELDS:
            # Ответ «25 вагонов» или «с 10 по 20 октября» разбираем теми же
            # правилами, что и заявку целиком — отдельный парсер не нужен.
            parsed = getattr(extract_with_rules(answer), name)
            if name == "volume" and (parsed.wagons or parsed.tons):
                field.value = {
                    "wagons": parsed.wagons,
                    "tons": parsed.tons,
                    "per_period": parsed.per_period,
                }
                field.status = FieldStatus.ok
            elif name == "period" and (parsed.date_from or parsed.date_to):
                field.value = {"date_from": parsed.date_from, "date_to": parsed.date_to}
                field.status = FieldStatus.ok
            elif name == "budget" and parsed.amount is not None:
                field.value = {
                    "amount": parsed.amount,
                    "currency": parsed.currency,
                    "basis": parsed.basis,
                    "vat": parsed.vat,
                }
                field.status = FieldStatus.ok
            else:
                field.value = None
                field.status = FieldStatus.ambiguous
                field.comment = f"Ответ «{answer}» не удалось разобрать — переформулируйте"
            continue

        field.value = _clean_option(answer)
        field.status = FieldStatus.ok

    return finalize(
        draft,
        result.text_preview,
        result.source,
        extractor=result.extractor,
        warnings=[],
    )
