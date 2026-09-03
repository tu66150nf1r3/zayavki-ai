"""Справочники: где однозначно, а где обязан появиться вопрос."""
from app.directory import lookup_cargo, lookup_station, normalize


def test_city_with_many_stations_is_ambiguous():
    result = lookup_station("Москва")
    assert result.status == "ambiguous"
    assert len(result.candidates) >= 5
    assert all(c.code for c in result.candidates)


def test_exact_station_resolves_with_esr_code():
    result = lookup_station("Находка-Восточная")
    assert result.status == "ok"
    assert result.code == "986200"


def test_prefixes_are_stripped():
    assert normalize("ст. Ерунаково") == "ерунаково"
    assert normalize("порт Ванино") == "ванино"
    assert lookup_station("ст. Ерунаково").status == "ok"


def test_typo_gives_candidates_not_a_guess():
    result = lookup_station("Ерунаковo")  # латинская o в конце
    assert result.status in ("ok", "ambiguous")
    assert result.candidates


def test_unknown_station_is_not_invented():
    result = lookup_station("Верхний Задрищенск")
    assert result.status == "not_found"
    assert result.value is None


def test_generic_cargo_requires_clarification():
    result = lookup_cargo("металл")
    assert result.status == "ambiguous"
    names = [c.value for c in result.candidates]
    assert "Лом чёрных металлов" in names
    assert "Прокат чёрных металлов" in names


def test_specific_cargo_beats_generic_word():
    """«уголь» — общее слово, но «уголь энергетический марки Д» уже конкретен."""
    assert lookup_cargo("уголь").status == "ambiguous"
    specific = lookup_cargo("уголь энергетический марки Д")
    assert specific.status == "ok"
    assert specific.code == "161005"


def test_substring_does_not_create_false_match():
    """«нефтепродукты» не должны схлопнуться в «нефть» по вхождению подстроки."""
    result = lookup_cargo("нефтепродукты")
    assert result.status == "ambiguous"
    assert result.value is None


def test_cargo_with_extra_words_still_matches():
    result = lookup_cargo("щебень фр. 20-40")
    assert result.status == "ok"
    assert result.code == "231005"
