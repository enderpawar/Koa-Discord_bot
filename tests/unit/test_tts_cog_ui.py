from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.tts_cog import (
    TTSCog,
    TTSControlView,
    VOICE_CHOICES,
    _tts_status_embed,
    _voice_label,
    _voice_panel_embed,
)
from cogs.audio_mode import AUDIO_MODE_MUSIC, AUDIO_MODE_TTS, mode_from_config
from cogs.tts_engine import DEFAULT_VOICE


@pytest.fixture(autouse=True)
def _enable_music_for_existing_mode_tests(monkeypatch) -> None:
    monkeypatch.setenv("MUSIC_ENABLED", "1")


def test_voice_choices_include_all_available_korean_voices() -> None:
    values = [choice.value for choice in VOICE_CHOICES]

    assert len(values) == 10
    assert len(set(values)) == 10
    assert values == [
        "ko-KR-SunHiNeural",
        "ko-KR-JiMinNeural",
        "ko-KR-SeoHyeonNeural",
        "ko-KR-SoonBokNeural",
        "ko-KR-YuJinNeural",
        "ko-KR-InJoonNeural",
        "ko-KR-BongJinNeural",
        "ko-KR-GookMinNeural",
        "ko-KR-HyunsuNeural",
        "ko-KR-HyunsuMultilingualNeural",
    ]


def test_join_greeting_embeds_the_gif_by_url() -> None:
    """1.4MB 파일을 입장할 때마다 올리지 않고 공개 저장소 raw URL 을 쓴다."""
    from cogs.tts_cog import JOIN_GIF_URL, _join_greeting_embed

    channel = _voice_channel(100)
    embed = _join_greeting_embed(channel)

    assert embed.title == "코아 왔어요"
    assert embed.image.url == JOIN_GIF_URL
    assert JOIN_GIF_URL.startswith("https://raw.githubusercontent.com/")
    assert JOIN_GIF_URL.endswith(".gif")


def test_join_gif_asset_is_small_enough_to_load_fast() -> None:
    """원본 6.7MB 를 그대로 두면 임베드가 늦게 뜬다. 최적화본을 유지한다."""
    from pathlib import Path

    gif = Path(__file__).resolve().parents[2] / "assets" / "koa-join.gif"

    assert gif.exists(), "assets/koa-join.gif 가 저장소에 있어야 URL 이 살아 있다"
    assert gif.read_bytes()[:6] == b"GIF89a"
    assert gif.stat().st_size < 2 * 1024 * 1024


def test_default_voice_is_the_soft_female_choice() -> None:
    """서버가 따로 고르기 전 기본 목소리는 `여성 · 부드러움` 이다."""
    assert DEFAULT_VOICE == "ko-KR-SeoHyeonNeural"
    assert _voice_label(DEFAULT_VOICE) == "여성 · 부드러움"
    assert DEFAULT_VOICE in [choice.value for choice in VOICE_CHOICES]


def test_voice_label_uses_choice_name() -> None:
    assert _voice_label("ko-KR-SunHiNeural") == "여성 · 차분"
    assert _voice_label("ko-KR-YuJinNeural") == "여성 · 경쾌"
    assert _voice_label("ko-KR-HyunsuMultilingualNeural") == "남성 · 다국어"
    assert _voice_label("custom") == "custom"


def test_tts_status_embed_groups_settings() -> None:
    embed = _tts_status_embed(
        {
            "tts_channel_id": 11,
            "voice_channel_id": 22,
            "voice": "ko-KR-SunHiNeural",
            "pronunciations": {"ㅇㅈ": "인정"},
        }
    )

    assert embed.title == "TTS 상태"
    assert [field.name for field in embed.fields] == [
        "입력 채널",
        "음성 채널",
        "보이스",
        "발음 사전",
        "상태",
        "오디오 모드",
    ]
    assert embed.fields[0].value == "<#11>"
    assert embed.fields[1].value == "<#22>"
    assert "여성 · 차분" in embed.fields[2].value
    assert embed.fields[3].value == "1개 규칙"
    assert embed.fields[4].value == "재생 준비됨"
    assert embed.fields[5].value == "`TTS`"


