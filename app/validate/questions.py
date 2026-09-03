"""Генерация уточняющих вопросов.

Вопросы собираются шаблонами, а не моделью, и это осознанно: менеджер должен
получать предсказуемый и одинаковый набор формулировок, а не каждый раз новый
пересказ. Модель уже отработала на этапе извлечения.
"""
from __future__ import annotations

from app.models import (
    FIELD_LABELS,
    FieldStatus,
    OrderDraft,
    Question,
    REQUIRED_FIELDS,
    SourceInfo,
)

# Чем меньше число, тем выше вопрос в списке.
PRIORITY = {
    "station_from": 10,
    "station_to": 11,
    "cargo": 20,
    "volume": 30,
    "period": 40,
    "company": 50,
    "budget": 60,
    "loading_terms": 70,
    "unloading_terms": 71,
}

MISSING_TEMPLATES = {
    "company": "Какая компания выступает заказчиком перевозки (грузоотправитель, плательщик)?",
    "station_from": "С какой станции отправления планируется перевозка? Укажите название станции или код ЕСР.",
    "station_to": "На какую станцию назначения идёт груз? Укажите название станции или код ЕСР.",
    "cargo": "Какой груз перевозим? Нужно наименование по номенклатуре ЕТСНГ.",
    "volume": "Какой объём перевозки: сколько вагонов или сколько тонн?",
    "period": "На какой период планируется перевозка? Укажите даты начала и окончания отгрузок.",
}

AMBIGUOUS_PREFIX = {
    "company": "Уточните заказчика перевозки",
    "station_from": "Уточните станцию отправления",
    "station_to": "Уточните станцию назначения",
    "cargo": "Уточните наименование груза",
    "volume": "Уточните объём перевозки",
    "period": "Уточните период перевозки",
    "budget": "Уточните условия по ставке",
    "loading_terms": "Уточните условия погрузки",
    "unloading_terms": "Уточните условия выгрузки",
}


def _options(field) -> list[str]:
    options: list[str] = []
    for candidate in field.candidates:
        label = candidate.value
        if candidate.code:
            label += f" (код {candidate.code}"
            label += f", {candidate.hint})" if candidate.hint else ")"
        elif candidate.hint:
            label += f" ({candidate.hint})"
        options.append(label)
    return options


def build_questions(draft: OrderDraft, source: SourceInfo | None = None) -> list[Question]:
    questions: list[Question] = []

    if source is not None and source.is_scan:
        questions.append(
            Question(
                field="__source__",
                text=(
                    "Из присланного файла не удалось прочитать текст — похоже, это скан "
                    "или изображение. Пришлите, пожалуйста, заявку текстом или в "
                    "редактируемом формате."
                ),
                priority=1,
                blocking=True,
                reason="missing",
            )
        )

    for name, field in draft.items():
        required = name in REQUIRED_FIELDS
        priority = PRIORITY.get(name, 100)

        if field.status == FieldStatus.missing:
            # Необязательные поля не спрашиваем: их отсутствие — не проблема заявки.
            if not required:
                continue
            questions.append(
                Question(
                    field=name,
                    text=MISSING_TEMPLATES.get(
                        name, f"Не указано: {FIELD_LABELS.get(name, name)}. Уточните, пожалуйста."
                    ),
                    priority=priority,
                    blocking=True,
                    reason="missing",
                )
            )
            continue

        if field.status in (FieldStatus.ambiguous, FieldStatus.low_confidence):
            prefix = AMBIGUOUS_PREFIX.get(name, f"Уточните: {FIELD_LABELS.get(name, name)}")
            detail = field.comment or "значение допускает несколько трактовок"
            raw = field.raw or (field.value if isinstance(field.value, str) else None)
            quoted = f" В заявке указано: «{raw}»." if raw else ""
            text = f"{prefix}. {detail}.{quoted}"
            options = _options(field)
            if options:
                text += " Какой вариант верный?"
            questions.append(
                Question(
                    field=name,
                    text=text,
                    options=options,
                    priority=priority,
                    blocking=required,
                    reason="ambiguous",
                )
            )

    questions.sort(key=lambda q: (not q.blocking, q.priority))
    return questions


def render_letter(questions: list[Question]) -> str:
    """Готовый текст письма клиенту — то, что менеджер отправит не редактируя."""
    if not questions:
        return "Заявка полная, уточнений не требуется."
    lines = [
        "Здравствуйте!",
        "",
        "Спасибо за заявку. Чтобы подготовить расчёт, уточните, пожалуйста:",
        "",
    ]
    for index, question in enumerate(questions, start=1):
        lines.append(f"{index}. {question.text}")
        for option in question.options:
            lines.append(f"   — {option}")
    lines += ["", "После вашего ответа сразу вернёмся со ставкой и сроками.", "", "С уважением,"]
    return "\n".join(lines)
