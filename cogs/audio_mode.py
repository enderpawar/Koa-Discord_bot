"""Guild-scoped exclusive audio mode coordination."""
from __future__ import annotations

import asyncio
from typing import Final

from discord.ext import commands

from cogs.config_store import ConfigStore


AUDIO_MODE_TTS: Final = "tts"
AUDIO_MODE_MUSIC: Final = "music"
VALID_AUDIO_MODES: Final = frozenset({AUDIO_MODE_TTS, AUDIO_MODE_MUSIC})
_BOT_ATTRIBUTE: Final = "_koa_audio_mode_coordinator"


def mode_from_config(config: dict) -> str:
    """Return a supported mode, defaulting old guild configs to TTS."""
    mode = config.get("audio_mode", AUDIO_MODE_TTS)
    return mode if mode in VALID_AUDIO_MODES else AUDIO_MODE_TTS


class AudioModeCoordinator:
    """Shares per-guild transition locks and the persisted mode across cogs."""

    def __init__(self, store: ConfigStore | None = None) -> None:
        self.store = store or ConfigStore()
        self._locks: dict[int, asyncio.Lock] = {}

    def lock_for(self, guild_id: int) -> asyncio.Lock:
        return self._locks.setdefault(guild_id, asyncio.Lock())

    def cached_mode(self, guild_id: int) -> str:
        return mode_from_config(self.store.get_cached_sync(guild_id))

    async def get_mode(self, guild_id: int) -> str:
        return mode_from_config(await self.store.get(guild_id))

    async def set_mode(self, guild_id: int, mode: str) -> None:
        if mode not in VALID_AUDIO_MODES:
            raise ValueError(f"unsupported audio mode: {mode}")
        await self.store.set(guild_id, audio_mode=mode)

    def discard_guild(self, guild_id: int) -> None:
        self._locks.pop(guild_id, None)


def get_audio_mode_coordinator(bot: commands.Bot) -> AudioModeCoordinator:
    current = getattr(bot, _BOT_ATTRIBUTE, None)
    if isinstance(current, AudioModeCoordinator):
        return current
    coordinator = AudioModeCoordinator()
    setattr(bot, _BOT_ATTRIBUTE, coordinator)
    return coordinator
