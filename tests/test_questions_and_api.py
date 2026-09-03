"""Генерация вопросов, цикл доуточнения и REST API (всё без сети)."""
from datetime import date

from fastapi.testclient import TestClient

from app.answers import apply_answers
from app.main import app
from app.models import Candidate, ExtractedField, FieldStatus, OrderDraft, SourceInfo
from app.pipeline import process_text
from app.validate.questions import build_questions, render_letter

client = TestClient(app)
TODAY = date(2026, 9, 3)


def test_missing_required_field_is_blocking():
    draft = OrderDraft()
    questions = build_questions(draft)
    assert {q.field for q in questions} == {
        "company", "station_from", "station_to", "cargo", "volume", "period",
    }
    assert all(q.blocking for q in questions)


def test_optional_fields_do_not_generate_questions():
    """Условия погрузки и ставка необязательны — их отсутствие не спрашиваем."""
    questions = build_questions(OrderDraft())
    assert not {"loading_terms", "unloading_terms", "budget"} & {q.field for q in questions}


def test_ambiguous_optional_field_is_asked_but_not_blocking():
    draft = OrderDraft(
        budget=ExtractedField(status=FieldStatus.ambiguous, comment="Ставка не названа")
    )
    question = next(q for q in build_questions(draft) if q.field == "budget")
    assert not question.blocking


def test_candidates_become_answer_options():
    draft = OrderDraft(
        station_to=ExtractedField(
            value="Находка",
            status=FieldStatus.ambiguous,
            comment="Несколько станций узла",
            candidates=[
                Candidate(value="Находка", code="986000", hint="ДВОСТ, Находка"),
                Candidate(value="Находка-Восточная", code="986200", hint="ДВОСТ, Находка"),
            ],
        )
    )
    question = next(q for q in build_questions(draft) if q.field == "station_to")
    assert question.options == [
        "Находка (код 986000, ДВОСТ, Находка)",
        "Находка-Восточная (код 986200, ДВОСТ, Находка)",
    ]


def test_scan_question_comes_first():
    questions = build_questions(OrderDraft(), SourceInfo(kind="pdf", is_scan=True))
    assert questions[0].field == "__source__"


def test_letter_without_questions_says_so():
    assert "уточнений не требуется" in render_letter([]).lower()


def test_answers_close_the_loop():
    """Ответы менеджера снимают вопросы и доводят заявку до готовности."""
    text = (
        "Нужно возить металл из Москвы в Находку-Восточную. "
        "Объём примерно 15-20 вагонов в месяц, начать в ближайшее время."
    )
    result = process_text(text, today=TODAY, use_llm=False)
    assert not result.ready_for_deal

    updated = apply_answers(
        result,
        {
            "company": 'ООО "Металлторг"',
            "station_from": "Ховрино (код 192300, МСК, Москва)",
            "station_to": "Находка-Восточная",
            "cargo": "Лом чёрных металлов (код 151500, полувагон)",
            "volume": "60 вагонов",
            "period": "с 01.11.2026 по 30.11.2026",
        },
    )

    assert updated.ready_for_deal
    assert updated.questions == []
    assert updated.draft.station_from.value == "Ховрино"
    assert updated.draft.station_from.code == "192300"
    assert updated.draft.cargo.code == "151500"
    assert updated.draft.volume.value["wagons"] == 60
    assert updated.draft.period.value["date_to"] == "2026-11-30"
    assert updated.draft.company.answered_by_user


def test_unparseable_answer_is_asked_again():
    result = process_text("Заявка на щебень", today=TODAY, use_llm=False)
    updated = apply_answers(result, {"volume": "много"})
    assert updated.draft.volume.status == FieldStatus.ambiguous
    assert any(q.field == "volume" for q in updated.questions)


def test_healthz():
    body = client.get("/healthz").json()
    assert body["status"] == "ok"


def test_index_page_renders():
    response = client.get("/")
    assert response.status_code == 200
    assert "Разбор входящей заявки" in response.text


def test_api_parse_text():
    response = client.post(
        "/api/parse",
        json={"text": "Щебень со ст. Ерунаково на ст. Находка-Восточная, 25 вагонов", "use_llm": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["extractor"] == "rules"
    assert body["draft"]["cargo"]["code"] == "231005"


def test_api_parse_rejects_empty_text():
    assert client.post("/api/parse", json={"text": "  ", "use_llm": False}).status_code == 400


def test_api_parse_file_upload():
    response = client.post(
        "/api/parse",
        files={"file": ("zayavka.txt", "Щебень со ст. Ерунаково, 25 вагонов".encode("utf-8"))},
        data={"use_llm": "false"},
    )
    assert response.status_code == 200
    assert response.json()["source"]["filename"] == "zayavka.txt"


def test_api_rejects_unknown_sample():
    assert client.post("/api/parse-sample", json={"name": "нет.txt"}).status_code == 404


def test_api_rejects_legacy_doc_format():
    response = client.post(
        "/api/parse",
        files={"file": ("old.doc", b"\xd0\xcf\x11\xe0")},
        data={"use_llm": "false"},
    )
    assert response.status_code == 400
    assert ".docx" in response.json()["detail"]


def test_recalculation_does_not_duplicate_comments():
    """Цикл доуточнения можно проходить многократно — комментарии не копятся."""
    result = process_text(
        "Щебень со ст. Ерунаково на ст. Находка-Восточная, 25 вагонов, "
        "10-20 октября 2026, ставка 95 000 руб.",
        today=TODAY,
        use_llm=False,
    )
    first = result.draft.budget.comment
    for _ in range(3):
        result = apply_answers(result, {"company": 'ООО "Тест"'})
    assert result.draft.budget.comment == first