def test_tts_status_embed_points_at_join_when_not_connected() -> None:
    """채널을 고르는 명령이 없으므로 `/입장` 말고는 안내할 것이 없다."""
    embed = _tts_status_embed({"voice": "ko-KR-SunHiNeural"})

    assert embed.fields[3].value == "등록된 규칙 없음"
    assert "/입장" in embed.fields[4].value


def test_tts_status_embed_shows_music_mode() -> None:
    embed = _tts_status_embed({"audio_mode": AUDIO_MODE_MUSIC})

    assert embed.fields[5].name == "오디오 모드"
    assert embed.fields[5].value == "`음악`"


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
    cog.modes = MagicMock()
    cog.modes.get_mode = AsyncMock(return_value=mode_from_config(cfg))
    cog.modes.cached_mode = MagicMock(return_value=mode_from_config(cfg))
    cog.modes.lock_for = MagicMock(side_effect=lambda _guild_id: asyncio.Lock())
    cog.queue = MagicMock()
    cog.queue.enqueue = AsyncMock()
    cog.queue.ensure_voice = AsyncMock()
    cog.queue.disconnect_voice = AsyncMock()
    cog._panel_view = MagicMock()
    cog._panel_last_sent = {}
    cog._panel_connect_tasks = {}
    cog._send_voice_panel = AsyncMock()
    cog._stop_music = AsyncMock(return_value=False)
    return cog


@pytest.mark.asyncio
async def test_music_controls_are_disabled_for_tts_only_operation(monkeypatch) -> None:
    monkeypatch.setenv("MUSIC_ENABLED", "0")
    cog = _make_cog({"audio_mode": AUDIO_MODE_MUSIC})
    view = TTSControlView(cog)
    music_button = next(
        item for item in view.children if item.custom_id == "koa_music:enable"
    )
    channel = _voice_channel(100)
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    assert music_button.disabled is True
    assert _voice_panel_embed(channel).fields[0].value == (
        "음악 기능은 현재 운영에서 비활성화되어 있습니다."
    )
    assert _tts_status_embed({"audio_mode": AUDIO_MODE_MUSIC}).fields[5].value == "`TTS`"

    await cog.enable_music_from_panel(interaction)

    interaction.response.send_message.assert_awaited_once()
    assert "비활성화" in interaction.response.send_message.await_args.kwargs[
        "embed"
    ].title
    cog.store.set.assert_not_awaited()


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
        audio_mode=AUDIO_MODE_TTS,
        tts_channel_id=channel.id,
        voice_channel_id=channel.id,
    )
    cog.queue.ensure_voice.assert_awaited_once_with(guild, channel.id)
    assert interaction.edit_original_response.await_count == 2
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_music_panel_switches_mode_and_clears_tts_connection() -> None:
    cog = _make_cog({"audio_mode": AUDIO_MODE_TTS})
    channel = _voice_channel(100)
    guild = MagicMock()
    guild.id = 1
    guild.voice_client = None
    interaction = MagicMock()
    interaction.guild = guild
    interaction.user.voice = SimpleNamespace(channel=channel)
    interaction.channel = channel
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.edit_original_response = AsyncMock()

    await cog.enable_music_from_panel(interaction)
    await asyncio.sleep(0)

    cog.store.set.assert_awaited_once_with(
        guild.id,
        audio_mode=AUDIO_MODE_MUSIC,
        tts_channel_id=channel.id,
        voice_channel_id=channel.id,
    )
    cog.queue.disconnect_voice.assert_awaited_once_with(guild)
    cog.queue.ensure_voice.assert_awaited_once_with(guild, channel.id)
    assert interaction.edit_original_response.await_count == 2


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
    ch.members = []
    return ch


def _human(member_id: int) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.bot = False
    return member


def _bot_member(member_id: int) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.bot = True
    return member


def _connected_voice(channel: discord.VoiceChannel) -> MagicMock:
    vc = MagicMock(spec=discord.VoiceClient)
    vc.channel = channel
    vc.is_connected.return_value = True
    return vc


