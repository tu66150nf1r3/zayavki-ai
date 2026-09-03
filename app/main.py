"""FastAPI-приложение: REST + одна страница интерфейса."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import SAMPLES_DIR, settings
from app.models import FIELD_LABELS, ProcessResult
from app.pipeline import process_text, process_upload
from app.samples import SAMPLE_TITLES, list_samples
from app.answers import apply_answers

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

BASE_DIR = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

app = FastAPI(
    title="Разбор входящих заявок на перевозку",
    description=(
        "Прототип: читает заявку в любом формате, извлекает поля для сделки и "
        "формирует вопросы там, где данных не хватает или они неоднозначны."
    ),
    version="0.1.0",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


class TextRequest(BaseModel):
    text: str
    use_llm: bool = True


class SampleRequest(BaseModel):
    name: str
    use_llm: bool = True


class AnswersRequest(BaseModel):
    result: ProcessResult
    answers: dict[str, str]


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "samples": list_samples(),
            "llm_enabled": settings.llm_enabled,
            "model": settings.model if settings.llm_enabled else None,
            "labels": FIELD_LABELS,
        },
    )


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "llm_enabled": settings.llm_enabled,
        "model": settings.model if settings.llm_enabled else None,
        "samples": len(list_samples()),
    }


@app.post("/api/parse", response_model=ProcessResult)
async def parse(request: Request):
    """Принимает либо JSON {"text": "..."}, либо multipart с файлом."""
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("application/json"):
        payload = TextRequest.model_validate(await request.json())
        if not payload.text.strip():
            raise HTTPException(status_code=400, detail="Пустой текст заявки")
        return process_text(payload.text, use_llm=payload.use_llm)

    form = await request.form()
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        text = str(form.get("text") or "")
        if not text.strip():
            raise HTTPException(status_code=400, detail="Не передан ни файл, ни текст заявки")
        return process_text(text, use_llm=form.get("use_llm") != "false")

    data = await upload.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Файл больше 10 МБ")
    try:
        return process_upload(
            upload.filename, data, use_llm=form.get("use_llm") != "false"
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/samples")
async def samples():
    return list_samples()


@app.post("/api/parse-sample", response_model=ProcessResult)
def parse_sample(payload: SampleRequest):
    if payload.name not in SAMPLE_TITLES:
        raise HTTPException(status_code=404, detail="Демо-заявка не найдена")
    path = SAMPLES_DIR / payload.name
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Файл {payload.name} не создан — запустите python samples/make_samples.py",
        )
    return process_upload(path.name, path.read_bytes(), use_llm=payload.use_llm)


@app.post("/api/answer", response_model=ProcessResult)
def answer(payload: AnswersRequest):
    """Замыкает цикл: ответы менеджера вписываются в поля и всё пересчитывается.

    Повторного обращения к модели не происходит — ответ пользователя надёжнее.
    """
    if not payload.answers:
        raise HTTPException(status_code=400, detail="Ответы не переданы")
    return apply_answers(payload.result, payload.answers)
