"""Проверки, защищающие сделку от выдумок модели."""
from datetime import date

from app.models import ExtractedField, FieldStatus, OrderDraft
from app.validate.rules import verify

SOURCE = (
    "Просим перевезти щебень со станции Ерунаково на станцию Находка-Восточная, "
    "25 полувагонов, с 10 по 20 октября 2026 года, ставка 95 000 руб."
)


def _ok(value, evidence, **extra):
    return ExtractedField(
        value=value, evidence=evidence, status=FieldStatus.ok, confidence=0.85, **extra
    )


def test_fabricated_quote_downgrades_field():
    """Модель «уверенно» вернула станцию, которой в заявке нет."""
    draft = OrderDraft(
        station_from=_ok("Мурманск", "отправление со станции Мурманск"),
    )
    warnings = verify(draft, SOURCE, today=date(2026, 9, 1))
    assert draft.station_from.status == FieldStatus.ambiguous
    assert any("цитата" in w.lower() for w in warnings)


def test_real_quote_survives():
    draft = OrderDraft(station_from=_ok("Ерунаково", "со станции Ерунаково"))
    verify(draft, SOURCE, today=date(2026, 9, 1))
    assert draft.station_from.status == FieldStatus.ok


def test_number_absent_from_text_is_dropped():
    """Пересчитанный моделью объём (которого в заявке нет) не должен попасть в сделку."""
    draft = OrderDraft(
        volume=_ok({"wagons": 40, "tons": None, "per_period": "всего"}, "25 полувагонов")
    )
    warnings = verify(draft, SOURCE, today=date(2026, 9, 1))
    assert draft.volume.status == FieldStatus.missing
    assert any("40" in w for w in warnings)


def test_number_present_in_text_survives_spacing():
    """«95 000» в тексте и 95000 в JSON — это одно и то же число."""
    draft = OrderDraft(
        budget=_ok(
            {"amount": 95000, "currency": "RUB", "basis": "за вагон", "vat": "без НДС"},
            "ставка 95 000 руб.",
        )
    )
    verify(draft, SOURCE, today=date(2026, 9, 1))
    assert draft.budget.status == FieldStatus.ok


def test_same_origin_and_destination_is_flagged():
    draft = OrderDraft(
        station_from=_ok("Ерунаково", "со станции Ерунаково"),
        station_to=_ok("Ерунаково", "на станцию Ерунаково"),
    )
    verify(draft, SOURCE, today=date(2026, 9, 1))
    assert draft.station_from.status == FieldStatus.ambiguous
    assert draft.station_to.status == FieldStatus.ambiguous


def test_reversed_period_is_flagged():
    draft = OrderDraft(
        period=_ok({"date_from": "2026-10-20", "date_to": "2026-10-10"}, "с 10 по 20 октября")
    )
    verify(draft, SOURCE, today=date(2026, 9, 1))
    assert draft.period.status == FieldStatus.ambiguous
    assert "раньше" in draft.period.comment


def test_tons_inconsistent_with_wagons_is_flagged():
    """25 вагонов щебня — это около 1 700 т, а не 100 000."""
    draft = OrderDraft(
        volume=_ok({"wagons": 25, "tons": 100000, "per_period": "всего"}, "25 полувагонов")
    )
    source = SOURCE + " Всего 100000 тонн."
    verify(draft, source, today=date(2026, 9, 1), tons_per_wagon=69)
    assert draft.volume.status == FieldStatus.ambiguous
    assert "не сходятся" in draft.volume.comment


def test_rate_without_vat_and_basis_needs_clarification():
    draft = OrderDraft(
        budget=_ok({"amount": 95000, "currency": None, "basis": None, "vat": None}, "ставка 95 000 руб.")
    )
    verify(draft, SOURCE, today=date(2026, 9, 1))
    assert draft.budget.status == FieldStatus.ambiguous
    assert "база расчёта" in draft.budget.comment


def test_user_answer_bypasses_quote_check():
    """Ответ менеджера не обязан цитироваться из заявки — он и есть источник."""
    field = _ok("Ховрино", None)
    field.answered_by_user = True
    draft = OrderDraft(station_from=field)
    verify(draft, SOURCE, today=date(2026, 9, 1))
    assert draft.station_from.status == FieldStatus.ok