@pytest.mark.asyncio
async def test_join_uses_callers_voice_channel_for_input_and_output() -> None:
    cog = _make_cog({"voice_channel_id": 999, "tts_channel_id": 888})
    channel = _voice_channel(100)
    guild = MagicMock()
    guild.id = 1
    interaction = MagicMock()
    interaction.guild = guild
    interaction.guild_id = guild.id
    interaction.user = SimpleNamespace(id=7, voice=SimpleNamespace(channel=channel))
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.edit_original_response = AsyncMock()

    await TTSCog.join.callback(cog, interaction)

    cog.store.get.assert_not_awaited()
    cog.queue.ensure_voice.assert_awaited_once_with(guild, channel.id)
    cog.store.set.assert_awaited_once_with(
        guild.id,
        audio_mode=AUDIO_MODE_TTS,
        tts_channel_id=channel.id,
        voice_channel_id=channel.id,
    )
    interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
    interaction.response.send_message.assert_not_awaited()
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "이 음성 채널의 채팅을 읽습니다" in embed.description


@pytest.mark.asyncio
async def test_join_greets_the_channel_only_when_it_actually_arrives() -> None:
    """명령 응답은 ephemeral 이라 채널의 다른 사람은 봇이 온 걸 모른다.

    그래서 인사는 공개로 따로 보내되, 이미 들어와 있는데 `/입장` 을 또 누른
    경우에는 GIF 가 두 번 뜨지 않아야 한다.
    """
    channel = _voice_channel(100)
    guild = MagicMock()
    guild.id = 1

    def _interaction():
        itx = MagicMock()
        itx.guild = guild
        itx.guild_id = guild.id
        itx.user = SimpleNamespace(id=7, voice=SimpleNamespace(channel=channel))
        itx.response.defer = AsyncMock()
        itx.edit_original_response = AsyncMock()
        return itx

    # 아직 연결 전 — 인사한다.
    guild.voice_client = None
    cog = _make_cog()
    cog._announce_join = AsyncMock()
    await TTSCog.join.callback(cog, _interaction())
    cog._announce_join.assert_awaited_once_with(channel)

    # 이미 같은 채널에 있음 — 인사하지 않는다.
    guild.voice_client = _connected_voice(channel)
    cog = _make_cog()
    cog._announce_join = AsyncMock()
    await TTSCog.join.callback(cog, _interaction())
    cog._announce_join.assert_not_awaited()


@pytest.mark.asyncio
async def test_join_requires_caller_to_be_in_voice_channel() -> None:
    cog = _make_cog()
    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.guild.id = 1
    interaction.guild_id = 1
    interaction.user = SimpleNamespace(id=7, voice=None)
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()

    await TTSCog.join.callback(cog, interaction)

    cog.queue.ensure_voice.assert_not_awaited()
    cog.store.set.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert "음성 채널에 입장" in embed.description


@pytest.mark.asyncio
async def test_join_is_blocked_while_music_mode_is_selected() -> None:
    cog = _make_cog({"audio_mode": AUDIO_MODE_MUSIC})
    channel = _voice_channel(100)
    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.guild.id = 1
    interaction.guild_id = 1
    interaction.user = SimpleNamespace(id=7, voice=SimpleNamespace(channel=channel))
    interaction.response.send_message = AsyncMock()

    await TTSCog.join.callback(cog, interaction)

    cog.queue.ensure_voice.assert_not_awaited()
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert embed.title == "TTS 재생 불가"
    assert "음악 모드" in embed.description


