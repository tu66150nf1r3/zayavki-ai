"""Схема ответа модели.

Отдельная от OrderDraft модель: сырой ответ LLM нельзя пускать в бизнес-логику,
пока он не прошёл валидацию. Валидаторы намеренно снисходительные — модель
периодически отдаёт строку вместо объекта или число строкой, и ронять на этом
весь разбор незачем.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


def _as_object(value: Any) -> Any:
    """«Москва» → {"value": "Москва"}; None → {}."""
    if value is None:
        return {}
    if isinstance(value, str):
        return {"value": value}
    if isinstance(value, list):
        return {"value": ", ".join(str(v) for v in value if v)}
    return value


def _to_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(" ", " ").replace(" ", "").replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


class LlmField(BaseModel):
    value: str | None = None
    evidence: str | None = None
    ambiguous: bool = False
    comment: str | None = None

    @field_validator("value", "evidence", "comment", mode="before")
    @classmethod
    def _stringify(cls, v: Any) -> Any:
        if v is None or isinstance(v, str):
            return v
        if isinstance(v, list):
            return ", ".join(str(x) for x in v if x) or None
        return str(v)

    @field_validator("value", "evidence", "comment")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("ambiguous", mode="before")
    @classmethod
    def _bool(cls, v: Any) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in {"true", "1", "yes", "да"}
        return bool(v)


class LlmVolume(LlmField):
    wagons: int | None = None
    tons: float | None = None
    per_period: str | None = None
    raw: str | None = None

    @field_validator("wagons", mode="before")
    @classmethod
    def _wagons(cls, v: Any) -> int | None:
        number = _to_number(v)
        return int(number) if number is not None else None

    @field_validator("tons", mode="before")
    @classmethod
    def _tons(cls, v: Any) -> float | None:
        return _to_number(v)

    @field_validator("per_period", "raw", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str | None:
        return None if v is None else str(v).strip() or None


class LlmPeriod(LlmField):
    date_from: str | None = None
    date_to: str | None = None
    raw: str | None = None

    @field_validator("date_from", "date_to", mode="before")
    @classmethod
    def _iso_date(cls, v: Any) -> str | None:
        if not v:
            return None
        text = str(v).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
        # Модель иногда отдаёт 10.10.2026 вопреки схеме — принимаем и приводим.
        match = re.fullmatch(r"(\d{2})[.\-/](\d{2})[.\-/](\d{4})", text)
        if match:
            day, month, year = match.groups()
            return f"{year}-{month}-{day}"
        return None

    @field_validator("raw", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str | None:
        return None if v is None else str(v).strip() or None


class LlmBudget(LlmField):
    amount: float | None = None
    currency: str | None = None
    basis: str | None = None
    vat: str | None = None
    raw: str | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, v: Any) -> float | None:
        return _to_number(v)

    @field_validator("currency", "basis", "vat", "raw", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str | None:
        return None if v is None else str(v).strip() or None


class LlmExtraction(BaseModel):
    company: LlmField = Field(default_factory=LlmField)
    station_from: LlmField = Field(default_factory=LlmField)
    station_to: LlmField = Field(default_factory=LlmField)
    cargo: LlmField = Field(default_factory=LlmField)
    loading_terms: LlmField = Field(default_factory=LlmField)
    unloading_terms: LlmField = Field(default_factory=LlmField)
    volume: LlmVolume = Field(default_factory=LlmVolume)
    period: LlmPeriod = Field(default_factory=LlmPeriod)
    budget: LlmBudget = Field(default_factory=LlmBudget)

    @model_validator(mode="before")
    @classmethod
    def _unwrap(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        # Модель иногда заворачивает результат в {"заявка": {...}} / {"data": {...}}.
        if len(data) == 1:
            only = next(iter(data.values()))
            if isinstance(only, dict) and {"company", "station_from", "cargo"} & set(only):
                data = only
        return {key: _as_object(value) for key, value in data.items()}
