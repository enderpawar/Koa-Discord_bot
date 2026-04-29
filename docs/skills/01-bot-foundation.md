# Skill 01 — Bot Foundation

## Purpose
discord.py `Bot` 인스턴스를 생성하고, 필요한 intents·환경변수·cog 로드·슬래시 sync까지 마친 "켜질 준비가 된" 봇 객체를 만든다.

## Inputs
- `DISCORD_TOKEN` (`.env`)
- 선택: `LOG_LEVEL` (기본 `INFO`)

## Outputs
- 실행 가능한 `bot.py` (엔트리포인트)
- 모든 cog가 attach된 `commands.Bot` 인스턴스

## Required Intents
```python
intents = discord.Intents.default()
intents.message_content = True   # on_message에서 본문 접근
intents.members = True           # display_name, voice 이벤트의 member
intents.voice_states = True      # on_voice_state_update
intents.guilds = True            # 기본 켜져있음
```

## Implementation Sketch
```python
# bot.py
import os, logging, shutil, asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("bot")

if shutil.which("ffmpeg") is None:
    raise RuntimeError("FFmpeg가 PATH에 없습니다. 설치 후 다시 실행하세요.")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

class TTSBot(commands.Bot):
    async def setup_hook(self) -> None:
        await self.load_extension("cogs.tts_cog")
        synced = await self.tree.sync()
        log.info("Synced %d slash commands", len(synced))

bot = TTSBot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    log.info("Logged in as %s (id=%s)", bot.user, bot.user.id)

if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])
```

## Applied Rules
- [04-secrets-and-security](../rules/04-secrets-and-security.md): 토큰은 `.env` 경유, 코드에 절대 하드코딩 금지
- [06-logging-standards](../rules/06-logging-standards.md): `logging` 모듈만 사용
- [03-error-resilience](../rules/03-error-resilience.md): FFmpeg 부재 등 사전 검증으로 명확한 실패 메시지

## Dependencies
- `discord.py>=2.4`, `python-dotenv`
- 외부 시스템: FFmpeg, 인터넷 연결

## Validation
1. `pip install -r requirements.txt`
2. `.env`에 `DISCORD_TOKEN=...`
3. `python bot.py` → `Logged in as ...`, `Synced 0 commands` (초기엔 명령 없음)
4. `Ctrl+C`로 정상 종료
