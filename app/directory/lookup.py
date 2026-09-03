"""Нормализация станций и грузов по справочникам.

Здесь принимается решение «однозначно или нет» — и оно принимается кодом,
а не моделью. Ровно один кандидат — значение уходит в сделку с кодом ЕСР/ЕТСНГ.
Ноль или больше одного — поле помечается ambiguous, и пользователю уходит вопрос.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent

# Порог похожести для нечёткого поиска: ниже — уже не опечатка, а другой объект.
FUZZY_THRESHOLD = 0.78
# Выше этого совпадение считаем уверенным и не переспрашиваем.
FUZZY_CONFIDENT = 0.93

_PREFIX_RE = re.compile(
    r"^(?:ст\.?|станция|станции|г\.?|город|порт|до|из|на)\s+", re.IGNORECASE
)
_CLEAN_RE = re.compile(r"[^\w\s-]", re.UNICODE)


def normalize(value: str) -> str:
    """Приводит написание к сравнимому виду: регистр, ё/е, префиксы «ст.», «порт»."""
    text = (value or "").strip().lower().replace("ё", "е")
    text = _CLEAN_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Префиксы срезаем по одному: «до ст. Находка» → «находка».
    for _ in range(3):
        stripped = _PREFIX_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    return text


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _contains_phrase(haystack: str, needle: str) -> bool:
    """Вхождение по границам слов: «нефтепродукты» не должны совпасть с «нефть»."""
    if not needle:
        return False
    return re.search(rf"(?:^|\s){re.escape(needle)}(?:\s|$)", haystack) is not None


@dataclass
class MatchCandidate:
    value: str
    code: str | None = None
    hint: str | None = None
    score: float = 1.0
    payload: dict = field(default_factory=dict)


@dataclass
class MatchResult:
    status: str                       # "ok" | "ambiguous" | "not_found"
    value: str | None = None
    code: str | None = None
    candidates: list[MatchCandidate] = field(default_factory=list)
    comment: str | None = None
    score: float = 0.0
    payload: dict = field(default_factory=dict)


@lru_cache(maxsize=1)
def _stations() -> list[dict]:
    with (DATA_DIR / "stations.json").open(encoding="utf-8") as fh:
        return json.load(fh)["stations"]


@lru_cache(maxsize=1)
def _cargo() -> dict:
    with (DATA_DIR / "cargo.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def _station_candidate(station: dict, score: float = 1.0) -> MatchCandidate:
    return MatchCandidate(
        value=station["name"],
        code=station["esr"],
        hint=f"{station['road']}, {station['city']}",
        score=score,
        payload=station,
    )


def lookup_station(query: str) -> MatchResult:
    key = normalize(query)
    if not key:
        return MatchResult(status="not_found", comment="Пустое значение")

    stations = _stations()

    # 1. Точное совпадение по названию или алиасу.
    exact = [
        s
        for s in stations
        if normalize(s["name"]) == key or any(normalize(a) == key for a in s["aliases"])
    ]
    if len(exact) == 1:
        station = exact[0]
        return MatchResult(
            status="ok",
            value=station["name"],
            code=station["esr"],
            score=1.0,
            payload=station,
            candidates=[_station_candidate(station)],
        )
    if len(exact) > 1:
        return MatchResult(
            status="ambiguous",
            candidates=[_station_candidate(s) for s in exact],
            comment=f"Под названием «{query}» в справочнике несколько станций",
        )

    # 2. Совпадение по городу — типовой случай «отправление из Москвы».
    by_city = [s for s in stations if normalize(s["city"]) == key]
    if len(by_city) == 1:
        station = by_city[0]
        return MatchResult(
            status="ok",
            value=station["name"],
            code=station["esr"],
            score=0.95,
            payload=station,
            candidates=[_station_candidate(station)],
            comment=f"Указан город «{query}», в нём одна грузовая станция",
        )
    if len(by_city) > 1:
        return MatchResult(
            status="ambiguous",
            candidates=[_station_candidate(s) for s in by_city],
            comment=(
                f"Указан город «{query}», а не станция: "
                f"в справочнике {len(by_city)} станций этого узла"
            ),
        )

    # 3. Нечёткий поиск — опечатки и сокращения.
    scored: list[tuple[float, dict]] = []
    for station in stations:
        variants = [station["name"], station["city"], *station["aliases"]]
        best = max(_similar(key, normalize(v)) for v in variants)
        if best >= FUZZY_THRESHOLD:
            scored.append((best, station))

    if scored:
        scored.sort(key=lambda pair: pair[0], reverse=True)
        top_score, top_station = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if top_score >= FUZZY_CONFIDENT and top_score - runner_up > 0.05:
            return MatchResult(
                status="ok",
                value=top_station["name"],
                code=top_station["esr"],
                score=top_score,
                payload=top_station,
                candidates=[_station_candidate(top_station, top_score)],
                comment=f"Сопоставлено по написанию «{query}»",
            )
        return MatchResult(
            status="ambiguous",
            candidates=[_station_candidate(s, sc) for sc, s in scored[:6]],
            comment=f"Точного совпадения для «{query}» нет, похожие варианты в справочнике",
        )

    return MatchResult(
        status="not_found",
        comment=f"Станция «{query}» не найдена в справочнике",
    )


def lookup_cargo(query: str) -> MatchResult:
    key = normalize(query)
    if not key:
        return MatchResult(status="not_found", comment="Пустое значение")

    data = _cargo()
    items = data["cargo"]

    def as_candidate(item: dict, score: float = 1.0) -> MatchCandidate:
        return MatchCandidate(
            value=item["name"],
            code=item["code"],
            hint=item["wagon_type"],
            score=score,
            payload=item,
        )

    # 1. Точное совпадение по наименованию или алиасу.
    exact = [
        i
        for i in items
        if normalize(i["name"]) == key or any(normalize(a) == key for a in i["aliases"])
    ]
    if len(exact) == 1:
        item = exact[0]
        return MatchResult(
            status="ok", value=item["name"], code=item["code"], score=1.0,
            payload=item, candidates=[as_candidate(item)],
        )

    # 2. Вхождение по границам слов: «уголь энергетический марки Д», «щебень фр. 20-40».
    # Проверяется раньше общих слов: конкретное наименование внутри фразы важнее,
    # чем то, что во фразе попалось слово «уголь».
    contained = [
        i
        for i in items
        if _contains_phrase(key, normalize(i["name"]))
        or any(
            len(normalize(a)) >= 4 and _contains_phrase(key, normalize(a))
            for a in i["aliases"]
        )
    ]
    if len(contained) == 1:
        item = contained[0]
        return MatchResult(
            status="ok", value=item["name"], code=item["code"], score=0.9,
            payload=item, candidates=[as_candidate(item, 0.9)],
            comment=f"Сопоставлено с номенклатурой по фрагменту «{query}»",
        )
    if len(contained) > 1:
        return MatchResult(
            status="ambiguous",
            candidates=[as_candidate(i, 0.6) for i in contained[:8]],
            comment=f"«{query}» подходит под несколько позиций номенклатуры",
        )

    # 3. Слишком общее наименование — формально «есть», но для сделки непригодно.
    for generic in data["too_generic"]:
        term = normalize(generic["term"])
        if key == term or _contains_phrase(key, term):
            wanted = generic.get("codes") or []
            by_code = {i["code"]: i for i in items}
            related = [as_candidate(by_code[c], 0.5) for c in wanted if c in by_code]
            return MatchResult(
                status="ambiguous",
                candidates=related[:8],
                comment=f"«{query}» — слишком общее наименование: {generic['hint']}",
            )

    # 4. Нечёткий поиск.
    scored: list[tuple[float, dict]] = []
    for item in items:
        variants = [item["name"], *item["aliases"]]
        best = max(_similar(key, normalize(v)) for v in variants)
        if best >= FUZZY_THRESHOLD:
            scored.append((best, item))
    if scored:
        scored.sort(key=lambda pair: pair[0], reverse=True)
        top_score, top_item = scored[0]
        if top_score >= FUZZY_CONFIDENT:
            return MatchResult(
                status="ok", value=top_item["name"], code=top_item["code"],
                score=top_score, payload=top_item,
                candidates=[as_candidate(top_item, top_score)],
                comment=f"Сопоставлено по написанию «{query}»",
            )
        return MatchResult(
            status="ambiguous",
            candidates=[as_candidate(i, sc) for sc, i in scored[:6]],
            comment=f"Точного совпадения для «{query}» нет, похожие позиции номенклатуры",
        )

    return MatchResult(
        status="not_found",
        comment=f"Груз «{query}» не найден в справочнике ЕТСНГ",
    )


def tons_per_wagon(cargo_payload: dict | None) -> float | None:
    """Типовая загрузка вагона — нужна для сверки тонн и количества вагонов."""
    if not cargo_payload:
        return None
    value = cargo_payload.get("tons_per_wagon")
    return float(value) if value else None
