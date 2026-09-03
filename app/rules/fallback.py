"""Экстрактор на правилах — без сети и без модели.

Две роли:
  1. Резерв. Нет ключа, API упал, модель вернула мусор — прототип продолжает
     работать, просто честно помечает в интерфейсе, чем именно извлекал.
  2. Перекрёстная проверка. Числа, найденные регулярками прямо в тексте,
     сравниваются с числами, которые вернула модель (см. validate/rules.py).

Возвращает ту же структуру LlmExtraction, что и модель, — дальше по конвейеру
разницы нет.
"""
from __future__ import annotations

import calendar
import re
from datetime import date
from difflib import SequenceMatcher

from app.directory.lookup import _cargo, _stations, normalize
from app.llm.schema import LlmBudget, LlmExtraction, LlmField, LlmPeriod, LlmVolume

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
MONTH_RE = "|".join(
    ["январ\\w*", "феврал\\w*", "март\\w*", "апрел\\w*", "мая", "май", "июн\\w*",
     "июл\\w*", "август\\w*", "сентябр\\w*", "октябр\\w*", "ноябр\\w*", "декабр\\w*"]
)

COMPANY_RE = re.compile(
    r"(?:ООО|ОАО|ЗАО|ПАО|АО|ИП|ТД|ТК|ГК)\s*[«\"']?([А-ЯЁA-Z][^»\"'\n,;]{1,60})[»\"']?",
    re.UNICODE,
)
WAGON_RE = re.compile(r"(\d[\d\s ]*)\s*(?:полу)?ваг(?:он\w*|\.)?", re.IGNORECASE)
TONS_RE = re.compile(r"(\d[\d\s ]*(?:[.,]\d+)?)\s*(?:тонн\w*|тн\b|т\.|т\b)", re.IGNORECASE)
DATE_RE = re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})\b")
DAY_MONTH_RE = re.compile(rf"\b(\d{{1,2}})\s*(?:-|—|–|по)?\s*(\d{{1,2}})?\s*({MONTH_RE})", re.IGNORECASE)
MONEY_RE = re.compile(
    r"(\d[\d\s ]{2,}(?:[.,]\d+)?)\s*(?:руб\w*|₽|р\.)", re.IGNORECASE
)
VAGUE_PERIOD_RE = re.compile(
    r"ближайш\w+ время|как можно скорее|в течение\w*\s+месяц\w*|асап|asap|"
    r"ежемесячн\w*|регулярн\w*|постоянн\w*|по мере готовности",
    re.IGNORECASE,
)

FROM_MARKERS = ("из ", "со ", "с ", "от ", "отправлен", "погрузк", "станция отправления")
TO_MARKERS = ("на ", "в ", "до ", "назначен", "выгрузк", "получател", "станция назначения")


def _clean_number(text: str) -> float:
    return float(text.replace(" ", "").replace(" ", "").replace(",", "."))


def _quote(text: str, start: int, end: int, pad: int = 25) -> str:
    """Цитата вокруг найденного места — тот же evidence, что требуется от модели."""
    left = max(0, start - pad)
    right = min(len(text), end + pad)
    return text[left:right].strip()


def _find_directory_hits(text: str) -> list[tuple[int, str, str]]:
    """Ищет в тексте станции из справочника: (позиция, написание из справочника, станция).

    Поиск по словам, а не подстрокой: в заявках пишут «из Москвы», «до Находки».
    """
    words = _tokenize(text)
    raw_hits: list[tuple[int, int, str, str]] = []  # начало, конец, написание, станция
    for station in _stations():
        for variant in [station["name"], *station["aliases"], station["city"]]:
            variant_words = normalize(variant).split()
            if not variant_words or len(normalize(variant)) < 4:
                continue
            span = _find_phrase(words, variant_words)
            if span:
                raw_hits.append((span[0], span[1], variant, station["name"]))

    # Длинное написание побеждает короткое на том же месте: «Находка-Восточная»
    # не должна схлопнуться в «Находка», потому что та стоит выше в справочнике.
    raw_hits.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    result: list[tuple[int, str, str]] = []
    taken: list[tuple[int, int]] = []
    seen: set[str] = set()
    for start, end, variant, name in raw_hits:
        if any(start < t_end and end > t_start for t_start, t_end in taken):
            continue
        if name in seen:
            continue
        taken.append((start, end))
        seen.add(name)
        result.append((start, variant, name))
    result.sort(key=lambda item: item[0])
    return result


def _direction(text: str, position: int) -> str | None:
    window = text[max(0, position - 30) : position].lower()
    from_score = max((window.rfind(m) for m in FROM_MARKERS), default=-1)
    to_score = max((window.rfind(m) for m in TO_MARKERS), default=-1)
    if from_score == to_score == -1:
        return None
    return "from" if from_score > to_score else "to"


