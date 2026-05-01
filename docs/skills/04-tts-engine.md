# Skill 04 — TTS Engine

## Purpose
정제된 텍스트를 한국어 음성 mp3 파일로 합성한다. 합성 결과를 임시파일로 반환하여 `discord.FFmpegPCMAudio`가 바로 사용할 수 있게 한다.

## API
```python
async def synthesize(text: str, voice: str = "ko-KR-SunHiNeural") -> Path:
    """TTS 합성 후 임시 mp3 파일 경로 반환. 호출 측에서 사용 후 삭제 책임."""

async def close_session() -> None:
    """봇 종료 시 호출. 모듈 재사용 ClientSession 정리."""
```

## 백엔드 — Azure Speech REST
- 엔드포인트: `https://{AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1`
- 인증: `Ocp-Apim-Subscription-Key` 헤더 (`AZURE_SPEECH_KEY`)
- 요청 본문: SSML (`<speak><voice name="…">…</voice></speak>`)
- 출력 포맷: `audio-24khz-48kbitrate-mono-mp3` (`X-Microsoft-OutputFormat` 헤더)
- HTTP keep-alive 를 위해 module-level `aiohttp.ClientSession` 재사용 → 연속 합성 시 edge-tts 대비 약 2.4× TTFB 개선 (벤치마크 `bench_tts.py` 참조)

## 보이스 선택지 (한국어)
| voice | 특징 |
|-------|------|
| `ko-KR-SunHiNeural` (기본) | 여성, 차분 |
| `ko-KR-InJoonNeural` | 남성, 자연스러움 |
| `ko-KR-BongJinNeural` | 남성, 무게감 |
| `ko-KR-GookMinNeural` | 남성, 친근 |

## Implementation Sketch
```python
import os, aiohttp, tempfile
from pathlib import Path
from xml.sax.saxutils import escape

_session: aiohttp.ClientSession | None = None

async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    return _session

async def synthesize(text: str, voice: str = "ko-KR-SunHiNeural") -> Path:
    if not text:
        raise ValueError("empty text")

    key = os.environ["AZURE_SPEECH_KEY"]
    region = os.environ["AZURE_SPEECH_REGION"]
    ssml = (
        f'<speak version="1.0" xml:lang="ko-KR">'
        f'<voice name="{voice}">{escape(text)}</voice></speak>'
    ).encode("utf-8")
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
        "User-Agent": "nothing-tts-bot",
    }

    fd = tempfile.NamedTemporaryFile(prefix="tts_", suffix=".mp3", delete=False)
    fd.close()
    path = Path(fd.name)
    try:
        session = await _get_session()
        url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        async with session.post(url, data=ssml, headers=headers) as resp:
            resp.raise_for_status()
            with path.open("wb") as f:
                async for chunk in resp.content.iter_any():
                    f.write(chunk)
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise
```

> **주의**: Azure Speech 키가 잘못되면 401 ClientResponseError. 무료(F0) 티어 한도(월 500K char) 초과 시 429. 둘 다 `aiohttp.ClientError` 계열로 1회 retry 후 호출 측에 전달된다.

## 재시도 정책
- 일시 네트워크 오류(`aiohttp.ClientError`, `asyncio.TimeoutError`)에 1회 재시도
- 그 외 예외는 즉시 raise → 호출 측(audio_queue worker)이 잡아서 로그 후 다음 항목 처리

## Applied Rules
- [03-error-resilience](../rules/03-error-resilience.md): 합성 실패가 봇을 죽이지 않게
- [05-async-correctness](../rules/05-async-correctness.md): aiohttp 는 async, blocking 호출 없음
- [07-korean-text](../rules/07-korean-text.md): voice는 항상 `ko-KR-*`, SSML `xml:lang="ko-KR"`

## Dependencies
- `aiohttp>=3.9` (discord.py[voice] 의 transitive 이지만 명시 의존)
- 환경변수 `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` (e.g. `koreacentral`)
- 인터넷 연결 (Azure Speech endpoint 호출)

## Validation
```bash
python -c "import asyncio; from cogs.tts_engine import synthesize, close_session; \
  p = asyncio.run(synthesize('안녕하세요. 테스트입니다.')); print(p); \
  print('size:', p.stat().st_size); asyncio.run(close_session())"
```
- 파일 크기 > 5KB
- 외부 플레이어로 재생 → 한국어 자연스러운 음성 확인
