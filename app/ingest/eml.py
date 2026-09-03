"""Разбор письма (.eml) штатным email из stdlib."""
from __future__ import annotations

import re
from email import policy
from email.parser import BytesParser

TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
    html = re.sub(r"(?i)</td>", "\t", html)
    text = TAG_RE.sub(" ", html)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return re.sub(r"[ \t]{2,}", " ", text)


def extract(data: bytes) -> tuple[str, list[str]]:
    msg = BytesParser(policy=policy.default).parsebytes(data)
    notes: list[str] = []

    header_lines = []
    for header in ("From", "To", "Subject", "Date"):
        value = msg.get(header)
        if value:
            header_lines.append(f"{header}: {value}")

    body = ""
    if msg.is_multipart():
        plain_parts, html_parts = [], []
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get_filename():
                notes.append(f"Во вложении файл {part.get_filename()} — он не разбирался")
                continue
            if ctype == "text/plain":
                plain_parts.append(part.get_content())
            elif ctype == "text/html":
                html_parts.append(_html_to_text(part.get_content()))
        body = "\n".join(plain_parts) or "\n".join(html_parts)
    else:
        content = msg.get_content()
        body = content if msg.get_content_type() == "text/plain" else _html_to_text(content)

    text = "\n".join(header_lines) + "\n\n" + body
    return text, notes
