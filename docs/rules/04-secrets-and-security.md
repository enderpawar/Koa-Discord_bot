# Rule 04 — Secrets & Security

## Rule
**시크릿(Discord 토큰, 외부 API 키)은 `.env`에서만 로드된다. 코드/로그/응답 메시지/커밋 어디에도 노출되지 않는다. 권한이 필요한 명령어는 반드시 권한 체크를 거친다.**

## Why
- Discord 봇 토큰이 유출되면 봇 계정이 도용되어 spam·abuse·계정 정지 위험
- 누군가 `cat .env` 한 줄로 끝나면 안 됨; 깃에 실수로 커밋되어 push되면 그 자체로 사고
- 슬래시 명령어 권한이 부실하면 임의 사용자가 `/settts`로 다른 채널로 변경하여 원치 않는 음성 송출 가능

## How to Apply

### 1. 환경변수
```python
# .env
DISCORD_TOKEN=...
LOG_LEVEL=INFO
```
- `python-dotenv`로 `load_dotenv()` 후 `os.environ["DISCORD_TOKEN"]`
- **절대** 코드에 하드코딩 금지

### 2. .gitignore (필수)
```
.env
config.json
__pycache__/
*.pyc
.venv/
*.mp3
*.tmp
```

### 3. 토큰 로깅 금지
```python
# ❌ log.info("starting with token %s", token)
# ✅ log.info("starting bot")
```
또한 예외 traceback에 토큰이 들어가는 일이 없도록, `bot.run(os.environ["DISCORD_TOKEN"])`만 사용하고 토큰을 변수로 보관하지 않음.

### 4. 슬래시 명령어 권한
- `/settts`, `/setvc`, `/setvoice` → `manage_channels=True` 권한 필요
- 데코레이터 `@app_commands.checks.has_permissions(manage_channels=True)`
- 권한 부족 시 `MissingPermissions` → 한국어 안내

### 5. config.json 보호
- PII(이메일, 실명) 저장 금지. 채널 ID·voice 식별자만.
- 백업 시에도 권한 분리

### 6. 외부 입력 처리
- `clean_message`에서 사용자 입력은 정규식 통과
- `Path` 조작 시 사용자 입력으로 경로 합성 금지 (현재 설계상 없음)

## .env.example (커밋 가능)
```
# .env.example - 실제 값은 .env 파일에 작성하고 .gitignore에 추가
DISCORD_TOKEN=your_token_here
LOG_LEVEL=INFO
```

## 토큰 유출 시 즉시 행동
1. https://discord.com/developers/applications 에서 토큰 reset
2. git history에서 제거 (`git filter-repo`)
3. 봇 활동 로그에 비정상 행동 점검
