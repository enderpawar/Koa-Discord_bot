# Skill 07 — Slash Commands

## Purpose
관리자/일반 사용자가 봇과 상호작용할 수 있는 6개의 슬래시 명령어를 정의·등록한다.

## 명령어 목록

| 명령어 | 권한 | 파라미터 | 동작 |
|--------|------|---------|------|
| `/settts` | Manage Channels | `channel: TextChannel` | TTS 읽기 대상 텍스트 채널 설정 |
| `/setvc` | Manage Channels | `channel: VoiceChannel` | 봇이 음성 출력할 채널 설정 |
| `/setvoice` | Manage Channels | `voice: Choice[str]` | TTS 보이스 변경 (한국어 10개 선택지) |
| `/join` | 일반 | – | 설정된 음성 채널로 즉시 입장 |
| `/leave` | 일반 | – | 음성 채널에서 퇴장 |
| `/status` | 일반 | – | 현재 설정(채널 ID, voice) 확인 |

## Implementation Sketch
```python
from discord import app_commands
from discord.ext import commands

class TTSCog(commands.Cog):
    def __init__(self, bot, store, queue):
        self.bot, self.store, self.queue = bot, store, queue

    @app_commands.command(name="settts", description="TTS 채팅 채널 지정")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def settts(self, itx: discord.Interaction, channel: discord.TextChannel):
        await self.store.set(itx.guild_id, tts_channel_id=channel.id)
        await itx.response.send_message(f"TTS 채널을 {channel.mention}으로 설정", ephemeral=True)

    @app_commands.command(name="setvc", description="음성 출력 채널 지정")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def setvc(self, itx, channel: discord.VoiceChannel):
        await self.store.set(itx.guild_id, voice_channel_id=channel.id)
        await itx.response.send_message(f"음성 채널을 {channel.mention}으로 설정", ephemeral=True)

    @app_commands.command(name="setvoice", description="TTS 보이스 변경")
    @app_commands.choices(voice=[
        app_commands.Choice(name="여성-차분 (SunHi)", value="ko-KR-SunHiNeural"),
        app_commands.Choice(name="남성-자연 (InJoon)", value="ko-KR-InJoonNeural"),
        app_commands.Choice(name="남성-무게감 (BongJin)", value="ko-KR-BongJinNeural"),
        app_commands.Choice(name="남성-친근 (GookMin)", value="ko-KR-GookMinNeural"),
    ])
    async def setvoice(self, itx, voice: app_commands.Choice[str]):
        await self.store.set(itx.guild_id, voice=voice.value)
        await itx.response.send_message(f"보이스: {voice.name}", ephemeral=True)

    @app_commands.command(name="join", description="음성 채널 입장")
    async def join(self, itx):
        cfg = await self.store.get(itx.guild_id)
        ch = itx.guild.get_channel(cfg.get("voice_channel_id", 0))
        if not ch:
            return await itx.response.send_message("먼저 /setvc로 음성 채널을 지정하세요", ephemeral=True)
        await self.queue.connect(itx.guild, ch.id)
        await itx.response.send_message("입장했습니다", ephemeral=True)

    @app_commands.command(name="leave", description="음성 채널 퇴장")
    async def leave(self, itx):
        if itx.guild.voice_client:
            await itx.guild.voice_client.disconnect()
            await itx.response.send_message("퇴장했습니다", ephemeral=True)
        else:
            await itx.response.send_message("음성 채널에 없습니다", ephemeral=True)

    @app_commands.command(name="status", description="현재 설정 확인")
    async def status(self, itx):
        cfg = await self.store.get(itx.guild_id)
        msg = (f"TTS 채널: <#{cfg.get('tts_channel_id', '미설정')}>\n"
               f"음성 채널: <#{cfg.get('voice_channel_id', '미설정')}>\n"
               f"보이스: {cfg.get('voice', 'ko-KR-SunHiNeural')}")
        await itx.response.send_message(msg, ephemeral=True)
```

## 권한 처리
- `app_commands.checks.has_permissions(manage_channels=True)` 데코레이터로 강제
- 권한 부족 시 `app_commands.errors.MissingPermissions` → on_app_command_error에서 한국어 안내

## Sync 정책
- `setup_hook`에서 `await bot.tree.sync()` 한 번만
- 개발 중에는 `await bot.tree.sync(guild=discord.Object(id=GUILD_ID))`로 단일 서버 즉시 반영 가능

## Applied Rules
- [04-secrets-and-security](../rules/04-secrets-and-security.md): 민감 명령(`settts`, `setvc`, `setvoice`)에 권한 체크
- [02-guild-isolation](../rules/02-guild-isolation.md): 모든 명령은 `interaction.guild_id` 컨텍스트로 작동
- [06-logging-standards](../rules/06-logging-standards.md): 명령어 실행을 INFO 레벨로 로깅

## Validation
1. 테스트 서버에서 `/settts` `/setvc` `/setvoice` `/join` 순으로 실행 → 응답 정상
2. `/status` → 설정값 표시
3. 일반 권한 사용자가 `/settts` → 권한 부족 안내
