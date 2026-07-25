"""Measure the production Azure streaming path without joining Discord voice.

Usage:
    python scripts/benchmark_tts_latency.py --samples 20

The warm first-chunk SLO matches the point at which the Discord AudioSource can
start emitting audible PCM.  The script exits non-zero when a request fails or
the p95 target is missed, so it can also be used as a deployment smoke test.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import statistics
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from cogs.tts_engine import DEFAULT_VOICE, close_session, stream_synthesize  # noqa: E402


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


async def _measure(text: str, voice: str) -> tuple[float, float, int]:
    started = time.perf_counter()
    first_chunk_ms: float | None = None
    audio_bytes = 0
    async for chunk in stream_synthesize(text, voice):
        if first_chunk_ms is None:
            first_chunk_ms = (time.perf_counter() - started) * 1000
        audio_bytes += len(chunk)
    total_ms = (time.perf_counter() - started) * 1000
    if first_chunk_ms is None or audio_bytes == 0:
        raise RuntimeError("TTS returned no audio")
    return first_chunk_ms, total_ms, audio_bytes


async def _run(samples: int, p95_slo_ms: float, voice: str) -> int:
    failures = 0
    first_chunks: list[float] = []
    totals: list[float] = []
    try:
        # Exclude the intentional TCP/TLS/WebSocket cold open from the warm SLO.
        await _measure("연결 준비", voice)
        for index in range(samples):
            try:
                first_ms, total_ms, _ = await _measure(
                    f"상용 티티에스 지연 측정 {index + 1}", voice
                )
            except Exception as exc:
                failures += 1
                print(f"sample={index + 1} failed={exc.__class__.__name__}")
                continue
            first_chunks.append(first_ms)
            totals.append(total_ms)
    finally:
        await close_session()

    if not first_chunks:
        print(f"result=FAIL samples={samples} failures={failures}")
        return 1

    first_p50 = statistics.median(first_chunks)
    first_p95 = _percentile(first_chunks, 0.95)
    total_p50 = statistics.median(totals)
    total_p95 = _percentile(totals, 0.95)
    passed = failures == 0 and first_p95 <= p95_slo_ms
    print(
        f"result={'PASS' if passed else 'FAIL'} samples={samples} "
        f"failures={failures} first_chunk_p50={first_p50:.1f}ms "
        f"first_chunk_p95={first_p95:.1f}ms total_p50={total_p50:.1f}ms "
        f"total_p95={total_p95:.1f}ms slo_p95={p95_slo_ms:.1f}ms"
    )
    return 0 if passed else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--p95-slo-ms", type=float, default=700.0)
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be at least 1")
    raise SystemExit(asyncio.run(_run(args.samples, args.p95_slo_ms, args.voice)))


if __name__ == "__main__":
    main()
