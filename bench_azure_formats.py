"""Azure Speech REST 출력 포맷별 지연시간 비교.

목표: synthesize() 의 TTFB(first audio byte) / TOTAL / bytes 를 포맷별로 측정.

핵심 가설:
1. Opus/PCM 직출력 → FFmpeg mp3 디코드 비용을 우회 가능 (런타임 효과는 별도지만,
   bytes 가 작거나 같으면 다운로드 자체도 개선됨)
2. 16kHz 32kbps mp3 는 다운로드가 가장 짧아 TOTAL 우위
3. TTFB 는 포맷 무관하게 Azure 백엔드 합성 시작 지연이 지배적이어야 함

포맷 후보 (Azure Speech REST X-Microsoft-OutputFormat):
  - audio-24khz-48kbitrate-mono-mp3  (현재 baseline)
  - audio-16khz-32kbitrate-mono-mp3  (저비트레이트)
  - audio-48khz-96kbitrate-mono-mp3  (고품질)
  - raw-48khz-16bit-mono-pcm         (FFmpeg bypass 후보 — discord.PCMAudio)
  - ogg-48khz-16bit-mono-opus        (FFmpeg bypass 후보 — Opus 직접)
  - webm-24khz-16bit-mono-opus       (저대역 Opus)
"""
from __future__ import annotations

import asyncio
import os
import statistics
import time

import aiohttp
from dotenv import load_dotenv

load_dotenv()

AZURE_KEY = os.environ["AZURE_SPEECH_KEY"]
AZURE_REGION = os.environ["AZURE_SPEECH_REGION"]
ENDPOINT = f"https://{AZURE_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"

VOICE = "ko-KR-SunHiNeural"
SAMPLES = [
    "안녕하세요.",
    "안녕하세요, 디스코드 TTS 봇입니다.",
    "오늘 날씨가 정말 좋네요. 산책하기 딱 좋은 날입니다. 같이 걸을까요?",
]
ITERATIONS = 4

FORMATS = [
    "audio-24khz-48kbitrate-mono-mp3",      # baseline (현재 운영)
    "audio-16khz-32kbitrate-mono-mp3",      # smaller mp3
    "audio-48khz-96kbitrate-mono-mp3",      # higher mp3
    "raw-48khz-16bit-mono-pcm",             # FFmpeg bypass (PCMAudio)
    "ogg-48khz-16bit-mono-opus",            # FFmpeg bypass (Opus)
    "webm-24khz-16bit-mono-opus",           # smaller Opus
]


def _ssml(text: str) -> bytes:
    from xml.sax.saxutils import escape

    return (
        f'<speak version="1.0" xml:lang="ko-KR">'
        f'<voice name="{VOICE}">{escape(text)}</voice></speak>'
    ).encode("utf-8")


async def _bench_one(
    session: aiohttp.ClientSession, fmt: str, text: str
) -> tuple[float, float, int]:
    """returns (ttfb_ms, total_ms, bytes)."""
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_KEY,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": fmt,
        "User-Agent": "bench-azure-formats",
    }
    body = _ssml(text)
    start = time.perf_counter()
    ttfb: float | None = None
    total_bytes = 0
    async with session.post(ENDPOINT, data=body, headers=headers) as resp:
        resp.raise_for_status()
        async for chunk in resp.content.iter_any():
            if ttfb is None:
                ttfb = (time.perf_counter() - start) * 1000
            total_bytes += len(chunk)
    total = (time.perf_counter() - start) * 1000
    return ttfb if ttfb is not None else total, total, total_bytes


def _summarize(label: str, results: list[tuple[float, float, int]]) -> dict:
    ttfbs = [r[0] for r in results]
    totals = [r[1] for r in results]
    bytes_ = [r[2] for r in results]
    return {
        "label": label,
        "n": len(results),
        "ttfb_med": statistics.median(ttfbs),
        "ttfb_mean": statistics.mean(ttfbs),
        "ttfb_min": min(ttfbs),
        "ttfb_max": max(ttfbs),
        "total_med": statistics.median(totals),
        "total_mean": statistics.mean(totals),
        "bytes_med": statistics.median(bytes_),
    }


async def main() -> None:
    timeout = aiohttp.ClientTimeout(total=20)
    print(f"region={AZURE_REGION}  voice={VOICE}")
    print(f"samples={len(SAMPLES)} iterations={ITERATIONS} formats={len(FORMATS)}")
    print()

    async with aiohttp.ClientSession(timeout=timeout) as session:
        # warm-up: TLS handshake + DNS 캐시 + Azure backend 워밍업
        print("warm-up (3 calls)...")
        for _ in range(3):
            await _bench_one(session, FORMATS[0], SAMPLES[0])

        per_format: dict[str, list[tuple[float, float, int]]] = {f: [] for f in FORMATS}

        # 라운드 로빈: 포맷-바이어스 (시점/네트워크 변동)를 평탄화
        for it in range(ITERATIONS):
            for text in SAMPLES:
                for fmt in FORMATS:
                    try:
                        r = await _bench_one(session, fmt, text)
                    except Exception as e:
                        print(f"  FAIL fmt={fmt} text_len={len(text)}: {e}")
                        continue
                    per_format[fmt].append(r)
                    print(
                        f"  it={it+1} len={len(text):2d} {fmt:40s} "
                        f"ttfb={r[0]:6.1f}ms total={r[1]:6.1f}ms bytes={r[2]:6d}"
                    )

    summaries = [_summarize(f, per_format[f]) for f in FORMATS if per_format[f]]
    summaries.sort(key=lambda s: s["ttfb_med"])

    print("\n" + "=" * 110)
    print(f"{'format':40s} {'TTFB med':>10s} {'TTFB mean':>10s} "
          f"{'TOTAL med':>10s} {'bytes med':>10s} {'n':>4s}")
    print("-" * 110)
    for s in summaries:
        print(
            f"{s['label']:40s} {s['ttfb_med']:8.1f}ms {s['ttfb_mean']:8.1f}ms "
            f"{s['total_med']:8.1f}ms {int(s['bytes_med']):>10d} {s['n']:>4d}"
        )
    print("=" * 110)

    base = next((s for s in summaries
                 if s["label"] == "audio-24khz-48kbitrate-mono-mp3"), None)
    if base is not None:
        print(f"\nbaseline = audio-24khz-48kbitrate-mono-mp3 "
              f"(TTFB med {base['ttfb_med']:.1f}ms, TOTAL med {base['total_med']:.1f}ms)")
        print("\n  format                                   ΔTTFB     ΔTOTAL   bytes ratio")
        for s in summaries:
            d_ttfb = s["ttfb_med"] - base["ttfb_med"]
            d_total = s["total_med"] - base["total_med"]
            ratio = s["bytes_med"] / base["bytes_med"] if base["bytes_med"] else 0
            print(
                f"  {s['label']:40s} {d_ttfb:+7.1f}ms {d_total:+7.1f}ms  {ratio:.2f}×"
            )


if __name__ == "__main__":
    asyncio.run(main())
