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
_REACTION_RE = re.compile(r"[ㅋㅎㅠㅜ]+")

_URL_PLACEHOLDER = "링크"
_TRUNCATE_SUFFIX = "…"  # U+2026
_REACTION_SOUNDS = {
    "ㅋ": "크",
    "ㅎ": "하",
    "ㅠ": "유",
    "ㅜ": "유",
}


class _MessageLike(Protocol):
    clean_content: str


def _normalize_reaction(match: re.Match[str]) -> str:
    sounds: list[str] = []
    for char in match.group(0):
        sound = _REACTION_SOUNDS.get(char)
        if sound:
            sounds.append(sound)
    return "".join(sounds)


def clean_message(message: _MessageLike, max_len: int = 200) -> str:
    text = _URL_RE.sub(_URL_PLACEHOLDER, message.clean_content)
    text = _CUSTOM_EMOJI_RE.sub(r"\1", text)
    text = _MD_RE.sub(" ", text)
    text = _REACTION_RE.sub(_normalize_reaction, text)
    text = text.replace("?", "음?")
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip() + _TRUNCATE_SUFFIX
    return text
