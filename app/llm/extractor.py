"""Извлечение полей заявки моделью: вызов → валидация → одна попытка починки."""
from __future__ import annotations

import logging
from datetime import date

from pydantic import ValidationError

from app.config import settings
from app.llm.client import LlmClient, LlmError
from app.llm.prompts import repair_prompt, system_prompt, user_prompt
from app.llm.schema import LlmExtraction

logger = logging.getLogger(__name__)


def extract_with_llm(text: str, today: date | None = None) -> tuple[LlmExtraction, list[str]]:
    """Отдаёт разобранную заявку и список предупреждений для показа пользователю.

    Кидает LlmError, если модель недоступна или дважды вернула негодный JSON —
    вызывающий код в этом случае переходит на детерминированный экстрактор.
    """
    if not settings.llm_enabled:
        raise LlmError("Ключ DEEPSEEK_API_KEY не задан")

    warnings: list[str] = []
    payload = text
    if len(payload) > settings.max_input_chars:
        payload = payload[: settings.max_input_chars]
        warnings.append(
            f"Документ обрезан до {settings.max_input_chars} символов — "
            "разобрана только первая часть"
        )

    client = LlmClient()
    messages = [
        {"role": "system", "content": system_prompt(today)},
        {"role": "user", "content": user_prompt(payload)},
    ]

    data, raw = client.complete_json(messages)
    try:
        return LlmExtraction.model_validate(data), warnings
    except ValidationError as exc:
        logger.info("Ответ модели не прошёл валидацию, пробуем починить: %s", exc)

    # Вторая попытка: возвращаем модели её же ответ вместе с текстом ошибки.
    messages += [
        {"role": "assistant", "content": raw},
        {"role": "user", "content": repair_prompt(str(exc))},
    ]
    data, _ = client.complete_json(messages)
    try:
        result = LlmExtraction.model_validate(data)
    except ValidationError as exc2:
        raise LlmError(f"Модель дважды вернула ответ не по схеме: {exc2}") from exc2

    warnings.append("Ответ модели пришлось исправлять повторным запросом")
    return result, warnings
