from __future__ import annotations

from cogs.tts_cog import _tts_status_embed, _voice_label


def test_voice_label_uses_choice_name() -> None:
    assert _voice_label("ko-KR-SunHiNeural") == "여성-차분 (SunHi)"
    assert _voice_label("custom") == "custom"


def test_tts_status_embed_groups_settings() -> None:
    embed = _tts_status_embed(
        {
            "tts_channel_id": 11,
            "voice_channel_id": 22,
            "voice": "ko-KR-SunHiNeural",
        }
    )

    assert embed.title == "TTS 상태"
    assert [field.name for field in embed.fields] == ["입력 채널", "음성 채널", "보이스", "상태"]
    assert embed.fields[0].value == "<#11>"
    assert embed.fields[1].value == "<#22>"
    assert "여성-차분" in embed.fields[2].value
    assert embed.fields[3].value == "재생 준비됨"
