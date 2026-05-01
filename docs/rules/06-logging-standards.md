# Rule 06 — Logging Standards

## Rule
**`print` 금지. 표준 `logging` 모듈만 사용. 로그 레벨과 포맷은 일관성 있게 유지하고, 모든 레코드에 `guild_id` 컨텍스트가 가능하면 포함된다.**

## Why
- `print`는 stdout만 가고 레벨·필터링 불가, 운영 시 로그 회전·집계가 안 됨
- 봇이 여러 서버를 동시에 처리하므로, 어느 guild에서 발생한 일인지 추적할 수 있어야 함
- `traceback.print_exc()` 같은 임시 디버깅 코드가 commit되면 `log.exception`이 동일 정보를 더 안전하게 남김

## 레벨 가이드

| 레벨 | 사용 사례 |
|------|----------|
| `DEBUG` | 개발 중 상세 추적 (메시지 정제 전후, 큐 크기 등) |
| `INFO` | 봇 라이프사이클(login, sync), 명령어 실행, 입/퇴장 알림 발송, voice connect/disconnect |
| `WARNING` | TTS 합성 1회 실패 후 재시도, 권한 부족 안내, 음성 채널 사라짐 |
| `ERROR` | retry 후에도 실패, voice connect 영구 실패 |
| `EXCEPTION` | unhandled 예외 (worker / event handler에서 catch한 모든 것) |

## 포맷
```python
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
```

## How to Apply

### 1. 로거 명명
```python
log = logging.getLogger(__name__)   # 모듈 단위
```
- `__name__`을 쓰면 `cogs.tts_cog`, `cogs.audio_queue` 처럼 자동 계층화

### 2. 컨텍스트 포함
```python
log.info("user joined: guild=%s user=%s", guild.id, member.id)
log.warning("tts failed once, retrying: guild=%s", guild.id)
```

### 3. 예외 로깅
```python
try:
    ...
except Exception:
    log.exception("audio worker failed: guild=%s", guild.id)
    # exception()은 traceback 자동 포함
```

### 4. 외부 라이브러리 로그 조절
```python
logging.getLogger("discord").setLevel(logging.WARNING)   # 너무 verbose
logging.getLogger("aiohttp.client").setLevel(logging.WARNING)
```

## Counter-examples
```python
# ❌
print("user joined", member.name)

# ✅
log.info("user joined: guild=%s user=%s name=%s", guild.id, member.id, member.name)
```

```python
# ❌ 토큰 로깅 가능성
log.info(f"starting with config: {os.environ}")

# ✅
log.info("starting bot")
```

## 운영 팁
- 추후 파일 로테이션이 필요하면 `RotatingFileHandler` 추가 (현재 범위 외)
- `LOG_LEVEL=DEBUG`로 환경변수만 변경하여 임시 추적 가능
