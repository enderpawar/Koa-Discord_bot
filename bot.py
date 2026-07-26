"""Discord TTS Bot — Phase 1 Foundation.

discord.py Bot 인스턴스 + intents + 환경변수 + 조건부 cog 로드 + 슬래시 sync.
이후 Phase에서 cogs.tts_cog 등이 추가되면 setup_hook이 자동으로 로드한다.
"""
from __future__ import annotations

import logging
import os
import random
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("discord").setLevel(logging.WARNING)
log = logging.getLogger("bot")

ROOT = Path(__file__).resolve().parent
COGS_DIR = ROOT / "cogs"
KST = timezone(timedelta(hours=9))
_ACTIVITY_ROTATION_MINUTES = 15
_BASE_ACTIVITIES = (
    "🛋️ 뒹굴거리는 중",
    "🥈 실버 승급전 중!!",
    "🎮 파티원 기다리는 중",
    "🍜 라면 먹고 한 판 더!",
    "💤 로딩 화면에서 꾸벅꾸벅",
    "✨ 오늘도 캐리 받을 준비 완료!",
    "🎧 보이스 채널에서 수다 떠는 중",
    "🧃 잠깐 당 충전하는 중",
)

# 로드 대상 cog (discord.py extension). 파일이 존재할 때만 로드 → 점진적 부트 안전.
# config_store / preprocess / tts_engine / audio_queue 는 tts_cog 가 import 하는 utility 모듈이므로
# extension 으로 등록하지 않는다. gcp_compute / mc_ping 도 mc_cog 가 쓰는 utility 다.
# valorant_api / valorant_store 도 valorant_cog 가 쓰는 utility 다.
# lol_api / lol_store 도 lol_cog 가 쓰는 utility 다.
KNOWN_EXTENSIONS = (
    "cogs.tts_cog",
    "cogs.rank_cog",
    "cogs.admin_cog",
    "cogs.web_admin_cog",
    "cogs.mc_cog",
    "cogs.valorant_cog",
    "cogs.lol_cog",
    "cogs.party_cog",
    "cogs.fortune_cog",
)


def _check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "FFmpeg가 PATH에 없습니다. 설치 후 다시 실행하세요. "
            "(Windows: https://www.gyan.dev/ffmpeg/builds/)"
        )


def _build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    intents.voice_states = True
    return intents


def _activity_names(now: datetime | None = None) -> tuple[str, ...]:
    """항상 쓸 문구에 한국 시간대·요일 조건 문구를 더한다."""
    current = now or datetime.now(KST)
    if 5 <= current.hour < 11:
        time_activity = "☀️ 모닝 큐 돌리는 중"
    elif 11 <= current.hour < 18:
        time_activity = "🎯 오늘의 첫 승 챙기는 중"
    elif 18 <= current.hour:
        time_activity = "🌆 저녁 파티 모집 중"
    else:
        time_activity = "🌙 새벽반과 밤샘 큐 돌리는 중"

    activities = _BASE_ACTIVITIES + (time_activity,)
    if current.weekday() >= 5:
        activities += ("🎉 주말 풀파티 즐기는 중",)
    return activities


def _build_activity(
    *,
    previous: str | None = None,
    now: datetime | None = None,
    chooser=random.choice,
) -> discord.Game:
    """직전 문구를 피해서 다음 게임 상태를 고른다."""
    activities = _activity_names(now)
    candidates = tuple(name for name in activities if name != previous) or activities
    return discord.Game(name=chooser(candidates))


class TTSBot(commands.Bot):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_activity_name: str | None = None

    @tasks.loop(minutes=_ACTIVITY_ROTATION_MINUTES)
    async def rotate_activity(self) -> None:
        activity = _build_activity(previous=self._last_activity_name)
        try:
            await self.change_presence(activity=activity)
        except Exception:
            log.exception("failed to update bot activity")
            return
        self._last_activity_name = activity.name
        log.info("updated bot activity: %s", activity.name)

    async def close(self) -> None:
        if self.rotate_activity.is_running():
            self.rotate_activity.cancel()
        await super().close()

    async def setup_hook(self) -> None:
        for ext in KNOWN_EXTENSIONS:
            module_path = ROOT / Path(*ext.split(".")).with_suffix(".py")
            if not module_path.exists():
                continue
            try:
                await self.load_extension(ext)
                log.info("loaded extension: %s", ext)
            except Exception:
                log.exception("failed to load extension: %s", ext)

        guild_id = os.getenv("TEST_GUILD_ID")
        if guild_id and guild_id.isdigit():
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("synced %d slash commands to guild %s", len(synced), guild_id)
        else:
            synced = await self.tree.sync()
            log.info("synced %d slash commands (global)", len(synced))


bot = TTSBot(command_prefix="!", intents=_build_intents())


@bot.event
async def on_ready() -> None:
    if not bot.rotate_activity.is_running():
        bot.rotate_activity.start()
    log.info("logged in as %s (id=%s)", bot.user, getattr(bot.user, "id", "?"))


def main() -> None:
    _check_ffmpeg()
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        log.error("DISCORD_TOKEN이 설정되지 않았습니다. .env를 확인하세요.")
        sys.exit(1)
    bot.run(token)


if __name__ == "__main__":
    main()
