from __future__ import annotations


def decode(data: bytes) -> str:
    """Заявки часто приходят в cp1251 — пробуем кодировки по очереди."""
    for encoding in ("utf-8", "utf-8-sig", "cp1251", "koi8-r"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract(data: bytes) -> tuple[str, list[str]]:
    return decode(data), []
