"""Конвейер обработки заявки.

    файл/текст → ingest → извлечение (LLM или правила) → нормализация по
    справочникам → проверки → вопросы пользователю

Извлечение отвечает только за «что написано в заявке». Решение «можно ли с этим
заводить сделку» принимают следующие шаги, и они детерминированные.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from app.directory.lookup import MatchResult, lookup_cargo, lookup_station, tons_per_wagon
from app.ingest import detect_and_extract
from app.llm.client import LlmError
from app.llm.extractor import extract_with_llm
from app.llm.schema import LlmBudget, LlmExtraction, LlmField, LlmPeriod, LlmVolume
from app.models import (
    Candidate,
    ExtractedField,
    FieldStatus,
    OrderDraft,
    ProcessResult,
    SourceInfo,
)
from app.rules.fallback import extract_with_rules
from app.validate.questions import build_questions, render_letter
from app.validate.rules import verify

logger = logging.getLogger(__name__)

PREVIEW_LIMIT = 20000


def _base_field(source: LlmField, value=None, raw: str | None = None) -> ExtractedField:
    filled = value not in (None, "", {}, [])
    if filled:
        status = FieldStatus.ambiguous if source.ambiguous else FieldStatus.ok
        confidence = 0.5 if source.ambiguous else 0.85
    else:
        # Значения нет, но модель отметила двусмысленность («в порт», «по рынку») —
        # это не «пусто», это «есть намёк, который надо уточнить».
        status = FieldStatus.ambiguous if source.ambiguous else FieldStatus.missing
        confidence = 0.0
    return ExtractedField(
        value=value,
        raw=raw,
        evidence=source.evidence,
        status=status,
        confidence=confidence,
        comment=source.comment,
    )


def _text_field(source: LlmField) -> ExtractedField:
    value = source.value
    return _base_field(source, value=value, raw=value)


def _volume_field(source: LlmVolume) -> ExtractedField:
    has_data = source.wagons is not None or source.tons is not None
    value = (
        {"wagons": source.wagons, "tons": source.tons, "per_period": source.per_period}
        if has_data
        else None
    )
    return _base_field(source, value=value, raw=source.raw or source.value)


def _period_field(source: LlmPeriod) -> ExtractedField:
    has_data = bool(source.date_from or source.date_to)
    value = {"date_from": source.date_from, "date_to": source.date_to} if has_data else None
    return _base_field(source, value=value, raw=source.raw or source.value)


def _budget_field(source: LlmBudget) -> ExtractedField:
    has_data = source.amount is not None
    value = (
        {
            "amount": source.amount,
            "currency": source.currency or ("RUB" if source.amount else None),
            "basis": source.basis,
            "vat": source.vat,
        }
        if has_data
        else None
    )
    return _base_field(source, value=value, raw=source.raw or source.value)


def to_draft(extraction: LlmExtraction) -> OrderDraft:
    return OrderDraft(
        company=_text_field(extraction.company),
        station_from=_text_field(extraction.station_from),
        station_to=_text_field(extraction.station_to),
        cargo=_text_field(extraction.cargo),
        volume=_volume_field(extraction.volume),
        period=_period_field(extraction.period),
        loading_terms=_text_field(extraction.loading_terms),
        unloading_terms=_text_field(extraction.unloading_terms),
        budget=_budget_field(extraction.budget),
    )


def _apply_match(field: ExtractedField, match: MatchResult) -> None:
    """Переносит результат справочника в поле. Справочник — источник истины по коду."""
    field.candidates = [
        Candidate(value=c.value, code=c.code, hint=c.hint, score=round(c.score, 2))
        for c in match.candidates
    ]
    if match.status == "ok":
        field.value = match.value
        field.code = match.code
        # Модель отметила двусмысленность, а справочник дал точное попадание —
        # верим справочнику только при уверенном совпадении.
        if match.score >= 0.95 and field.status == FieldStatus.ambiguous:
            field.status = FieldStatus.ok
            field.confidence = max(field.confidence, 0.8)
            field.comment = match.comment
        elif field.status != FieldStatus.ambiguous:
            field.status = FieldStatus.ok
            field.confidence = max(field.confidence, match.score)
            field.comment = match.comment or field.comment
        return

    field.status = FieldStatus.ambiguous
    field.confidence = min(field.confidence, 0.4)
    field.comment = match.comment or field.comment


def normalize_draft(draft: OrderDraft) -> float | None:
    """Сопоставляет станции и груз со справочниками. Возвращает норму загрузки вагона."""
    for name in ("station_from", "station_to"):
        field = draft.get(name)
        if field.filled and isinstance(field.value, str):
            field.raw = field.raw or field.value
            _apply_match(field, lookup_station(field.value))

    norm: float | None = None
    cargo = draft.cargo
    if cargo.filled and isinstance(cargo.value, str):
        cargo.raw = cargo.raw or cargo.value
        match = lookup_cargo(cargo.value)
        _apply_match(cargo, match)
        norm = tons_per_wagon(match.payload)
    return norm


def _format_date(value: str | None) -> str:
    if not value:
        return "?"
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return value


def _thousands(value: float) -> str:
    text = f"{value:,.0f}".replace(",", " ")
    return text


def fill_display(draft: OrderDraft) -> None:
    """Готовит человекочитаемые строки, чтобы интерфейс не занимался форматированием."""
    for name, field in draft.items():
        if not field.filled:
            field.display = None
            continue
        value = field.value
        if name in ("station_from", "station_to") and field.code:
            field.display = f"{value} (ЕСР {field.code})"
        elif name == "cargo" and field.code:
            field.display = f"{value} (ЕТСНГ {field.code})"
        elif name == "volume" and isinstance(value, dict):
            parts = []
            if value.get("wagons"):
                parts.append(f"{value['wagons']} ваг.")
            if value.get("tons"):
                parts.append(f"{_thousands(value['tons'])} т")
            suffix = value.get("per_period")
            tail = f" ({suffix})" if suffix and suffix != "всего" else ""
            field.display = " / ".join(parts) + tail
        elif name == "period" and isinstance(value, dict):
            field.display = (
                f"{_format_date(value.get('date_from'))} — {_format_date(value.get('date_to'))}"
            )
        elif name == "budget" and isinstance(value, dict):
            parts = [f"{_thousands(value['amount'])} {value.get('currency') or ''}".strip()]
            if value.get("basis"):
                parts.append(value["basis"])
            if value.get("vat"):
                parts.append(value["vat"])
            field.display = ", ".join(parts)
        else:
            field.display = str(value)


def finalize(
    draft: OrderDraft,
    text: str,
    source: SourceInfo,
    today: date | None = None,
    extractor: str = "rules",
    warnings: list[str] | None = None,
) -> ProcessResult:
    """Нормализация → проверки → вопросы. Общий хвост для разбора и для доуточнения."""
    warnings = list(warnings or [])
    norm = normalize_draft(draft)
    warnings += verify(draft, text, today=today, tons_per_wagon=norm)
    fill_display(draft)
    questions = build_questions(draft, source)
    return ProcessResult(
        source=source,
        draft=draft,
        questions=questions,
        ready_for_deal=not any(q.blocking for q in questions),
        extractor=extractor,  # type: ignore[arg-type]
        warnings=warnings + source.notes,
        text_preview=text[:PREVIEW_LIMIT],
        letter=render_letter(questions),
    )


def process_text(
    text: str,
    source: SourceInfo | None = None,
    today: date | None = None,
    use_llm: bool = True,
) -> ProcessResult:
    source = source or SourceInfo(kind="text", chars=len(text))
    warnings: list[str] = []
    extractor = "rules"

    if not text.strip():
        return finalize(
            OrderDraft(), text, source, today,
            extractor="rules",
            warnings=["Пустой документ: извлекать нечего"],
        )

    extraction: LlmExtraction | None = None
    if use_llm:
        try:
            extraction, llm_warnings = extract_with_llm(text, today=today)
            warnings += llm_warnings
            extractor = "llm"
        except LlmError as exc:
            logger.warning("LLM недоступна, переходим на правила: %s", exc)
            warnings.append(f"Модель не отработала ({exc}) — заявка разобрана на правилах")

    if extraction is None:
        extraction = extract_with_rules(text, today=today)
        extractor = "rules"

    return finalize(
        to_draft(extraction), text, source, today, extractor=extractor, warnings=warnings
    )


def process_upload(
    filename: str | None,
    data: bytes,
    today: date | None = None,
    use_llm: bool = True,
) -> ProcessResult:
    text, source = detect_and_extract(filename, data)
    if source.is_scan:
        # Текста нет — обращаться к модели незачем, она может только нафантазировать.
        return finalize(
            OrderDraft(), text, source, today, extractor="rules", warnings=[]
        )
    return process_text(text, source=source, today=today, use_llm=use_llm)
