from __future__ import annotations

import asyncio
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


def _make_cog(cfg: dict | None = None) -> TTSCog:
    cfg = cfg or {}
    bot = MagicMock()
    bot.add_view = MagicMock()
    cog = TTSCog.__new__(TTSCog)
    cog.bot = bot
    cog.store = MagicMock()
    cog.store.get = AsyncMock(return_value=dict(cfg))
    cog.store.set = AsyncMock()
    # ConfigStore 가 path-singleton 이라 cached 와 async 결과는 항상 동일하다.
    cog.store.get_cached_sync = MagicMock(return_value=dict(cfg))
    cog.queue = MagicMock()
    cog.queue.enqueue = AsyncMock()
    cog.queue.ensure_voice = AsyncMock()
    cog.queue.disconnect_voice = AsyncMock()
    cog._panel_view = MagicMock()
    cog._panel_last_sent = {}
    cog._panel_connect_tasks = {}
    cog._send_voice_panel = AsyncMock()
    return cog


@pytest.mark.asyncio
async def test_enable_panel_defers_before_voice_connection() -> None:
    cog = _make_cog()
    channel = _voice_channel(100)
    guild = MagicMock()
    guild.id = 1

    interaction = MagicMock()
    interaction.guild = guild
    interaction.user.voice = SimpleNamespace(channel=channel)
    interaction.channel = channel
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.edit_original_response = AsyncMock()

    await cog.enable_from_panel(interaction)
    await asyncio.sleep(0)

    interaction.response.defer.assert_awaited_once_with(
        thinking=True,
        ephemeral=True,
    )
    cog.store.set.assert_awaited_once_with(
        guild.id,
        tts_channel_id=channel.id,
        voice_channel_id=channel.id,
    )
    cog.queue.ensure_voice.assert_awaited_once_with(guild, channel.id)
    assert interaction.edit_original_response.await_count == 2
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_disable_panel_cancels_connect_and_disconnects_voice() -> None:
    cog = _make_cog({"tts_channel_id": 100, "voice_channel_id": 100})
    channel = _voice_channel(100)
    guild = MagicMock()
    guild.id = 1
    interaction = MagicMock()
    interaction.guild = guild
    interaction.channel = channel
    interaction.response.defer = AsyncMock()
    interaction.edit_original_response = AsyncMock()

    never = asyncio.Event()
    connect_task = asyncio.create_task(never.wait())
    cog._panel_connect_tasks[guild.id] = connect_task

    await cog.disable_from_panel(interaction)

    assert connect_task.cancelled()
    cog.queue.disconnect_voice.assert_awaited_once_with(guild)
    interaction.response.defer.assert_awaited_once_with(
        thinking=True,
        ephemeral=True,
    )
    interaction.edit_original_response.assert_awaited_once()


def _voice_channel(channel_id: int) -> MagicMock:
    ch = MagicMock(spec=discord.VoiceChannel)
    ch.id = channel_id
    return ch


@pytest.mark.asyncio
async def test_voice_state_no_announcement_when_bot_disconnected() -> None:
    """봇이 watched 채널에 미접속이면 사용자 입장 시 안내 enqueue 금지 (자동 입장 방지)."""
    cog = _make_cog({
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
    cog = _make_cog({
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
    cog = _make_cog({
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


@pytest.mark.asyncio
async def test_handle_tts_message_fast_path_skips_async_get() -> None:
    """무관 채널 메시지는 cached 만 보고 즉시 reject — async store.get 미호출."""
    cog = _make_cog({"tts_channel_id": 100, "voice_channel_id": 100})

    message = MagicMock()
    message.author.bot = False
    message.webhook_id = None
    message.guild = MagicMock(); message.guild.id = 1
    message.channel = MagicMock(); message.channel.id = 999

    await cog._handle_tts_message(message)

    cog.store.get.assert_not_awaited()
    cog.queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_handle_tts_message_enqueues_when_channel_matches() -> None:
    """매칭 채널이면 정상 enqueue."""
    cog = _make_cog({
        "tts_channel_id": 100, "voice_channel_id": 200, "voice": "ko-KR-SunHiNeural",
    })

    message = MagicMock()
    message.author.bot = False
    message.webhook_id = None
    message.guild = MagicMock(); message.guild.id = 1
    message.channel = MagicMock(); message.channel.id = 100
    message.clean_content = "hello"

    await cog._handle_tts_message(message)

    cog.store.get.assert_awaited_once()
    cog.queue.enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_voice_state_fast_path_skips_unrelated_channel() -> None:
    """watched 채널 외 음성 채널 입/퇴장은 async store.get 호출 없이 패널만 보낸다."""
    cog = _make_cog({"voice_channel_id": 100})

    member = MagicMock()
    member.bot = False
    member.display_name = "tester"
    member.guild = MagicMock(); member.guild.id = 1
    member.guild.voice_client = None

    before = SimpleNamespace(channel=None)
    after = SimpleNamespace(channel=_voice_channel(777))

    await cog._handle_voice_state(member, before, after)

    cog.store.get.assert_not_awaited()
    cog.queue.enqueue.assert_not_called()
    cog._send_voice_panel.assert_awaited_once()