def _extract_stations(text: str) -> tuple[LlmField, LlmField]:
    station_from = LlmField()
    station_to = LlmField()
    hits = _find_directory_hits(text)
    if not hits:
        return station_from, station_to

    assigned: dict[str, tuple[int, str]] = {}
    unassigned: list[tuple[int, str]] = []
    for position, variant, _name in hits:
        direction = _direction(text, position)
        if direction and direction not in assigned:
            assigned[direction] = (position, variant)
        else:
            unassigned.append((position, variant))

    # Направление не размечено предлогами — берём порядок появления в тексте.
    if "from" not in assigned and unassigned:
        assigned["from"] = unassigned.pop(0)
    if "to" not in assigned and unassigned:
        assigned["to"] = unassigned.pop(0)

    for key, target in (("from", station_from), ("to", station_to)):
        if key in assigned:
            position, variant = assigned[key]
            target.value = variant
            target.evidence = _quote(text, position, position + len(variant))
    return station_from, station_to


def _word_matches(text_word: str, variant_word: str) -> bool:
    """Сравнение с поправкой на падежи: «щебня» ≈ «щебень», «угля» ≈ «уголь».

    Полноценная лемматизация в прототип не тащится (это лишняя зависимость),
    поэтому используется дешёвая эвристика: совпадает начало слова и слова
    достаточно похожи целиком.
    """
    if text_word == variant_word:
        return True
    # Короткие слова сравниваем только точно — на них любая эвристика шумит.
    if len(text_word) < 5 or len(variant_word) < 5:
        return False
    # Падежное окончание — это один-два символа. Большая разница в длине означает
    # другое слово: «металл» не должен матчиться в «металлолом».
    if abs(len(text_word) - len(variant_word)) > 2:
        return False
    if text_word[:3] != variant_word[:3]:
        return False
    # Порог подобран так, чтобы «щебня»≈«щебень» проходило, а «примерно»≈«Приморск» — нет.
    # Экстрактор резервный: пропустить поле лучше, чем подставить чужое значение.
    return SequenceMatcher(None, text_word, variant_word).ratio() >= 0.7


def _find_phrase(
    words: list[tuple[int, str]], variant_words: list[str]
) -> tuple[int, int] | None:
    """Границы фразы в тексте, если все слова варианта нашлись подряд."""
    if not variant_words:
        return None
    for index in range(len(words) - len(variant_words) + 1):
        if all(
            _word_matches(words[index + offset][1], variant_word)
            for offset, variant_word in enumerate(variant_words)
        ):
            start = words[index][0]
            last_position, last_word = words[index + len(variant_words) - 1]
            return start, last_position + len(last_word)
    return None


def _tokenize(text: str) -> list[tuple[int, str]]:
    lowered = text.lower().replace("ё", "е")
    return [(m.start(), m.group()) for m in re.finditer(r"[а-яa-z0-9-]+", lowered)]


def _extract_cargo(text: str) -> LlmField:
    words = _tokenize(text)
    data = _cargo()

    best: tuple[int, tuple[int, int], str] | None = None  # вес, границы, наименование
    for item in data["cargo"]:
        for variant in [item["name"], *item["aliases"]]:
            variant_words = normalize(variant).split()
            if not variant_words or len(normalize(variant)) < 4:
                continue
            span = _find_phrase(words, variant_words)
            if span:
                weight = len(variant_words) * 100 + len(variant)
                if best is None or weight > best[0]:
                    best = (weight, span, item["name"])
    if best:
        _, span, name = best
        return LlmField(value=name, evidence=_quote(text, span[0], span[1]))

    for generic in data["too_generic"]:
        span = _find_phrase(words, normalize(generic["term"]).split())
        if span:
            return LlmField(
                value=generic["term"],
                evidence=_quote(text, span[0], span[1]),
                ambiguous=True,
                comment="Найдено общее наименование груза",
            )
    return LlmField()


def _extract_volume(text: str) -> LlmVolume:
    volume = LlmVolume()
    match = WAGON_RE.search(text)
    if match:
        volume.wagons = int(_clean_number(match.group(1)))
        volume.raw = match.group(0).strip()
        volume.evidence = _quote(text, match.start(), match.end())
    tons = TONS_RE.search(text)
    if tons:
        volume.tons = _clean_number(tons.group(1))
        volume.raw = volume.raw or tons.group(0).strip()
        volume.evidence = volume.evidence or _quote(text, tons.start(), tons.end())
    if re.search(r"в месяц|ежемесячн|/мес", text, re.IGNORECASE):
        volume.per_period = "в месяц"
    elif re.search(r"в сутки|/сут", text, re.IGNORECASE):
        volume.per_period = "в сутки"
    elif volume.wagons or volume.tons:
        volume.per_period = "всего"
    if re.search(r"примерн\w*|около|порядка|~|\d+\s*[-–—]\s*\d+\s*ваг", text, re.IGNORECASE):
        volume.ambiguous = True
        volume.comment = "Объём указан приблизительно или вилкой"
    return volume


