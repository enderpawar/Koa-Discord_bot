from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.tts_cog import TTSCog, _tts_status_embed, _voice_label


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


def _make_cog() -> TTSCog:
    bot = MagicMock()
    bot.add_view = MagicMock()
    cog = TTSCog.__new__(TTSCog)
    cog.bot = bot
    cog.store = MagicMock()
    cog.queue = MagicMock()
    cog.queue.enqueue = AsyncMock()
    cog._panel_view = MagicMock()
    cog._panel_last_sent = {}
    cog._send_voice_panel = AsyncMock()
    return cog


def _voice_channel(channel_id: int) -> MagicMock:
    ch = MagicMock(spec=discord.VoiceChannel)
    ch.id = channel_id
    return ch


@pytest.mark.asyncio
async def test_voice_state_no_announcement_when_bot_disconnected() -> None:
    """봇이 watched 채널에 미접속이면 사용자 입장 시 안내 enqueue 금지 (자동 입장 방지)."""
    cog = _make_cog()
    cog.store.get = AsyncMock(return_value={
        "voice_channel_id": 100, "voice": "ko-KR-SunHiNeural",
    })
    member = MagicMock()
    member.bot = False
    member.display_name = "tester"
    member.guild = MagicMock()
    member.guild.id = 1
    member.guild.voice_client = None  # 봇 미접속

    before = SimpleNamespace(channel=None)
    after = SimpleNamespace(channel=_voice_channel(100))

    await cog._handle_voice_state(member, before, after)

    cog.queue.enqueue.assert_not_called()
    cog._send_voice_panel.assert_awaited_once()


@pytest.mark.asyncio
async def test_voice_state_announces_when_bot_already_connected() -> None:
    """봇이 watched 채널에 이미 접속해 있으면 입장 안내를 enqueue 한다."""
    cog = _make_cog()
    cog.store.get = AsyncMock(return_value={
        "voice_channel_id": 100, "voice": "ko-KR-SunHiNeural",
    })
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.channel = _voice_channel(100)

    member = MagicMock()
    member.bot = False
    member.display_name = "tester"
    member.guild = MagicMock()
    member.guild.id = 1
    member.guild.voice_client = vc

    before = SimpleNamespace(channel=None)
    after = SimpleNamespace(channel=_voice_channel(100))

    await cog._handle_voice_state(member, before, after)

    cog.queue.enqueue.assert_awaited_once()
    args, _ = cog.queue.enqueue.call_args
    assert args[1].text == "tester님 입장"


@pytest.mark.asyncio
async def test_voice_state_no_announcement_when_bot_in_other_channel() -> None:
    """봇이 다른 채널에 접속해 있으면 watched 채널 입장 안내를 보내지 않는다."""
    cog = _make_cog()
    cog.store.get = AsyncMock(return_value={
        "voice_channel_id": 100, "voice": "ko-KR-SunHiNeural",
    })
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.channel = _voice_channel(999)  # 다른 채널

    member = MagicMock()
    member.bot = False
    member.display_name = "tester"
    member.guild = MagicMock()
    member.guild.id = 1
    member.guild.voice_client = vc

    before = SimpleNamespace(channel=None)
    after = SimpleNamespace(channel=_voice_channel(100))

    await cog._handle_voice_state(member, before, after)

    cog.queue.enqueue.assert_not_called()
