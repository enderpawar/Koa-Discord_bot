# Skill 04 — TTS Engine

## Purpose
정제된 텍스트를 한국어 음성 mp3 파일로 합성한다. 합성 결과를 임시파일로 반환하여 `discord.FFmpegPCMAudio`가 바로 사용할 수 있게 한다.

## API
```python
async def synthesize(text: str, voice: str = "ko-KR-SunHiNeural") -> Path:
    """TTS 합성 후 임시 mp3 파일 경로 반환. 호출 측에서 사용 후 삭제 책임."""
```

## 보이스 선택지 (한국어)
| voice | 특징 |
|-------|------|
| `ko-KR-SunHiNeural` (기본) | 여성, 차분 |
| `ko-KR-InJoonNeural` | 남성, 자연스러움 |
| `ko-KR-BongJinNeural` | 남성, 무게감 |
| `ko-KR-GookMinNeural` | 남성, 친근 |

## Implementation Sketch
```python
import edge_tts, tempfile
from pathlib import Path

async def synthesize(text: str, voice: str = "ko-KR-SunHiNeural") -> Path:
    if not text:
        raise ValueError("empty text")
    fd = tempfile.NamedTemporaryFile(prefix="tts_", suffix=".mp3", delete=False)
    fd.close()
    path = Path(fd.name)
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(path))   # edge-tts 기본 제공
        return path
    except Exception:
        try: path.unlink(missing_ok=True)
        except Exception: pass
        raise
```

> **주의**: `Communicate.save()`는 내부적으로 stream을 모아 파일에 씀. 청크 단위 처리가 필요하면 `Communicate.stream()`을 직접 순회.

## 재시도 정책
- 일시 네트워크 오류(`aiohttp.ClientError`, `edge_tts.exceptions.NoAudioReceived`)에 1회 재시도
- 그 외 예외는 즉시 raise → 호출 측(audio_queue worker)이 잡아서 로그 후 다음 항목 처리

## Applied Rules
- [03-error-resilience](../rules/03-error-resilience.md): 합성 실패가 봇을 죽이지 않게
- [05-async-correctness](../rules/05-async-correctness.md): edge-tts는 async, blocking 호출 없음
- [07-korean-text](../rules/07-korean-text.md): voice는 항상 `ko-KR-*`

## Dependencies
- `edge-tts>=6.1.10`
- 인터넷 연결 (Microsoft Edge TTS endpoint 호출)

## Validation
```bash
python -c "import asyncio; from cogs.tts_engine import synthesize; \
  p = asyncio.run(synthesize('안녕하세요. 테스트입니다.')); print(p); \
  print('size:', p.stat().st_size)"
```
- 파일 크기 > 5KB
- 외부 플레이어로 재생 → 한국어 자연스러운 음성 확인
