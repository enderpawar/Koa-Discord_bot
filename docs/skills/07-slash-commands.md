# Skill 07 — Slash Commands

## Purpose
관리자/일반 사용자가 봇과 상호작용할 수 있는 TTS 슬래시 명령어를 정의·등록한다.

## 명령어 목록

| 명령어 | 권한 | 파라미터 | 동작 |
|--------|------|---------|------|
| `/목소리` | Manage Channels | `종류: Choice[str]` | TTS 보이스 변경 (한국어 10개 선택지) |
| `/입장` | 일반 | – | 사용자가 참여 중인 음성 채널로 입장하고 그 채널 채팅을 TTS 입력으로 자동 설정 |
| `/퇴장` | 일반 | – | 음성 채널에서 퇴장 |
| `/상태` | 일반 | – | 현재 설정(채널, voice, 발음 사전 규칙 수) 확인 |

## 채널을 고르는 명령은 두지 않는다

`tts_channel_id` / `voice_channel_id` 는 **런타임 상태이지 사용자 설정이 아니다.**
`/입장` 과 음성 패널의 `TTS 켜기` 가 연결할 때마다 두 값을 현재 음성 채널 ID 로
함께 덮어쓴다 (음성 채널 채팅의 ID = 음성 채널 ID).

한때 `/읽기채널`·`/음성채널` 로 둘을 따로 골랐지만, `on_message` 가 재생 조건으로
`voice_channel_id` 에 **실제 연결되어 있을 것**을 요구하고 연결 경로가 항상 두 값을
덮어쓰기 때문에, 분리된 조합은 저장은 되어도 단 한 문장도 재생되지 않았다.
저장되는 척하는 컨트롤을 남기지 않기 위해 명령과 대시보드 드롭다운을 모두 제거했다.
회귀 가드: `tests/unit/test_korean_commands.py::test_tts_channel_pickers_are_gone`.

읽을 대상을 서버가 조정하는 수단은 **발음 사전**(Skill 03)이며, 편집 UI 는 관리자
대시보드에 있다.

## Implementation Sketch
```python
from discord import app_commands
from discord.ext import commands

class TTSCog(commands.Cog):
    def __init__(self, bot, store, queue):
        self.bot, self.store, self.queue = bot, store, queue

    @app_commands.command(name="목소리", description="TTS 보이스 변경")
    @app_commands.rename(voice="종류")
    @app_commands.choices(voice=[
        app_commands.Choice(name="여성 · 차분", value="ko-KR-SunHiNeural"),
        app_commands.Choice(name="남성-자연 (InJoon)", value="ko-KR-InJoonNeural"),
        app_commands.Choice(name="남성-무게감 (BongJin)", value="ko-KR-BongJinNeural"),
        app_commands.Choice(name="남성-친근 (GookMin)", value="ko-KR-GookMinNeural"),
    ])
    async def setvoice(self, itx, voice: app_commands.Choice[str]):
        await self.store.set(itx.guild_id, voice=voice.value)
        await itx.response.send_message(f"보이스: {voice.name}", ephemeral=True)

    @app_commands.command(name="입장", description="현재 음성 채널에서 TTS 시작")
    async def join(self, itx):
        voice_state = getattr(itx.user, "voice", None)
        ch = voice_state.channel if voice_state else None
        if not isinstance(ch, discord.VoiceChannel):
            return await itx.response.send_message(
                "먼저 사용할 음성 채널에 입장하세요",
                ephemeral=True,
            )
        await self.queue.ensure_voice(itx.guild, ch.id)
        await self.store.set(
            itx.guild_id,
            tts_channel_id=ch.id,
            voice_channel_id=ch.id,
        )
        await itx.response.send_message(
            "입장했습니다. 이제 이 음성 채널의 채팅을 읽습니다",
            ephemeral=True,
        )

    @app_commands.command(name="퇴장", description="음성 채널 퇴장")
    async def leave(self, itx):
        if itx.guild.voice_client:
            await itx.guild.voice_client.disconnect()
            await itx.response.send_message("퇴장했습니다", ephemeral=True)
        else:
            await itx.response.send_message("음성 채널에 없습니다", ephemeral=True)

    @app_commands.command(name="상태", description="현재 설정 확인")
    async def status(self, itx):
        cfg = await self.store.get(itx.guild_id)
        # 채널은 `/입장` 이 써 넣은 현재 위치를 읽기만 한다.
        msg = (f"TTS 채널: <#{cfg.get('tts_channel_id', '미설정')}>\n"
               f"음성 채널: <#{cfg.get('voice_channel_id', '미설정')}>\n"
               f"보이스: {cfg.get('voice', 'ko-KR-SunHiNeural')}\n"
               f"발음 사전: {len(cfg.get('pronunciations') or {})}개")
        await itx.response.send_message(msg, ephemeral=True)
```

## 권한 처리
- `app_commands.checks.has_permissions(manage_channels=True)` 데코레이터로 강제
- 권한 부족 시 `app_commands.errors.MissingPermissions` → on_app_command_error에서 한국어 안내

## Sync 정책
- `setup_hook`에서 `await bot.tree.sync()` 한 번만
- 개발 중에는 `await bot.tree.sync(guild=discord.Object(id=GUILD_ID))`로 단일 서버 즉시 반영 가능

## Applied Rules
- [04-secrets-and-security](../rules/04-secrets-and-security.md): 민감 명령(`목소리`)에 권한 체크
- [02-guild-isolation](../rules/02-guild-isolation.md): 모든 명령은 `interaction.guild_id` 컨텍스트로 작동
- [06-logging-standards](../rules/06-logging-standards.md): 명령어 실행을 INFO 레벨로 로깅

## Validation
1. 사용자가 음성 채널에 입장한 뒤 `/입장` 실행 → 봇 입장 + 해당 음성 채널 채팅을 TTS로 읽음
2. `/상태` → 설정값 표시
3. 일반 권한 사용자가 `/목소리` → 권한 부족 안내
