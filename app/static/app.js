"use strict";

const $ = (id) => document.getElementById(id);
const STATUS_TITLES = {
  ok: "распознано",
  missing: "нет данных",
  ambiguous: "неоднозначно",
  low_confidence: "не подтверждено",
};

let current = null; // последний ProcessResult

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function setStatus(message, isError = false) {
  const box = $("status");
  box.textContent = message;
  box.classList.toggle("error", isError);
  box.hidden = false;
}

function busy(on) {
  $("parse").disabled = on;
  document.querySelectorAll(".chip").forEach((chip) => (chip.disabled = on));
}

async function callApi(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch (_) { /* тело не JSON — оставляем код статуса */ }
    throw new Error(detail);
  }
  return response.json();
}

function renderField(name, field) {
  const label = window.FIELD_LABELS[name] || name;
  const value = field.display || "—";
  const parts = [];
  parts.push(`<div class="field ${field.status}">`);
  parts.push('<div class="field-head">');
  parts.push(`<span class="field-label">${escapeHtml(label)}</span>`);
  parts.push(`<span class="tag ${field.status}">${STATUS_TITLES[field.status] || field.status}</span>`);
  parts.push("</div>");
  parts.push(
    `<div class="field-value${field.display ? "" : " empty"}">${escapeHtml(value)}</div>`
  );
  if (field.comment) parts.push(`<div class="field-note">${escapeHtml(field.comment)}</div>`);
  if (field.answered_by_user) parts.push('<div class="field-user">✓ подтверждено менеджером</div>');
  if (field.evidence) {
    parts.push(`<div class="evidence">из заявки: «${escapeHtml(field.evidence)}»</div>`);
  }
  parts.push("</div>");
  return parts.join("");
}

function renderQuestion(question, index) {
  const parts = [`<div class="question ${question.blocking ? "blocking" : ""}">`];
  const marker = question.blocking ? '<span class="marker">!</span>' : "";
  parts.push(`<div class="question-text">${marker}${index + 1}. ${escapeHtml(question.text)}</div>`);

  if (question.field !== "__source__") {
    if (question.options.length) {
      const options = question.options
        .map((option) => `<option value="${escapeHtml(option)}">${escapeHtml(option)}</option>`)
        .join("");
      parts.push(
        `<select data-field="${escapeHtml(question.field)}">` +
          '<option value="">— выберите вариант или впишите ответ ниже —</option>' +
          options +
        "</select>"
      );
    }
    parts.push(
      `<input type="text" data-field="${escapeHtml(question.field)}" placeholder="Ответ клиента…">`
    );
  }
  parts.push("</div>");
  return parts.join("");
}

function render(result) {
  current = result;
  $("status").hidden = true;
  $("result").hidden = false;

  const blocking = result.questions.filter((q) => q.blocking).length;
  const verdict = $("verdict");
  const engine = result.extractor === "llm" ? "извлечено моделью" : "извлечено правилами (без модели)";
  const source = result.source.filename ? `файл ${result.source.filename}` : "текст из формы";
  if (result.ready_for_deal) {
    verdict.className = "verdict ready";
    verdict.innerHTML =
      "✓ Данных достаточно — можно заводить сделку." +
      `<span class="meta">${escapeHtml(source)} · ${engine} · символов: ${result.source.chars}</span>`;
  } else {
    verdict.className = "verdict blocked";
    verdict.innerHTML =
      `Сделку заводить рано: ${blocking} обязательн${blocking === 1 ? "ый вопрос" : "ых вопроса(ов)"} без ответа.` +
      `<span class="meta">${escapeHtml(source)} · ${engine} · символов: ${result.source.chars}</span>`;
  }

  $("fields").innerHTML = Object.entries(result.draft)
    .map(([name, field]) => renderField(name, field))
    .join("");

  const questionsBox = $("questions");
  if (result.questions.length) {
    questionsBox.innerHTML = result.questions.map(renderQuestion).join("");
    $("qcount").textContent = `${result.questions.length} шт., блокирующих: ${blocking}`;
    $("answerControls").hidden = false;
  } else {
    questionsBox.innerHTML = '<div class="empty-state">Уточнений не требуется — заявка полная.</div>';
    $("qcount").textContent = "";
    $("answerControls").hidden = true;
  }

  const warnings = $("warnings");
  if (result.warnings.length) {
    warnings.hidden = false;
    warnings.innerHTML =
      "<strong>Замечания разбора:</strong><ul>" +
      result.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("") +
      "</ul>";
  } else {
    warnings.hidden = true;
  }

  $("json").textContent = JSON.stringify(result, null, 2);
}

function useLlm() {
  return $("useLlm").checked;
}

async function run(task, label) {
  busy(true);
  setStatus(label);
  $("result").hidden = true;
  try {
    render(await task());
  } catch (error) {
    setStatus(`Ошибка: ${error.message}`, true);
  } finally {
    busy(false);
  }
}

function parseText() {
  const text = $("text").value.trim();
  if (!text) {
    setStatus("Вставьте текст заявки или загрузите файл.", true);
    return;
  }
  run(
    () => callApi("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, use_llm: useLlm() }),
    }),
    "Разбираем заявку…"
  );
}

function parseFile(file) {
  const form = new FormData();
  form.append("file", file);
  form.append("use_llm", useLlm() ? "true" : "false");
  run(
    () => callApi("/api/parse", { method: "POST", body: form }),
    `Читаем ${file.name}…`
  );
}

function parseSample(name) {
  run(
    () => callApi("/api/parse-sample", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, use_llm: useLlm() }),
    }),
    `Разбираем демо-заявку ${name}…`
  );
}

function collectAnswers() {
  const answers = {};
  document.querySelectorAll("#questions [data-field]").forEach((control) => {
    const value = control.value.trim();
    // Текстовое поле важнее выпадающего списка: менеджер мог уточнить вручную.
    if (value) answers[control.dataset.field] = value;
  });
  return answers;
}

function recalc() {
  const answers = collectAnswers();
  if (!Object.keys(answers).length) {
    setStatus("Заполните хотя бы один ответ.", true);
    return;
  }
  run(
    () => callApi("/api/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ result: current, answers }),
    }),
    "Пересчитываем заявку с ответами…"
  );
}

async function copyLetter() {
  if (!current) return;
  const button = $("copyLetter");
  try {
    await navigator.clipboard.writeText(current.letter);
    button.textContent = "Скопировано ✓";
  } catch (_) {
    // clipboard недоступен без https — показываем текст, чтобы скопировать вручную
    window.prompt("Скопируйте письмо:", current.letter);
  }
  setTimeout(() => (button.textContent = "Скопировать письмо с вопросами"), 2000);
}

$("parse").addEventListener("click", parseText);
$("recalc").addEventListener("click", recalc);
$("copyLetter").addEventListener("click", copyLetter);
$("browse").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", (event) => {
  if (event.target.files.length) parseFile(event.target.files[0]);
});
document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => parseSample(chip.dataset.sample));
});

const dropzone = $("dropzone");
["dragenter", "dragover"].forEach((type) =>
  dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.add("over");
  })
);
["dragleave", "drop"].forEach((type) =>
  dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.remove("over");
  })
);
dropzone.addEventListener("drop", (event) => {
  if (event.dataTransfer.files.length) parseFile(event.dataTransfer.files[0]);
});
