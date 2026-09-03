"""Тонкая обёртка над DeepSeek (OpenAI-совместимый API).

Особенности провайдера, из-за которых обёртка вообще нужна:
  * строгий response_format=json_schema недоступен, работает только json_object —
    поэтому схема живёт в промпте, а гарантию даёт валидация на нашей стороне;
  * deepseek-v4-* — reasoning-модели, часть бюджета уходит в reasoning_content,
    при маленьком max_tokens ответ обрывается до JSON.
"""
from __future__ import annotations

import json
import logging
import re

from app.config import settings

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LlmError(RuntimeError):
    """Модель недоступна или вернула нечто, что не удалось разобрать."""


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text).strip()
    return text


def parse_json_response(content: str) -> dict:
    """Разбор ответа с подстраховкой: обрамление ```json и текст вокруг JSON."""
    cleaned = _strip_fences(content or "")
    if not cleaned:
        raise LlmError("Модель вернула пустой ответ")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError as exc:
                raise LlmError(f"Ответ модели не является валидным JSON: {exc}") from exc
        raise LlmError("В ответе модели не найден JSON-объект")


class LlmClient:
    def __init__(self) -> None:
        from openai import OpenAI

        self._client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
            max_retries=1,
        )
        self.model = settings.model

    def complete_json(self, messages: list[dict]) -> tuple[dict, str]:
        """Возвращает разобранный JSON и сырой текст ответа."""
        extra: dict = {}
        if settings.reasoning_effort:
            extra["reasoning_effort"] = settings.reasoning_effort
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0,
                max_tokens=settings.max_tokens,
                response_format={"type": "json_object"},
                **extra,
            )
        except Exception as exc:  # noqa: BLE001 — наружу отдаём один тип ошибки
            raise LlmError(f"Вызов модели не удался: {exc}") from exc

        choice = response.choices[0]
        content = choice.message.content or ""
        if choice.finish_reason == "length" and not content.strip():
            raise LlmError(
                "Ответ оборвался по лимиту токенов (модель израсходовала бюджет "
                "на reasoning). Увеличьте LLM_MAX_TOKENS."
            )
        logger.debug("LLM usage: %s", getattr(response, "usage", None))
        return parse_json_response(content), content
