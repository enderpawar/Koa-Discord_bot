from __future__ import annotations

from cogs.admin_cog import _TIME_RE, _configured, _settings_embed, _settings_message


def test_leaderboard_post_time_validation() -> None:
    assert _TIME_RE.match("23:59")
    assert _TIME_RE.match("00:00")
    assert not _TIME_RE.match("24:00")
    assert not _TIME_RE.match("8:00")


def test_configured_masks_secrets() -> None:
    assert _configured("token", secret=True) == "설정됨"
    assert _configured("koreacentral") == "`koreacentral`"
    assert _configured(None) == "미설정"


def test_settings_message_includes_panel_state() -> None:
    message = _settings_message(
        {
            "leaderboard_daily_enabled": True,
            "leaderboard_channel_id": 123,
            "leaderboard_post_time": "23:59",
        }
    )

    assert "관리자 설정 패널" in message
    assert "일일 리더보드: `켜짐`" in message
    assert "리더보드 채널: <#123>" in message
    assert "리더보드 발송 시각: `23:59` KST" in message


def test_settings_embed_groups_admin_state() -> None:
    embed = _settings_embed(
        {
            "tts_channel_id": 11,
            "voice_channel_id": 22,
            "leaderboard_daily_enabled": True,
            "leaderboard_channel_id": 123,
            "leaderboard_post_time": "23:59",
            "leaderboard_last_post_date": "2026-05-01",
        }
    )

    assert embed.title == "관리자 설정"
    assert [field.name for field in embed.fields] == ["TTS", "일일 리더보드", "작업"]
    assert "입력 채널: <#11>" in embed.fields[0].value
    assert "자동 발송: `켜짐`" in embed.fields[1].value
    assert "발송 채널: <#123>" in embed.fields[1].value