def _month_number(word: str) -> int | None:
    word = word.lower()
    for stem, number in MONTHS.items():
        if word.startswith(stem):
            return number
    return None


def _extract_period(text: str, today: date) -> LlmPeriod:
    period = LlmPeriod()

    dates = DATE_RE.findall(text)
    if dates:
        parsed: list[date] = []
        for day, month, year in dates:
            year_int = int(year)
            if year_int < 100:
                year_int += 2000
            try:
                parsed.append(date(year_int, int(month), int(day)))
            except ValueError:
                continue
        if parsed:
            parsed.sort()
            period.date_from = parsed[0].isoformat()
            period.date_to = parsed[-1].isoformat()
            match = DATE_RE.search(text)
            period.raw = match.group(0)
            period.evidence = _quote(text, match.start(), match.end())
            return period

    match = DAY_MONTH_RE.search(text)
    if match:
        day_from, day_to, month_word = match.groups()
        month = _month_number(month_word)
        if month:
            year = today.year if month >= today.month else today.year + 1
            try:
                start = date(year, month, int(day_from))
                end = date(year, month, int(day_to or day_from))
                period.date_from = start.isoformat()
                period.date_to = end.isoformat()
                period.raw = match.group(0)
                period.evidence = _quote(text, match.start(), match.end())
                if not re.search(r"\b20\d{2}\b", period.evidence or ""):
                    period.ambiguous = True
                    period.comment = "Год в заявке не указан, подставлен ближайший"
                return period
            except ValueError:
                pass

    bare_month = re.search(rf"\b({MONTH_RE})\b", text, re.IGNORECASE)
    if bare_month:
        month = _month_number(bare_month.group(1))
        if month:
            year = today.year if month >= today.month else today.year + 1
            last_day = calendar.monthrange(year, month)[1]
            period.date_from = date(year, month, 1).isoformat()
            period.date_to = date(year, month, last_day).isoformat()
            period.raw = bare_month.group(0)
            period.evidence = _quote(text, bare_month.start(), bare_month.end())
            period.ambiguous = True
            period.comment = "Назван месяц целиком, конкретные даты отгрузки неизвестны"
            return period

    vague = VAGUE_PERIOD_RE.search(text)
    if vague:
        period.raw = vague.group(0)
        period.evidence = _quote(text, vague.start(), vague.end())
        period.ambiguous = True
        period.comment = "Период назван расплывчато"
    return period


def _extract_budget(text: str) -> LlmBudget:
    budget = LlmBudget()
    match = MONEY_RE.search(text)
    if not match:
        if re.search(r"по рынку|рыночн\w+ ставк|дайте ставку|ваше предложение", text, re.IGNORECASE):
            budget.ambiguous = True
            budget.comment = "Ставка не названа, клиент ждёт предложение"
        return budget

    budget.amount = _clean_number(match.group(1))
    budget.currency = "RUB"
    budget.raw = match.group(0).strip()
    budget.evidence = _quote(text, match.start(), match.end(), pad=40)

    tail = text[match.end() : match.end() + 60].lower()
    head = text[max(0, match.start() - 40) : match.start()].lower()
    context = head + " " + tail
    if "за вагон" in context or "/ваг" in context or "за ваг" in context:
        budget.basis = "за вагон"
    elif "за тонн" in context or "/т" in context:
        budget.basis = "за тонну"
    elif "за рейс" in context:
        budget.basis = "за рейс"
    if "без ндс" in context:
        budget.vat = "без НДС"
    elif "с ндс" in context or "вкл. ндс" in context or "включая ндс" in context:
        budget.vat = "с НДС"
    return budget


def _extract_terms(text: str, keywords: tuple[str, ...]) -> LlmField:
    for line in text.splitlines():
        lowered = line.lower()
        if any(word in lowered for word in keywords):
            cleaned = line.strip()
            if 3 < len(cleaned) < 200:
                return LlmField(value=cleaned, evidence=cleaned)
    return LlmField()


def extract_with_rules(text: str, today: date | None = None) -> LlmExtraction:
    today = today or date.today()
    company = LlmField()
    match = COMPANY_RE.search(text)
    if match:
        # Собираем название обратно, чтобы не оставить висящую кавычку.
        legal_form = match.group(0).split()[0]
        name = match.group(1).strip(" \"'«»,;.")
        company.value = f"{legal_form} «{name}»"
        company.evidence = _quote(text, match.start(), match.end())

    station_from, station_to = _extract_stations(text)
    return LlmExtraction(
        company=company,
        station_from=station_from,
        station_to=station_to,
        cargo=_extract_cargo(text),
        volume=_extract_volume(text),
        period=_extract_period(text, today),
        budget=_extract_budget(text),
        loading_terms=_extract_terms(text, ("погрузк", "загрузк", "налив")),
        unloading_terms=_extract_terms(text, ("выгрузк", "разгрузк", "слив")),
    )
