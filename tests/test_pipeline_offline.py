"""Полный конвейер без обращения к модели (use_llm=False) плюс разбор файлов."""
from datetime import date
from pathlib import Path

import pytest

from app.config import SAMPLES_DIR
from app.ingest import detect_and_extract
from app.models import FieldStatus
from app.pipeline import process_text, process_upload

TODAY = date(2026, 9, 3)


def _sample(name: str):
    path = SAMPLES_DIR / name
    if not path.exists():
        pytest.skip(f"{name} не сгенерирован — запустите python samples/make_samples.py")
    return path


def test_full_request_needs_no_questions():
    text = _sample("01_full.txt").read_text(encoding="utf-8")
    result = process_text(text, today=TODAY, use_llm=False)
    assert result.ready_for_deal
    assert result.questions == []
    assert result.draft.station_from.code == "863600"
    assert result.draft.station_to.code == "986200"
    assert result.draft.cargo.code == "231005"
    assert result.draft.volume.value["wagons"] == 25


def test_incomplete_request_asks_about_volume_and_period():
    text = _sample("02_incomplete.txt").read_text(encoding="utf-8")
    result = process_text(text, today=TODAY, use_llm=False)
    assert not result.ready_for_deal
    asked = {q.field for q in result.questions}
    assert {"volume", "period"} <= asked
    assert all(q.blocking for q in result.questions if q.field in {"volume", "period"})


def test_ambiguous_request_offers_directory_options():
    text = _sample("03_ambiguous.txt").read_text(encoding="utf-8")
    result = process_text(text, today=TODAY, use_llm=False)
    assert not result.ready_for_deal

    by_field = {q.field: q for q in result.questions}
    assert "Москва-Товарная-Павелецкая (код 191500, МСК, Москва)" in by_field["station_from"].options
    assert any("Лом чёрных металлов" in option for option in by_field["cargo"].options)


def test_empty_text_produces_no_invented_fields():
    result = process_text("   ", today=TODAY, use_llm=False)
    assert all(not field.filled for _, field in result.draft.items())
    assert not result.ready_for_deal


@pytest.mark.parametrize(
    "name,kind,fragment",
    [
        ("04_letter.eml", "eml", "Саратов"),
        ("05_zayavka.docx", "docx", "Череповец"),
        ("06_plan.xlsx", "xlsx", "Мыски"),
    ],
)
def test_binary_formats_are_reduced_to_text(name, kind, fragment):
    path = _sample(name)
    text, info = detect_and_extract(name, path.read_bytes())
    assert info.kind == kind
    assert not info.is_scan
    assert fragment in text


def test_scan_pdf_is_reported_not_guessed():
    """PDF без текстового слоя: система обязана попросить исходник, а не выдумать поля."""
    path = _sample("07_scan.pdf")
    result = process_upload(path.name, path.read_bytes(), today=TODAY, use_llm=False)

    assert result.source.is_scan
    assert not result.ready_for_deal
    assert all(not field.filled for _, field in result.draft.items())
    assert result.questions[0].field == "__source__"
    assert "скан" in result.questions[0].text
    assert any("OCR" in warning for warning in result.warnings)


def test_letter_is_rendered_for_manager():
    text = _sample("02_incomplete.txt").read_text(encoding="utf-8")
    result = process_text(text, today=TODAY, use_llm=False)
    assert "Здравствуйте!" in result.letter
    assert "1." in result.letter


def test_docx_table_fields_are_extracted():
    path = _sample("05_zayavka.docx")
    result = process_upload(path.name, path.read_bytes(), today=TODAY, use_llm=False)
    assert result.draft.station_from.value == "Череповец-2"
    assert result.draft.station_to.value == "Усть-Луга"
    assert result.draft.period.status == FieldStatus.ok


def test_scan_asks_only_one_meaningful_question():
    """Документ не прочитан — один осмысленный вопрос вместо списка пустых полей."""
    path = _sample("07_scan.pdf")
    result = process_upload(path.name, path.read_bytes(), today=TODAY, use_llm=False)
    assert len(result.questions) == 1
    assert result.questions[0].field == "__source__"
