"""MIME decoding and deterministic extraction, without SDK or browser imports."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import Message
from html.parser import HTMLParser


MAX_BODY_SIZE = 1_000_000


def headers(payload: dict) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    entries = payload.get("headers", [])
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        name, value = entry.get("name"), entry.get("value")
        if isinstance(name, str) and isinstance(value, str):
            try:
                value = str(make_header(decode_header(value)))
            except (LookupError, UnicodeError, ValueError):
                continue
            values.setdefault(name.lower(), []).append(value)
    return values


class _VisibleHTML(HTMLParser):
    """Use anchor text, never hrefs, scripts, styles, or head metadata."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head"}:
            self.hidden += 1
        if tag in {"br", "p", "div", "li", "tr", "td", "hr"}:
            self.text.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head"} and self.hidden:
            self.hidden -= 1
        if tag in {"p", "div", "li", "tr", "td"}:
            self.text.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


@dataclass(frozen=True)
class MessageBodies:
    plain: tuple[str, ...] = field(default=(), repr=False)
    html: tuple[str, ...] = field(default=(), repr=False)


def decode_bodies(payload: dict) -> MessageBodies:
    """Walk nested inline MIME parts, skipping malformed data and attachments."""
    plain, html = [], []
    remaining = MAX_BODY_SIZE

    def visit(part: dict, depth: int = 0) -> None:
        nonlocal remaining
        if not isinstance(part, dict) or depth > 30 or remaining <= 0:
            return
        meta = headers(part)
        if part.get("filename") or any(value.lower().startswith("attachment")
                                      for value in meta.get("content-disposition", [])):
            return
        for child in part.get("parts", []) if isinstance(part.get("parts", []), list) else []:
            visit(child, depth + 1)
        mime = part.get("mimeType", "")
        if not isinstance(mime, str):
            return
        mime = mime.lower()
        body = part.get("body", {})
        data = body.get("data") if isinstance(body, dict) else None
        if mime not in {"text/plain", "text/html"} or not isinstance(data, str) or not data:
            return
        if len(data) > MAX_BODY_SIZE * 2:
            return
        try:
            raw = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
            if len(raw) > remaining:
                return
            content_type = Message()
            content_type["Content-Type"] = next(iter(meta.get("content-type", [])), mime)
            text = raw.decode(content_type.get_content_charset() or "utf-8")
        except (ValueError, binascii.Error, UnicodeError, LookupError):
            return
        remaining -= len(raw)
        if mime == "text/html":
            parser = _VisibleHTML()
            parser.feed(text)
            parser.close()
            html.append("".join(parser.text))
        else:
            plain.append(text)

    visit(payload)
    return MessageBodies(tuple(plain), tuple(html))


def valid_code(value: object, length: int, characters: str) -> bool:
    return (isinstance(value, str) and len(value) == length
            and all(re.fullmatch(characters, char) is not None for char in value))


@dataclass(frozen=True)
class Extraction:
    value: str | None = field(default=None, repr=False)
    ambiguous: bool = False


def extract_code(bodies: MessageBodies, regex: str, length: int,
                 characters: str = r"[0-9]") -> Extraction:
    """Prefer plain text, then HTML; refuse multiple distinct valid codes.

    Named 'code' capture wins, otherwise use the first capture or the full match.
    """
    pattern = re.compile(regex)
    for texts in (bodies.plain, bodies.html):
        candidates = set()
        for text in texts:
            for match in pattern.finditer(text):
                value = (match.group("code") if "code" in pattern.groupindex else
                         match.group(1) if pattern.groups else match.group(0))
                if valid_code(value, length, characters):
                    candidates.add(value)
        if candidates:
            return Extraction(next(iter(candidates)) if len(candidates) == 1 else None,
                              ambiguous=len(candidates) > 1)
    return Extraction()
