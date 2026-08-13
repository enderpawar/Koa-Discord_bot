# Skill 04 — TTS Engine

## Purpose
정제된 텍스트를 한국어 음성으로 합성한다. 합성 결과를 임시 raw PCM 파일로 반환하여 `discord.FFmpegPCMAudio` 가 디코드 없이 통과 재생할 수 있게 한다.

## API
```python
async def synthesize(
    text: str, voice: str = DEFAULT_VOICE, *, tone: str | None = None
) -> Path:
    """TTS 합성 후 임시 raw PCM 파일 경로 반환 (48kHz / 16-bit / mono).
    호출 측에서 사용 후 삭제 책임."""

async def stream_synthesize(
    text: str, voice: str = DEFAULT_VOICE, *, tone: str | None = None
) -> AsyncIterator[bytes]: ...

async def load_voice_styles(*, force: bool = False) -> dict[str, frozenset[str]]:
    """보이스별 지원 감정 스타일 목록을 Azure 에서 받아 캐시한다."""

def style_for(voice: str, tone: str | None) -> str | None:
    """그 보이스가 실제로 지원하는 스타일만. 없으면 None."""

async def close_session() -> None:
    """봇 종료 시 호출. 모듈 재사용 ClientSession 정리."""
```

## 감정 스타일 (`<mstts:express-as>`)

`tone` 은 `preprocess.detect_tone` 이 정한 라벨이며(Skill 03), 이 모듈이 Azure
스타일로 옮긴다.

- **지원 여부를 코드에 박지 않는다.** 어떤 보이스가 어떤 스타일을 지원하는지는
  보이스마다 다르고 Azure 가 수시로 바꾼다. 기동 시
  `{region}.tts.speech.microsoft.com/cognitiveservices/voices/list` 의 `StyleList`
  를 받아 캐시하고, 그 목록에 있는 스타일만 SSML 에 넣는다.
- **폴백 체인.** `_TONE_STYLE_CHAIN` 이 라벨마다 시도 순서를 정한다
  (`excited → cheerful`). 끝까지 없으면 스타일 없이 읽는다.
- **실패 시 조용히 꺼진다.** 카탈로그를 못 받으면 빈 표가 되어 `style_for` 가 항상
  None 을 돌려주고, 읽기는 그대로 동작한다 (Rule 03).
- **스타일 없는 SSML 은 손대지 않는다.** `mstts` 네임스페이스는 스타일을 실제로
  붙일 때만 선언한다. 카탈로그를 못 받았을 때 예전과 **완전히 동일한** 요청이
  나가야 하기 때문. 회귀 가드: `test_ssml_without_style_is_unchanged`.
- **캐시 키에 톤이 들어간다.** 같은 문장·같은 보이스라도 톤이 다르면 오디오가
  다르다. `audio_queue.PCMCache` 의 키는 `(voice, tone, text)` 다.

## 백엔드 — Azure Speech REST
- 엔드포인트: `https://{AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1`
- 인증: `Ocp-Apim-Subscription-Key` 헤더 (`AZURE_SPEECH_KEY`)
- 요청 본문: SSML (`<speak><voice name="…">…</voice></speak>`)
- 출력 포맷: **`raw-48khz-16bit-mono-pcm`** (`X-Microsoft-OutputFormat` 헤더). Discord 의 native sample rate 와 일치 → `audio_queue` 에서 `FFmpegPCMAudio(..., before_options="-f s16le -ar 48000 -ac 1")` 로 mp3 디코드 단계를 건너뛴다.
- HTTP keep-alive 를 위해 module-level `aiohttp.ClientSession` 재사용 (TLS/DNS 비용 1회만 지불).
- 벤치마크 (`bench_azure_formats.py`, koreacentral, n=12/포맷): mp3 24kHz/48kbps 대비 **TTFB 중앙값 −25ms, TOTAL 중앙값 −23ms**. Azure 가 백엔드에서 인코딩 단계를 건너뛰고 즉시 PCM 을 송신하기 때문 (bytes 는 약 16배 크지만 동일 region 내 대역 충분).

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
        "X-Microsoft-OutputFormat": "raw-48khz-16bit-mono-pcm",
        "User-Agent": "koa-tts-bot",
    }

    fd = tempfile.NamedTemporaryFile(prefix="tts_", suffix=".pcm", delete=False)
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
- 파일 크기 > 100KB (raw PCM 은 mp3 대비 ~16× 큼 — 48kHz × 16-bit × mono = 96KB/sec)
- 외부 플레이어 재생 시 raw PCM 헤더 옵션 필요: `ffplay -f s16le -ar 48000 -ac 1 <file>`
- 또는 봇 안에서 voice channel 입장 후 메시지 → 한국어 자연스러운 음성 확인
