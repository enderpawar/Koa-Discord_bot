"""
Phase 3 — Message Preprocessing
URL/멘션/마크다운/길이/빈문자열 케이스의 정제 동작 검증.
"""
from __future__ import annotations
import importlib.util
from dataclasses import dataclass

import pytest

_HAS = importlib.util.find_spec("cogs.preprocess") is not None
pytestmark = pytest.mark.skipif(_HAS is False, reason="Phase 3 not yet implemented")


@dataclass
class FakeMessage:
    """discord.Message 대용. clean_message 가 사용하는 속성만 모방."""
    clean_content: str


def _clean(text: str, max_len: int = 200) -> str:
    from cogs.preprocess import clean_message  # noqa: WPS433
    return clean_message(FakeMessage(clean_content=text), max_len=max_len)


def test_url_replaced_with_korean_link():
    assert _clean("안녕 https://example.com 봐") == "안녕 링크 봐"


def test_https_and_http_both_replaced():
    out = _clean("a http://x.io b https://y.io c")
    assert out == "a 링크 b 링크 c"


def test_markdown_noise_stripped():
    assert _clean("**굵게** _기울임_ ~~취소선~~ `코드`") == "굵게 기울임 취소선 코드"


def test_custom_emoji_preserves_name():
    assert _clean("hey <:smile:1234> there") == "hey smile there"


def test_truncate_at_max_len():
    out = _clean("가" * 250, max_len=200)
    assert len(out) <= 201   # … 한 글자 여유
    assert out.endswith("…")


def test_empty_input_returns_empty():
    assert _clean("") == ""
    assert _clean("   ") == ""


def test_consecutive_whitespace_collapsed():
    assert _clean("a    b\n\n c") == "a b c"


def test_korean_chat_laughter_repeats_are_speakable():
    assert _clean("ㅋㅋㅋㅋㅋ") == "크크크크크"


def test_question_mark_is_spoken_as_um_question():
    assert _clean("?") == "음?"


def test_korean_chat_crying_is_speakable():
    assert _clean("ㅠㅠ") == "유유"


def test_mixed_korean_chat_reactions_are_speakable():
    assert _clean("ㅋㅋㅋㅋㅋ 나 ㅎㅎ? ㅠㅠ") == "크크크크크 나 하하음? 유유"