@pytest.mark.asyncio
async def test_join_does_not_change_channels_when_voice_connection_fails() -> None:
    cog = _make_cog()
    cog.queue.ensure_voice.side_effect = RuntimeError("voice failed")
    channel = _voice_channel(100)
    guild = MagicMock()
    guild.id = 1
    interaction = MagicMock()
    interaction.guild = guild
    interaction.guild_id = guild.id
    interaction.user = SimpleNamespace(id=7, voice=SimpleNamespace(channel=channel))
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.edit_original_response = AsyncMock()

    await TTSCog.join.callback(cog, interaction)

    cog.store.set.assert_not_awaited()
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert embed.title == "입장 실패"


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
    vc.channel.members = [_human(10), _human(11)]

    member = MagicMock()
    member.id = 10
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
async def test_voice_state_disconnects_when_last_human_leaves() -> None:
    cog = _make_cog({
        "voice_channel_id": 100, "voice": "ko-KR-SunHiNeural",
    })
    channel = _voice_channel(100)
    channel.members = [_bot_member(999)]
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.channel = channel

    member = _human(10)
    member.display_name = "tester"
    member.guild = MagicMock()
    member.guild.id = 1
    member.guild.voice_client = vc

    await cog._handle_voice_state(
        member,
        SimpleNamespace(channel=channel),
        SimpleNamespace(channel=None),
    )

    cog.queue.disconnect_voice.assert_awaited_once_with(member.guild)
    cog.queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_state_stays_when_another_human_remains() -> None:
    cog = _make_cog({
        "voice_channel_id": 100, "voice": "ko-KR-SunHiNeural",
    })
    channel = _voice_channel(100)
    channel.members = [_human(11), _bot_member(999)]
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.channel = channel

    member = _human(10)
    member.display_name = "tester"
    member.guild = MagicMock()
    member.guild.id = 1
    member.guild.voice_client = vc

    await cog._handle_voice_state(
        member,
        SimpleNamespace(channel=channel),
        SimpleNamespace(channel=None),
    )

    cog.queue.disconnect_voice.assert_not_awaited()
    cog.queue.enqueue.assert_awaited_once()
    args, _ = cog.queue.enqueue.call_args
    assert args[1].text == "tester님 퇴장"


@pytest.mark.asyncio
async def test_handle_tts_message_fast_path_skips_async_get() -> None:
    """무관 채널 메시지는 cached 만 보고 즉시 reject — async store.get 미호출."""
    cog = _make_cog({"tts_channel_id": 100, "voice_channel_id": 100})

    message = MagicMock()
    message.author.bot = False
    message.webhook_id = None
    message.guild = MagicMock()
    message.guild.id = 1
    message.channel = MagicMock()
    message.channel.id = 999

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
    message.guild = MagicMock()
    message.guild.id = 1
    voice_channel = _voice_channel(200)
    voice_channel.members = [_human(10)]
    message.guild.get_channel.return_value = voice_channel
    message.guild.voice_client = _connected_voice(voice_channel)
    message.channel = MagicMock()
    message.channel.id = 100
    message.clean_content = "hello"

    await cog._handle_tts_message(message)

    cog.store.get.assert_awaited_once()
    cog.queue.enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_tts_message_reads_joined_voice_channel_chat() -> None:
    """`/입장`이 같은 ID로 저장한 음성 채널 채팅을 바로 읽는다."""
    cog = _make_cog({
        "tts_channel_id": 100,
        "voice_channel_id": 100,
        "voice": "ko-KR-SunHiNeural",
    })
    voice_channel = _voice_channel(100)
    voice_channel.members = [_human(10)]
    message = MagicMock()
    message.guild = MagicMock()
    message.guild.id = 1
    message.guild.get_channel.return_value = voice_channel
    message.guild.voice_client = _connected_voice(voice_channel)
    message.channel = voice_channel
    message.clean_content = "음성 채널 채팅"

    await cog._handle_tts_message(message)

    cog.queue.enqueue.assert_awaited_once()
    request = cog.queue.enqueue.await_args.args[1]
    assert request.voice_channel_id == voice_channel.id
    assert request.text == "음성 채널 채팅"


