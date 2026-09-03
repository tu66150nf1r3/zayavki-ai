"""Проверки, которые не зависят от модели.

Это главный барьер против выдумок. Модель может уверенно вернуть станцию,
которой в заявке нет, — но она не сможет подделать цитату, потому что цитата
ищется в исходном тексте посимвольно. Не подтвердилось — поле понижается
в статусе и уходит в вопросы, а не в сделку.
"""
from __future__ import annotations

import re
from datetime import date

from app.models import FieldStatus, OrderDraft, REQUIRED_FIELDS

# Разброс загрузки вагона широкий (род вагона, груз, ограничения по осевой
# нагрузке), поэтому сверка тонн и вагонов ловит только грубые расхождения.
TONS_TOLERANCE = 0.35


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


def _digits_only(text: str) -> str:
    """«95 000 руб.» → «95000руб.» — чтобы искать числа независимо от разрядки."""
    return re.sub(r"(?<=\d)[\s ,](?=\d)", "", text or "")


def _evidence_found(evidence: str, source: str) -> bool:
    """Цитата ищется целиком, а если не нашлась — по значимым словам.

    Модель иногда склеивает цитату через перенос строки или чуть меняет пробелы.
    Полностью придуманную цитату это не пропустит: требуется, чтобы почти все
    слова цитаты нашлись в исходном тексте.
    """
    needle = _squash(evidence)
    if not needle:
        return False
    haystack = _squash(source)
    if needle in haystack:
        return True
    words = [w for w in re.findall(r"[\w-]+", needle) if len(w) > 2]
    if not words:
        return False
    found = sum(1 for word in words if word in haystack)
    return found / len(words) >= 0.8


def _number_in_text(value: float | int | None, source: str) -> bool:
    if value is None:
        return True
    compact = _digits_only(source)
    as_int = int(value)
    if float(value) == as_int and str(as_int) in compact:
        return True
    return str(value).replace(".", ",") in compact or str(value) in compact


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _downgrade(field, comment: str) -> None:
    """Понижает статус поля. Идемпотентно: при повторном разборе (цикл доуточнения)
    один и тот же комментарий не должен накапливаться."""
    field.status = FieldStatus.ambiguous
    if not field.comment:
        field.comment = comment
    elif comment not in field.comment:
        field.comment = f"{field.comment}. {comment}"


def verify(
    draft: OrderDraft,
    source_text: str,
    today: date | None = None,
    tons_per_wagon: float | None = None,
) -> list[str]:
    """Прогоняет проверки, правит статусы полей на месте, возвращает предупреждения.

    tons_per_wagon — типовая загрузка вагона для распознанного груза (из справочника
    ЕТСНГ); без неё сверка тонн и вагонов пропускается.
    """
    today = today or date.today()
    warnings: list[str] = []

    # 1. Цитата обязана находиться в исходном тексте.
    for name, field in draft.items():
        if not field.filled or field.answered_by_user:
            continue
        if not field.evidence:
            _downgrade(field, "Значение не подтверждено цитатой из заявки")
            warnings.append(f"Поле «{name}» извлечено без ссылки на текст заявки")
        elif not _evidence_found(field.evidence, source_text):
            _downgrade(field, "Цитата не найдена в исходном документе — значение не подтверждено")
            field.confidence = min(field.confidence, 0.3)
            warnings.append(
                f"Поле «{name}»: цитата «{field.evidence[:40]}…» отсутствует в заявке"
            )

    # 2. Числа должны встречаться в тексте — модель не должна их пересчитывать.
    volume = draft.volume
    if volume.filled and not volume.answered_by_user and isinstance(volume.value, dict):
        for key, label in (("wagons", "количество вагонов"), ("tons", "объём в тоннах")):
            number = volume.value.get(key)
            if number is not None and not _number_in_text(number, source_text):
                volume.value[key] = None
                _downgrade(volume, f"В заявке нет числа, подтверждающего {label}")
                warnings.append(f"Объём: {label} ({number}) не найден в тексте заявки")
        if not any(v is not None for v in (volume.value.get("wagons"), volume.value.get("tons"))):
            volume.value = None
            volume.status = FieldStatus.missing
            volume.comment = "Объём перевозки в заявке не указан"

    budget = draft.budget
    if budget.filled and not budget.answered_by_user and isinstance(budget.value, dict):
        amount = budget.value.get("amount")
        if amount is not None and not _number_in_text(amount, source_text):
            budget.value["amount"] = None
            _downgrade(budget, "Сумма ставки не найдена в тексте заявки")
            warnings.append(f"Бюджет: сумма {amount} не подтверждается текстом заявки")

    # 3. Обязательное поле без значения — это missing, а не «ок с пустым».
    for name, field in draft.items():
        if not field.filled and field.status not in (FieldStatus.ambiguous,):
            field.status = FieldStatus.missing
            if name in REQUIRED_FIELDS and not field.comment:
                field.comment = "Данных нет в заявке"

    # 4. Маршрут из станции в саму себя.
    origin, destination = draft.station_from, draft.station_to
    if origin.filled and destination.filled and origin.value == destination.value:
        for field in (origin, destination):
            _downgrade(field, "Станции отправления и назначения совпадают")
        warnings.append("Маршрут указан из станции в неё же — вероятно, ошибка в заявке")

    # 5. Логика периода.
    period = draft.period
    if period.filled and isinstance(period.value, dict):
        start = _parse_iso(period.value.get("date_from"))
        end = _parse_iso(period.value.get("date_to"))
        if start and end and end < start:
            _downgrade(period, "Дата окончания периода раньше даты начала")
            warnings.append("Период перевозки задан наоборот: конец раньше начала")
        if end and end < today:
            _downgrade(period, f"Период уже в прошлом относительно {today.isoformat()}")
        if start and not end:
            _downgrade(period, "Указана только дата начала, окончание периода неизвестно")

    # 6. Сверка тонн и вагонов между собой.
    if volume.filled and isinstance(volume.value, dict):
        wagons = volume.value.get("wagons")
        tons = volume.value.get("tons")
        norm = tons_per_wagon
        if wagons and tons and norm:
            expected = wagons * norm
            if abs(tons - expected) / expected > TONS_TOLERANCE:
                _downgrade(
                    volume,
                    f"{wagons} ваг. и {tons:g} т не сходятся: при типовой загрузке "
                    f"{norm:g} т/ваг ожидается около {expected:g} т",
                )
                warnings.append("Объём в тоннах не сходится с количеством вагонов")
        if volume.value.get("per_period") in ("в месяц", "в сутки") and not (
            period.filled and isinstance(period.value, dict) and period.value.get("date_to")
        ):
            _downgrade(
                volume,
                "Объём указан за период (в месяц/в сутки), а срок перевозки не определён — "
                "общий объём посчитать нельзя",
            )

    # 7. Полнота ставки: сумма без базы или без НДС не годится для сделки.
    if budget.filled and isinstance(budget.value, dict) and budget.value.get("amount"):
        # Модель часто уже написала «НДС не указан» — не повторяем это второй раз.
        said = _squash(budget.comment or "")
        missing_parts = []
        if not budget.value.get("basis") and "за вагон" not in said and "за тонн" not in said:
            missing_parts.append("база расчёта (за вагон / за тонну / за рейс)")
        if not budget.value.get("vat") and "ндс" not in said:
            missing_parts.append("НДС (включён или нет)")
        if not budget.value.get("currency") and "валют" not in said:
            missing_parts.append("валюта")
        if missing_parts:
            _downgrade(budget, "Не указано: " + ", ".join(missing_parts))

    return warnings
