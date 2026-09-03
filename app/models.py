"""Модели результата разбора заявки.

Ключевая идея: поле — не голое значение, а значение + статус + доказательство.
Именно это позволяет отличить «данных нет» от «данные есть, но неоднозначны»
и не превращать домыслы модели в поля сделки.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class FieldStatus(str, Enum):
    ok = "ok"                        # значение извлечено и нормализовано
    missing = "missing"              # в заявке этих данных нет
    ambiguous = "ambiguous"          # данные есть, но допускают несколько трактовок
    low_confidence = "low_confidence"  # извлечено, но не подтверждено проверками


class Candidate(BaseModel):
    """Вариант нормализации из справочника — идёт в UI как вариант ответа."""
    value: str
    code: str | None = None
    hint: str | None = None
    score: float = 1.0


class ExtractedField(BaseModel):
    value: Any | None = None
    raw: str | None = None            # как было написано в заявке
    code: str | None = None           # код ЕСР / ЕТСНГ после нормализации
    evidence: str | None = None       # дословная цитата из исходного документа
    status: FieldStatus = FieldStatus.missing
    confidence: float = 0.0
    candidates: list[Candidate] = Field(default_factory=list)
    comment: str | None = None        # почему missing/ambiguous
    display: str | None = None        # готовая строка для интерфейса
    answered_by_user: bool = False    # значение подтверждено ответом менеджера

    @property
    def filled(self) -> bool:
        return self.value not in (None, "", {}, [])


class Question(BaseModel):
    field: str
    text: str
    options: list[str] = Field(default_factory=list)
    priority: int = 100
    blocking: bool = False            # без ответа сделку заводить нельзя
    reason: Literal["missing", "ambiguous", "unverified"] = "missing"


class SourceInfo(BaseModel):
    filename: str | None = None
    kind: str = "text"                # text | eml | docx | pdf | xlsx
    chars: int = 0
    is_scan: bool = False
    notes: list[str] = Field(default_factory=list)


FIELD_ORDER: list[str] = [
    "company",
    "station_from",
    "station_to",
    "cargo",
    "volume",
    "period",
    "loading_terms",
    "unloading_terms",
    "budget",
]

FIELD_LABELS: dict[str, str] = {
    "company": "Компания",
    "station_from": "Станция отправления",
    "station_to": "Станция назначения",
    "cargo": "Груз",
    "volume": "Объём / количество вагонов",
    "period": "Период перевозки",
    "loading_terms": "Условия погрузки",
    "unloading_terms": "Условия выгрузки",
    "budget": "Бюджет / ставка",
}

# Без этих полей сделку заводить нельзя.
REQUIRED_FIELDS: set[str] = {
    "company",
    "station_from",
    "station_to",
    "cargo",
    "volume",
    "period",
}


class OrderDraft(BaseModel):
    company: ExtractedField = Field(default_factory=ExtractedField)
    station_from: ExtractedField = Field(default_factory=ExtractedField)
    station_to: ExtractedField = Field(default_factory=ExtractedField)
    cargo: ExtractedField = Field(default_factory=ExtractedField)
    volume: ExtractedField = Field(default_factory=ExtractedField)
    period: ExtractedField = Field(default_factory=ExtractedField)
    loading_terms: ExtractedField = Field(default_factory=ExtractedField)
    unloading_terms: ExtractedField = Field(default_factory=ExtractedField)
    budget: ExtractedField = Field(default_factory=ExtractedField)

    def get(self, name: str) -> ExtractedField:
        return getattr(self, name)

    def items(self):
        for name in FIELD_ORDER:
            yield name, self.get(name)


class ProcessResult(BaseModel):
    source: SourceInfo
    draft: OrderDraft
    questions: list[Question] = Field(default_factory=list)
    ready_for_deal: bool = False
    extractor: Literal["llm", "rules"] = "rules"
    warnings: list[str] = Field(default_factory=list)
    text_preview: str = ""            # исходный текст, чтобы UI подсвечивал цитаты
    letter: str = ""                  # готовое письмо клиенту с вопросами