@pytest.mark.asyncio
async def test_handle_tts_message_ignores_input_in_music_mode() -> None:
    cog = _make_cog({
        "audio_mode": AUDIO_MODE_MUSIC,
        "tts_channel_id": 100,
        "voice_channel_id": 100,
    })
    voice_channel = _voice_channel(100)
    voice_channel.members = [_human(10)]
    message = MagicMock()
    message.guild = MagicMock()
    message.guild.id = 1
    message.guild.voice_client = _connected_voice(voice_channel)
    message.channel = voice_channel
    message.clean_content = "음악 모드에서는 읽지 마"

    await cog._handle_tts_message(message)

    cog.store.get.assert_not_awaited()
    cog.queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_tts_message_applies_the_guild_pronunciation_dictionary() -> None:
    cog = _make_cog({
        "tts_channel_id": 100,
        "voice_channel_id": 100,
        "voice": "ko-KR-SunHiNeural",
        "pronunciations": {"ㅇㅈ": "인정"},
    })
    voice_channel = _voice_channel(100)
    voice_channel.members = [_human(10)]
    message = MagicMock()
    message.guild = MagicMock()
    message.guild.id = 1
    message.guild.get_channel.return_value = voice_channel
    message.guild.voice_client = _connected_voice(voice_channel)
    message.channel = voice_channel
    message.clean_content = "그거 ㅇㅈ"

    await cog._handle_tts_message(message)

    assert cog.queue.enqueue.await_args.args[1].text == "그거 인정"


@pytest.mark.asyncio
async def test_handle_tts_message_does_not_rejoin_empty_voice_channel() -> None:
    cog = _make_cog({
        "tts_channel_id": 100, "voice_channel_id": 200,
    })

    message = MagicMock()
    message.author.bot = False
    message.webhook_id = None
    message.guild = MagicMock()
    message.guild.id = 1
    voice_channel = _voice_channel(200)
    message.guild.get_channel.return_value = voice_channel
    message.guild.voice_client = _connected_voice(voice_channel)
    message.channel = MagicMock()
    message.channel.id = 100
    message.clean_content = "아무도 없어요"

    await cog._handle_tts_message(message)

    cog.queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_tts_message_does_not_auto_join_when_disconnected() -> None:
    """사람이 있어도 봇이 미접속이면 채팅 메시지로 자동 입장하지 않는다."""
    cog = _make_cog({
        "tts_channel_id": 100,
        "voice_channel_id": 100,
    })
    voice_channel = _voice_channel(100)
    voice_channel.members = [_human(10)]
    message = MagicMock()
    message.guild = MagicMock()
    message.guild.id = 1
    message.guild.voice_client = None
    message.guild.get_channel.return_value = voice_channel
    message.channel = voice_channel
    message.clean_content = "자동 입장 금지"

    await cog._handle_tts_message(message)

    cog.store.get.assert_not_awaited()
    cog.queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_tts_message_ignores_different_bot_voice_channel() -> None:
    """봇이 다른 음성 채널에 있으면 설정 채널 채팅을 재생하지 않는다."""
    cog = _make_cog({
        "tts_channel_id": 100,
        "voice_channel_id": 100,
    })
    voice_channel = _voice_channel(100)
    voice_channel.members = [_human(10)]
    message = MagicMock()
    message.guild = MagicMock()
    message.guild.id = 1
    message.guild.voice_client = _connected_voice(_voice_channel(200))
    message.guild.get_channel.return_value = voice_channel
    message.channel = voice_channel
    message.clean_content = "다른 채널"

    await cog._handle_tts_message(message)

    cog.store.get.assert_not_awaited()
    cog.queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_voice_state_fast_path_skips_unrelated_channel() -> None:
    """watched 채널 외 음성 채널 입/퇴장은 async store.get 호출 없이 패널만 보낸다."""
    cog = _make_cog({"voice_channel_id": 100})

    member = MagicMock()
    member.bot = False
    member.display_name = "tester"
    member.guild = MagicMock()
    member.guild.id = 1
    member.guild.voice_client = None

    before = SimpleNamespace(channel=None)
    after = SimpleNamespace(channel=_voice_channel(777))

    await cog._handle_voice_state(member, before, after)

    cog.store.get.assert_not_awaited()
    cog.queue.enqueue.assert_not_called()
    cog._send_voice_panel.assert_awaited_once()
