"""Конфигурация прототипа. Всё читается из .env, ничего не хардкодится."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLES_DIR = BASE_DIR / "samples"

load_dotenv(BASE_DIR / ".env")


class Settings:
    def __init__(self) -> None:
        self.api_key: str = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
        self.model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro").strip()
        self.timeout: float = float(os.getenv("LLM_TIMEOUT", "180"))
        # deepseek-v4-* — reasoning-модели: основная часть бюджета уходит на
        # reasoning_content (на неоднозначной заявке замерено ~4 300 токенов у pro
        # и до ~10 500 у flash), при маленьком max_tokens ответ обрывается до JSON.
        self.max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "16000"))
        # Пусто — режим модели по умолчанию. "minimal" сокращает размышления
        # и ускоряет ответ примерно в полтора раза, что удобно для демонстрации.
        self.reasoning_effort: str = os.getenv("LLM_REASONING_EFFORT", "").strip()
        self.max_input_chars: int = int(os.getenv("LLM_MAX_INPUT_CHARS", "12000"))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.api_key)


settings = Settings()
