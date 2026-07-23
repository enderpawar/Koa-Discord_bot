"""Discord TTS Bot — Phase 1 Foundation.

discord.py Bot 인스턴스 + intents + 환경변수 + 조건부 cog 로드 + 슬래시 sync.
이후 Phase에서 cogs.tts_cog 등이 추가되면 setup_hook이 자동으로 로드한다.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

import discord
from discord.ext import commands
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

# 로드 대상 cog (discord.py extension). 파일이 존재할 때만 로드 → 점진적 부트 안전.
# config_store / preprocess / tts_engine / audio_queue 는 tts_cog 가 import 하는 utility 모듈이므로
# extension 으로 등록하지 않는다. gcp_compute / mc_ping 도 mc_cog 가 쓰는 utility 다.
KNOWN_EXTENSIONS = (
    "cogs.tts_cog",
    "cogs.rank_cog",
    "cogs.admin_cog",
    "cogs.web_admin_cog",
    "cogs.mc_cog",
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


class TTSBot(commands.Bot):
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
