"""edge-tts vs Azure Speech REST 지연시간 비교."""
import asyncio
import os
import statistics
import time
from pathlib import Path

import aiohttp
import edge_tts
from dotenv import load_dotenv

load_dotenv()

AZURE_KEY = os.environ["AZURE_SPEECH_KEY"]
AZURE_REGION = os.environ["AZURE_SPEECH_REGION"]

VOICE = "ko-KR-SunHiNeural"
SAMPLES = [
    "안녕하세요, 디스코드 TTS 봇입니다.",
    "오늘 날씨가 정말 좋네요. 산책하기 딱 좋은 날입니다.",
    "지금부터 음성 합성 속도를 비교 측정하겠습니다.",
]
ITERATIONS = 5  # per sample


async def bench_edge(text: str) -> tuple[float, float, int]:
    """returns (ttfb_ms, total_ms, bytes)."""
    comm = edge_tts.Communicate(text, VOICE)
    start = time.perf_counter()
    ttfb = None
    total_bytes = 0
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            if ttfb is None:
                ttfb = (time.perf_counter() - start) * 1000
            total_bytes += len(chunk["data"])
    total = (time.perf_counter() - start) * 1000
    return ttfb or total, total, total_bytes


async def bench_azure(session: aiohttp.ClientSession, text: str) -> tuple[float, float, int]:
    """Azure Speech REST API. returns (ttfb_ms, total_ms, bytes)."""
    url = f"https://{AZURE_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    ssml = (
        f'<speak version="1.0" xml:lang="ko-KR">'
        f'<voice name="{VOICE}">{text}</voice></speak>'
    )
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_KEY,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
        "User-Agent": "bench-tts",
    }
    start = time.perf_counter()
    ttfb = None
    total_bytes = 0
    async with session.post(url, data=ssml, headers=headers) as resp:
        resp.raise_for_status()
        async for chunk in resp.content.iter_any():
            if ttfb is None:
                ttfb = (time.perf_counter() - start) * 1000
            total_bytes += len(chunk)
    total = (time.perf_counter() - start) * 1000
    return ttfb or total, total, total_bytes


def summarize(label: str, results: list[tuple[float, float, int]]) -> None:
    ttfbs = [r[0] for r in results]
    totals = [r[1] for r in results]
    bytes_ = [r[2] for r in results]
    print(f"\n[{label}]  n={len(results)}")
    print(f"  TTFB   mean={statistics.mean(ttfbs):7.1f}ms  median={statistics.median(ttfbs):7.1f}ms  min={min(ttfbs):7.1f}ms  max={max(ttfbs):7.1f}ms")
    print(f"  TOTAL  mean={statistics.mean(totals):7.1f}ms  median={statistics.median(totals):7.1f}ms  min={min(totals):7.1f}ms  max={max(totals):7.1f}ms")
    print(f"  bytes  mean={statistics.mean(bytes_):.0f}")


async def main() -> None:
    edge_results: list[tuple[float, float, int]] = []
    azure_results: list[tuple[float, float, int]] = []

    # warm-up (DNS, TLS handshakes 등 제외)
    print("warm-up...")
    async with aiohttp.ClientSession() as s:
        await bench_edge(SAMPLES[0])
        await bench_azure(s, SAMPLES[0])

    print(f"benchmarking ({ITERATIONS} iter × {len(SAMPLES)} samples)...")
    async with aiohttp.ClientSession() as s:
        for i in range(ITERATIONS):
            for text in SAMPLES:
                er = await bench_edge(text)
                ar = await bench_azure(s, text)
                edge_results.append(er)
                azure_results.append(ar)
                print(f"  iter {i+1} len={len(text):3d}  edge ttfb={er[0]:6.1f}ms total={er[1]:6.1f}ms | azure ttfb={ar[0]:6.1f}ms total={ar[1]:6.1f}ms")

    summarize("edge-tts (websocket)", edge_results)
    summarize("Azure Speech (REST)", azure_results)

    edge_med_total = statistics.median([r[1] for r in edge_results])
    az_med_total = statistics.median([r[1] for r in azure_results])
    edge_med_ttfb = statistics.median([r[0] for r in edge_results])
    az_med_ttfb = statistics.median([r[0] for r in azure_results])
    print("\n=== 결론 ===")
    print(f"  TTFB   median 차이: edge {edge_med_ttfb:.1f}ms vs azure {az_med_ttfb:.1f}ms  ({(edge_med_ttfb-az_med_ttfb):+.1f}ms 차이)")
    print(f"  TOTAL  median 차이: edge {edge_med_total:.1f}ms vs azure {az_med_total:.1f}ms  ({(edge_med_total-az_med_total):+.1f}ms 차이)")
    if az_med_total < edge_med_total:
        ratio = edge_med_total / az_med_total
        print(f"  → Azure 가 {ratio:.2f}× 빠름")
    else:
        ratio = az_med_total / edge_med_total
        print(f"  → edge-tts 가 {ratio:.2f}× 빠름")


if __name__ == "__main__":
    asyncio.run(main())
