from __future__ import annotations

from cogs.ui import channel_ref, enabled_label, notice_embed


def test_channel_ref_formats_missing_and_present() -> None:
    assert channel_ref(None) == "미설정"
    assert channel_ref(123) == "<#123>"


def test_enabled_label() -> None:
    assert enabled_label(True) == "켜짐"
    assert enabled_label(False) == "꺼짐"


def test_notice_embed_uses_title_and_description() -> None:
    embed = notice_embed("완료", "설정했습니다.", tone="ok")

    assert embed.title == "완료"
    assert embed.description == "설정했습니다."
