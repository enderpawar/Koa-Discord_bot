"""Phase 3 — TTS 전 메시지 정제.

호출자(`on_message`)가 `message.author.bot` 가드를 먼저 통과시킬 책임을 진다 (Rule 01).
이 모듈은 순수 함수이며 외부 의존이 없다 — 테스트는 `clean_content` 속성만 가진
페이크 메시지로 검증된다.

서버별 발음 사전은 호출자가 `pronunciations` 로 넘긴다. 이 모듈이 ConfigStore 를
직접 읽지 않아야 순수 함수로 남고, 테스트가 dict 하나로 끝난다.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Mapping, Protocol

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

# 발음 사전 한도. 대시보드 검증(web_admin_cog)과 저장된 설정 양쪽에서 같은 값을
# 쓴다. 사전은 메시지마다 훑으므로 무한정 늘어나면 핫 경로가 느려진다.
MAX_PRONUNCIATION_RULES = 100
MAX_PRONUNCIATION_KEY = 20
MAX_PRONUNCIATION_VALUE = 40

# 감정 라벨. Azure 스타일 이름과 철자가 같지만 여기서는 그냥 라벨이다 —
# 어떤 보이스가 어떤 스타일을 실제로 지원하는지는 tts_engine 이 판단한다.
TONE_CHEERFUL = "cheerful"
TONE_SAD = "sad"
TONE_EXCITED = "excited"

_LAUGH_RE = re.compile(r"[ㅋㅎ]")
_CRY_RE = re.compile(r"[ㅠㅜ]")
_BANG_RE = re.compile(r"[!！]")
# 자음 하나짜리(`ㅋ`, `ㅠ`)는 추임새나 오타일 때가 많다. 두 번은 반복해야
# 감정으로 친다.
_TONE_MIN_REPEAT = 2
_TONE_MIN_BANGS = 2


def detect_tone(text: str) -> str | None:
    """원문에서 감정 신호를 읽는다. 애매하면 None(기본 톤).

    반드시 **정제 전 원문**에 대해 부른다. `clean_message` 는 `ㅋㅋ` 를 `크크` 로
    바꾸므로, 그 뒤에 부르면 신호가 이미 사라진 뒤다.

    확신이 없으면 감정을 붙이지 않는다. 잘못 얹은 감정은 밋밋한 낭독보다 훨씬
    귀에 거슬린다.
    """
    if not text:
        return None
    laughs = len(_LAUGH_RE.findall(text))
    cries = len(_CRY_RE.findall(text))
    if laughs >= _TONE_MIN_REPEAT or cries >= _TONE_MIN_REPEAT:
        if laughs > cries:
            return TONE_CHEERFUL
        if cries > laughs:
            return TONE_SAD
        # `ㅋㅋㅠㅠ` 처럼 웃음과 울음이 같은 무게면 어느 쪽도 고르지 않는다.
        return None
    if len(_BANG_RE.findall(text)) >= _TONE_MIN_BANGS:
        return TONE_EXCITED
    return None


class _MessageLike(Protocol):
    clean_content: str


def normalize_pronunciations(rules: Mapping[str, str] | None) -> dict[str, str]:
    """설정에서 읽은 원시 값을 검증된 사전으로 좁힌다.

    config.json 은 손으로도 고칠 수 있고 과거 버전이 남긴 값이 섞일 수도 있다.
    한도를 넘거나 형식이 어긋난 항목은 예외 대신 조용히 버린다 — 규칙 하나
    때문에 그 서버의 TTS 전체가 멈추는 편이 훨씬 나쁘다 (Rule 03).
    """
    if not isinstance(rules, Mapping):
        return {}
    cleaned: dict[str, str] = {}
    for raw_key, raw_value in rules.items():
        key = str(raw_key).strip()
        value = "" if raw_value is None else str(raw_value).strip()
        if not key or len(key) > MAX_PRONUNCIATION_KEY:
            continue
        if len(value) > MAX_PRONUNCIATION_VALUE:
            continue
        cleaned[key] = value
        if len(cleaned) >= MAX_PRONUNCIATION_RULES:
            break
    return cleaned


@lru_cache(maxsize=32)
def _compile_pronunciations(
    pairs: tuple[tuple[str, str], ...],
) -> tuple[re.Pattern[str], dict[str, str]] | None:
    """치환 규칙을 정규식 하나로 컴파일한다.

    규칙을 str.replace 로 하나씩 적용하면 앞 규칙의 결과에 뒤 규칙이 또 걸린다
    (`가`→`나`, `나`→`다` 를 넣으면 `가` 가 `다` 로 읽힌다). 한 패턴으로 한 번만
    훑어 각 위치를 정확히 한 번 바꾼다.

    긴 원문을 먼저 배열해야 `롤` 규칙이 `롤체` 규칙을 가로채지 않는다.
    사전은 거의 바뀌지 않으므로 컴파일 결과를 캐시해 메시지마다 다시 만들지 않는다.
    """
    table = {key.lower(): value for key, value in pairs}
    if not table:
        return None
    pattern = re.compile(
        "|".join(re.escape(key) for key in sorted(table, key=len, reverse=True)),
        re.IGNORECASE,
    )
    return pattern, table


def _apply_pronunciations(text: str, rules: Mapping[str, str] | None) -> str:
    cleaned = normalize_pronunciations(rules)
    if not cleaned:
        return text
    compiled = _compile_pronunciations(tuple(cleaned.items()))
    if compiled is None:
        return text
    pattern, table = compiled
    # 치환값은 리터럴이다. 문자열로 넘기면 `\1` 같은 입력이 역참조로 해석된다.
    return pattern.sub(lambda match: table[match.group(0).lower()], text)


def _normalize_reaction(match: re.Match[str]) -> str:
    sounds: list[str] = []
    for char in match.group(0):
        sound = _REACTION_SOUNDS.get(char)
        if sound:
            sounds.append(sound)
    return "".join(sounds)


def clean_message(
    message: _MessageLike,
    max_len: int = 200,
    *,
    pronunciations: Mapping[str, str] | None = None,
) -> str:
    text = _URL_RE.sub(_URL_PLACEHOLDER, message.clean_content)
    text = _CUSTOM_EMOJI_RE.sub(r"\1", text)
    text = _MD_RE.sub(" ", text)
    # 서버 사전이 기본 규칙보다 먼저다. `ㅋㅋ` 을 다르게 읽고 싶은 서버가
    # 아래 자음 반응 처리를 덮어쓸 수 있어야 한다.
    text = _apply_pronunciations(text, pronunciations)
    text = _REACTION_RE.sub(_normalize_reaction, text)
    text = _WS_RE.sub(" ", text).strip()
    # 문장에 붙은 물음표는 건드리지 않는다. TTS 가 억양으로 처리하므로
    # `음` 을 끼워 넣으면 "밥 먹었어음?" 처럼 읽힌다.
    if text and _QUESTION_ONLY_RE.fullmatch(text):
        return _QUESTION_ONLY_SPEECH
    if len(text) > max_len:
        text = text[:max_len].rstrip() + _TRUNCATE_SUFFIX
    return text
