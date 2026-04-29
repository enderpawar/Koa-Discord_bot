"""Phase 3 — TTS 전 메시지 정제.

호출자(`on_message`)가 `message.author.bot` 가드를 먼저 통과시킬 책임을 진다 (Rule 01).
이 모듈은 순수 함수이며 외부 의존이 없다 — 테스트는 `clean_content` 속성만 가진
페이크 메시지로 검증된다.
"""
from __future__ import annotations

import re
from typing import Protocol

_URL_RE = re.compile(r"https?://\S+")
_CUSTOM_EMOJI_RE = re.compile(r"<a?:([A-Za-z0-9_]+):\d+>")
_MD_RE = re.compile(r"(\*{1,3}|_{1,3}|~{2}|`+)")
_WS_RE = re.compile(r"\s+")

_URL_PLACEHOLDER = "링크"
_TRUNCATE_SUFFIX = "…"  # U+2026


class _MessageLike(Protocol):
    clean_content: str


def clean_message(message: _MessageLike, max_len: int = 200) -> str:
    text = _URL_RE.sub(_URL_PLACEHOLDER, message.clean_content)
    text = _CUSTOM_EMOJI_RE.sub(r"\1", text)
    text = _MD_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip() + _TRUNCATE_SUFFIX
    return text
