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
# 물음표만 있는 메시지. `?`, `??`, `????` 처럼 되묻는 채팅이다.
_QUESTION_ONLY_RE = re.compile(r"[?？\s]+")

_URL_PLACEHOLDER = "링크"
# 물음표 하나는 소리가 없어서 그냥 두면 침묵이 재생된다. 되묻는 뉘앙스만
# 한 번 읽어 준다. 개수와 무관하게 한 번이다 — `????` 를 네 번 읽으면 시끄럽다.
#
# `으음?!` 인 이유: 앞의 `으` 가 소리를 끌어 밋밋한 `음` 보다 갸웃하는 느낌이
# 살고, 끝의 `?!` 로 Azure 가 억양을 올렸다 끊어 궁금함과 놀람이 섞인 톤이 된다.
_QUESTION_ONLY_SPEECH = "으음?!"
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
    text = _WS_RE.sub(" ", text).strip()
    # 문장에 붙은 물음표는 건드리지 않는다. TTS 가 억양으로 처리하므로
    # `음` 을 끼워 넣으면 "밥 먹었어음?" 처럼 읽힌다.
    if text and _QUESTION_ONLY_RE.fullmatch(text):
        return _QUESTION_ONLY_SPEECH
    if len(text) > max_len:
        text = text[:max_len].rstrip() + _TRUNCATE_SUFFIX
    return text
