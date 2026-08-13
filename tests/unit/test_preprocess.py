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


def _clean(text: str, max_len: int = 200, pronunciations=None) -> str:
    from cogs.preprocess import clean_message  # noqa: WPS433
    return clean_message(
        FakeMessage(clean_content=text),
        max_len=max_len,
        pronunciations=pronunciations,
    )


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


def test_question_only_message_is_spoken_once_regardless_of_count():
    """되묻는 `?` 는 소리가 없어 그냥 두면 침묵이 나간다. 한 번만 읽어 준다."""
    assert _clean("?") == "으음?!"
    assert _clean("??") == "으음?!"
    assert _clean("????") == "으음?!"
    assert _clean("  ???  ") == "으음?!"
    assert _clean("? ?") == "으음?!"


def test_question_mark_attached_to_a_sentence_is_left_alone():
    """문장 뒤 물음표는 TTS 억양이 처리한다. `음` 을 끼우면 읽기가 망가진다."""
    assert _clean("밥 먹었어?") == "밥 먹었어?"
    assert _clean("뭐? 진짜??") == "뭐? 진짜??"
    assert _clean("왜?????") == "왜?????"


def test_korean_chat_crying_is_speakable():
    assert _clean("ㅠㅠ") == "유유"


def test_mixed_korean_chat_reactions_are_speakable():
    assert _clean("ㅋㅋㅋㅋㅋ 나 ㅎㅎ? ㅠㅠ") == "크크크크크 나 하하? 유유"


# ---------- 서버별 발음 사전 ----------


def test_pronunciation_rule_replaces_the_term():
    assert _clean("그거 ㅇㅈ", pronunciations={"ㅇㅈ": "인정"}) == "그거 인정"


def test_no_rules_leaves_text_untouched():
    assert _clean("그거 ㅇㅈ", pronunciations={}) == "그거 ㅇㅈ"
    assert _clean("그거 ㅇㅈ", pronunciations=None) == "그거 ㅇㅈ"


def test_rules_do_not_chain_into_each_other():
    """`가`→`나`, `나`→`다` 를 넣어도 `가` 는 `나` 로만 읽혀야 한다."""
    assert _clean("가나", pronunciations={"가": "나", "나": "다"}) == "나다"


def test_longer_term_wins_over_its_own_prefix():
    rules = {"롤": "리그오브레전드", "롤체": "전략적 팀 전투"}
    assert _clean("롤체 하자", pronunciations=rules) == "전략적 팀 전투 하자"
    assert _clean("롤 하자", pronunciations=rules) == "리그오브레전드 하자"


def test_matching_ignores_ascii_case():
    assert _clean("GG 치자", pronunciations={"gg": "지지"}) == "지지 치자"


def test_empty_reading_drops_the_term():
    assert _clean("야 씨 왔어", pronunciations={"씨 ": ""}) == "야 왔어"


def test_rule_wins_over_builtin_reaction_sounds():
    """서버가 기본 자음 처리를 덮어쓸 수 있어야 한다."""
    assert _clean("ㅋㅋ", pronunciations={"ㅋㅋ": "하하"}) == "하하"


def test_replacement_value_is_literal_not_a_regex_backreference():
    assert _clean("x", pronunciations={"x": r"\1"}) == r"\1"


def test_replacement_happens_before_truncation():
    out = _clean("가" + "나" * 199, max_len=200, pronunciations={"가": "다" * 30})
    assert len(out) <= 201
    assert out.startswith("다" * 30)


def test_malformed_rules_are_dropped_not_raised():
    from cogs.preprocess import (  # noqa: WPS433
        MAX_PRONUNCIATION_KEY,
        MAX_PRONUNCIATION_RULES,
        normalize_pronunciations,
    )

    assert normalize_pronunciations(None) == {}
    assert normalize_pronunciations("not a mapping") == {}
    assert normalize_pronunciations({"  ": "x"}) == {}
    assert normalize_pronunciations({"가" * (MAX_PRONUNCIATION_KEY + 1): "x"}) == {}
    assert normalize_pronunciations({" 가 ": " 나 "}) == {"가": "나"}
    over = {f"k{i}": "v" for i in range(MAX_PRONUNCIATION_RULES + 20)}
    assert len(normalize_pronunciations(over)) == MAX_PRONUNCIATION_RULES
