"""Azure Speech REST vs WebSocket 레이턴시 비교 벤치.

목표:
  warm_consecutive (5s 간격, 10회) — 현재 keep-alive 가 잘 동작하는 정상 케이스.
  cold_after_idle  (60s 간격, 5회) — Discord 대화 공백 후 첫 메시지 케이스.
                                     WS 의 가장 큰 이득 가능성 패턴.

WebSocket 프로토콜 (Azure proprietary, edge-tts/Speech SDK 역엔지니어링 기반):
  endpoint: wss://{region}.tts.speech.microsoft.com/cognitiveservices/websocket/v1
  auth: bearer token (issueToken endpoint 로 발급, TTL 10분)
  text frame format:
    Path:{name}\r\nX-RequestId:{hex}\r\nX-Timestamp:{iso}\r\nContent-Type:{ct}\r\n\r\n{body}
  client→server (turn 당):
    1. (한 번만) Path:speech.config        Content-Type:application/json
    2.          Path:synthesis.context     Content-Type:application/json
    3.          Path:ssml                   Content-Type:application/ssml+xml
  server→client:
    text: turn.start, response, audio.metadata, turn.end
    binary: 2-byte BE header length + ASCII headers + audio payload
            -> 첫 binary 프레임 도착 = TTFB
            -> turn.end 도착          = TOTAL
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from xml.sax.saxutils import escape

import aiohttp
from dotenv import load_dotenv

load_dotenv()

AZURE_KEY = os.environ["AZURE_SPEECH_KEY"]
AZURE_REGION = os.environ["AZURE_SPEECH_REGION"]
REST_ENDPOINT = f"https://{AZURE_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
WS_ENDPOINT = f"wss://{AZURE_REGION}.tts.speech.microsoft.com/cognitiveservices/websocket/v1"
TOKEN_ENDPOINT = f"https://{AZURE_REGION}.api.cognitive.microsoft.com/sts/v1.0/issueToken"

VOICE = "ko-KR-SunHiNeural"
OUTPUT_FORMAT = "raw-48khz-16bit-mono-pcm"

SAMPLES = [
    "안녕하세요.",
    "안녕하세요, 디스코드 TTS 봇입니다.",
    "오늘 날씨가 정말 좋네요. 산책하기 딱 좋은 날입니다. 같이 걸을까요?",
]

PATTERNS = {
    "warm_consecutive": dict(interval_s=5.0, count=10),
    "cold_after_idle": dict(interval_s=60.0, count=5),
}


# ---------------------------------------------------------------------------
# Token

async def get_token(session: aiohttp.ClientSession) -> str:
    headers = {"Ocp-Apim-Subscription-Key": AZURE_KEY, "Content-Length": "0"}
    async with session.post(TOKEN_ENDPOINT, headers=headers) as resp:
        resp.raise_for_status()
        return await resp.text()


# ---------------------------------------------------------------------------
# SSML

def build_ssml(text: str) -> str:
    return (
        f'<speak version="1.0" xml:lang="ko-KR">'
        f'<voice name="{VOICE}">{escape(text)}</voice></speak>'
    )


# ---------------------------------------------------------------------------
# REST backend

async def rest_synthesize(
    session: aiohttp.ClientSession, text: str
) -> tuple[float, float, int]:
    """returns (ttfb_ms, total_ms, bytes)."""
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_KEY,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
        "User-Agent": "bench-ws-vs-rest",
    }
    body = build_ssml(text).encode("utf-8")
    start = time.perf_counter()
    ttfb: float | None = None
    nbytes = 0
    async with session.post(REST_ENDPOINT, data=body, headers=headers) as resp:
        resp.raise_for_status()
        async for chunk in resp.content.iter_any():
            if ttfb is None:
                ttfb = (time.perf_counter() - start) * 1000
            nbytes += len(chunk)
    total = (time.perf_counter() - start) * 1000
    return ttfb if ttfb is not None else total, total, nbytes


# ---------------------------------------------------------------------------
# WebSocket backend

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _build_text_frame(path: str, content_type: str, body: str, request_id: str) -> str:
    return (
        f"Path:{path}\r\n"
        f"X-RequestId:{request_id}\r\n"
        f"X-Timestamp:{_now_iso()}\r\n"
        f"Content-Type:{content_type}\r\n"
        f"\r\n"
        f"{body}"
    )


def _parse_text_frame(data: str) -> tuple[dict[str, str], str]:
    if "\r\n\r\n" in data:
        head, body = data.split("\r\n\r\n", 1)
    else:
        head, body = data, ""
    headers: dict[str, str] = {}
    for line in head.split("\r\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    return headers, body


def _parse_binary_frame(data: bytes) -> tuple[dict[str, str], bytes]:
    header_len = int.from_bytes(data[:2], "big")
    head_str = data[2 : 2 + header_len].decode("ascii", errors="replace")
    headers: dict[str, str] = {}
    for line in head_str.strip().split("\r\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    payload = data[2 + header_len :]
    return headers, payload


_SPEECH_CONFIG_BODY = json.dumps({
    "context": {
        "system": {"name": "SpeechSDK", "version": "1.0.0", "build": "bench"},
        "os": {"platform": "Python", "name": "asyncio", "version": "3.10+"},
    }
})


def _synth_context_body() -> str:
    return json.dumps({
        "synthesis": {
            "audio": {
                "metadataOptions": {
                    "bookmarkEnabled": False,
                    "punctuationBoundaryEnabled": False,
                    "sentenceBoundaryEnabled": False,
                    "sessionEndEnabled": True,
                    "visemeEnabled": False,
                    "wordBoundaryEnabled": False,
                },
                "outputFormat": OUTPUT_FORMAT,
            },
            "language": {"autoDetection": False},
        }
    })


@dataclass
class WSConn:
    ws: aiohttp.ClientWebSocketResponse
    speech_config_sent: bool = False
    debug: bool = False

    async def synthesize(self, text: str) -> tuple[float, float, int]:
        """returns (ttfb_ms, total_ms, bytes). 첫 binary frame = TTFB, turn.end = TOTAL."""
        request_id = uuid.uuid4().hex
        # Microsoft 의 일부 구현은 매 turn 마다 speech.config 가 필요하지 않다고 보지만,
        # 안전을 위해 첫 turn 에서만 보낸다 (재사용 시 TTFB 에 영향 없음).
        if not self.speech_config_sent:
            await self.ws.send_str(
                _build_text_frame(
                    "speech.config", "application/json",
                    _SPEECH_CONFIG_BODY, request_id,
                )
            )
            self.speech_config_sent = True

        ssml = build_ssml(text)
        start = time.perf_counter()
        await self.ws.send_str(
            _build_text_frame(
                "synthesis.context", "application/json",
                _synth_context_body(), request_id,
            )
        )
        await self.ws.send_str(
            _build_text_frame(
                "ssml", "application/ssml+xml",
                ssml, request_id,
            )
        )

        ttfb: float | None = None
        nbytes = 0
        while True:
            msg = await self.ws.receive(timeout=15.0)
            if msg.type == aiohttp.WSMsgType.TEXT:
                headers, _ = _parse_text_frame(msg.data)
                path = headers.get("Path", "")
                if self.debug:
                    print(f"      [WS text] Path={path}")
                if path == "turn.end":
                    total = (time.perf_counter() - start) * 1000
                    return (
                        ttfb if ttfb is not None else total,
                        total,
                        nbytes,
                    )
            elif msg.type == aiohttp.WSMsgType.BINARY:
                if ttfb is None:
                    ttfb = (time.perf_counter() - start) * 1000
                _, payload = _parse_binary_frame(msg.data)
                nbytes += len(payload)
                if self.debug:
                    print(f"      [WS bin ] +{len(payload)}B  total={nbytes}")
            elif msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.ERROR,
            ):
                raise RuntimeError(f"WS closed unexpectedly: {msg.type} data={msg.data!r}")


async def open_ws(
    session: aiohttp.ClientSession, token: str, *, debug: bool = False
) -> WSConn:
    conn_id = uuid.uuid4().hex
    url = f"{WS_ENDPOINT}?Authorization=bearer%20{token}&X-ConnectionId={conn_id}"
    ws = await session.ws_connect(
        url,
        headers={"Ocp-Apim-Subscription-Key": AZURE_KEY},
        heartbeat=30.0,
        autoping=True,
        max_msg_size=0,
    )
    return WSConn(ws=ws, debug=debug)


# ---------------------------------------------------------------------------
# Bench harness

@dataclass
class Sample:
    pattern: str
    backend: str
    text_len: int
    ttfb_ms: float
    total_ms: float
    nbytes: int


@dataclass
class Stats:
    pattern: str
    backend: str
    n: int
    ttfb_med: float
    ttfb_p95: float
    total_med: float
    total_p95: float
    bytes_med: int
    samples: list[Sample] = field(default_factory=list)


def _p95(xs: list[float]) -> float:
    if not xs:
        return float("nan")
    if len(xs) < 2:
        return xs[0]
    qs = statistics.quantiles(xs, n=20)  # 95th percentile = 19/20 boundary
    return qs[18]


def _stats(pattern: str, backend: str, samples: list[Sample]) -> Stats:
    ttfbs = sorted(s.ttfb_ms for s in samples)
    totals = sorted(s.total_ms for s in samples)
    bytes_ = sorted(s.nbytes for s in samples)
    return Stats(
        pattern=pattern,
        backend=backend,
        n=len(samples),
        ttfb_med=statistics.median(ttfbs),
        ttfb_p95=_p95(ttfbs),
        total_med=statistics.median(totals),
        total_p95=_p95(totals),
        bytes_med=int(statistics.median(bytes_)),
        samples=samples,
    )


async def run_pattern(
    name: str,
    session: aiohttp.ClientSession,
    ws_conn: WSConn,
    interval_s: float,
    count: int,
) -> list[Sample]:
    print(f"\n--- pattern: {name}  (interval={interval_s}s × {count}) ---")
    out: list[Sample] = []
    total_calls = count * len(SAMPLES)
    call_idx = 0
    for it in range(count):
        if it > 0 and interval_s > 0:
            print(f"  idle {interval_s:.0f}s ...", flush=True)
            await asyncio.sleep(interval_s)
        for text in SAMPLES:
            call_idx += 1
            # REST 와 WS 를 같은 iter 안에서 측정 (네트워크 변동 평탄화).
            try:
                r_ttfb, r_total, r_bytes = await rest_synthesize(session, text)
            except Exception as e:
                print(f"  [{call_idx}/{total_calls}] REST FAIL len={len(text)}: {e}")
                r_ttfb = r_total = float("nan")
                r_bytes = 0
            try:
                w_ttfb, w_total, w_bytes = await ws_conn.synthesize(text)
            except Exception as e:
                print(f"  [{call_idx}/{total_calls}] WS FAIL len={len(text)}: {e}")
                w_ttfb = w_total = float("nan")
                w_bytes = 0

            out.append(Sample(name, "REST", len(text), r_ttfb, r_total, r_bytes))
            out.append(Sample(name, "WS", len(text), w_ttfb, w_total, w_bytes))
            print(
                f"  [{call_idx:2d}/{total_calls}] len={len(text):2d}  "
                f"REST ttfb={r_ttfb:6.1f} total={r_total:6.1f}  |  "
                f"WS ttfb={w_ttfb:6.1f} total={w_total:6.1f}"
            )
    return out


def _print_table(all_stats: list[Stats]) -> None:
    print("\n" + "=" * 96)
    print(
        f"{'pattern':18s} {'backend':6s} {'TTFB med':>10s} {'TTFB p95':>10s} "
        f"{'TOTAL med':>10s} {'TOTAL p95':>10s} {'bytes med':>10s} {'n':>4s}"
    )
    print("-" * 96)
    for s in all_stats:
        print(
            f"{s.pattern:18s} {s.backend:6s} "
            f"{s.ttfb_med:8.1f}ms {s.ttfb_p95:8.1f}ms "
            f"{s.total_med:8.1f}ms {s.total_p95:8.1f}ms "
            f"{s.bytes_med:>10d} {s.n:>4d}"
        )
    print("=" * 96)


def _print_delta(all_stats: list[Stats]) -> tuple[float, float]:
    """returns (warm_ttfb_delta, cold_ttfb_delta) for decision."""
    by = {(s.pattern, s.backend): s for s in all_stats}
    warm_ttfb = warm_total = cold_ttfb = cold_total = float("nan")
    print("\nΔ(WS - REST):")
    for pat in PATTERNS.keys():
        rest = by.get((pat, "REST"))
        ws = by.get((pat, "WS"))
        if rest is None or ws is None:
            continue
        d_ttfb = ws.ttfb_med - rest.ttfb_med
        d_total = ws.total_med - rest.total_med
        print(
            f"  {pat:18s}  TTFB Δ_med = {d_ttfb:+6.1f}ms   "
            f"TOTAL Δ_med = {d_total:+6.1f}ms"
        )
        if pat == "warm_consecutive":
            warm_ttfb, warm_total = d_ttfb, d_total
        elif pat == "cold_after_idle":
            cold_ttfb, cold_total = d_ttfb, d_total
    return warm_ttfb, cold_ttfb


def _decision(warm_ttfb_delta: float, cold_ttfb_delta: float) -> None:
    """음수(WS 가 더 빠름)일수록 절대값이 큰 게 이득.
    인지 임계: 100ms 이하 음성 onset 차이는 일반적으로 비전문가가 변별 어려움.
    """
    print("\n--- 체감 평가 ---")
    print(
        "  100ms 미만 음성 onset 차이는 일반적으로 변별이 어렵고, "
        "30ms 이하는 거의 측정 노이즈."
    )

    # cold 패턴이 결정적
    print("\n--- 권고 ---")
    cold_gain = -cold_ttfb_delta if cold_ttfb_delta == cold_ttfb_delta else 0.0
    if cold_gain != cold_gain:  # NaN
        print("  cold 데이터 부족 → 재실행 필요.")
        return
    if cold_gain > 80:
        print(f"  GO  — cold-after-idle 에서 WS 가 {cold_gain:.0f}ms 빠름. "
              f"default 전환 권장.")
    elif cold_gain > 30:
        print(f"  TOGGLE — cold 에서 {cold_gain:.0f}ms 이득. "
              f"TTS_BACKEND env 변수로 옵션 제공 권장.")
    else:
        print(f"  HOLD — cold 에서 {cold_gain:+.0f}ms 차이로 체감 불가. "
              f"WS 도입 보류, REST 측 keep-alive ping 으로 충분.")
    if warm_ttfb_delta == warm_ttfb_delta:
        warm_gain = -warm_ttfb_delta
        print(f"  warm 패턴: {warm_gain:+.0f}ms 차이 — 정상 케이스에서 영향 미미.")


# ---------------------------------------------------------------------------
# main

async def main() -> None:
    print(f"region={AZURE_REGION}  voice={VOICE}  format={OUTPUT_FORMAT}")
    print(f"patterns={list(PATTERNS.keys())}  samples_per_iter={len(SAMPLES)}")
    print()

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        print("[1/4] token 발급 ...", flush=True)
        token = await get_token(session)
        print(f"      token len={len(token)}  ttl=10min")

        print("[2/4] WS 연결 ...", flush=True)
        ws_conn = await open_ws(session, token, debug=False)
        try:
            print("[3/4] 워밍업 (REST x3 + WS x3) ...", flush=True)
            for i in range(3):
                t0 = time.perf_counter()
                await rest_synthesize(session, SAMPLES[0])
                t1 = time.perf_counter()
                await ws_conn.synthesize(SAMPLES[0])
                t2 = time.perf_counter()
                print(
                    f"      warm-{i+1}: REST={1000*(t1-t0):.0f}ms  "
                    f"WS={1000*(t2-t1):.0f}ms"
                )

            print("[4/4] 패턴별 측정 ...", flush=True)
            all_samples: list[Sample] = []
            for pname, cfg in PATTERNS.items():
                samples = await run_pattern(
                    pname, session, ws_conn,
                    interval_s=cfg["interval_s"], count=cfg["count"],
                )
                all_samples.extend(samples)
        finally:
            try:
                await ws_conn.ws.close()
            except Exception:
                pass

    # 집계
    by_key: dict[tuple[str, str], list[Sample]] = {}
    for s in all_samples:
        if s.ttfb_ms != s.ttfb_ms:  # NaN — 실패 호출 제외
            continue
        by_key.setdefault((s.pattern, s.backend), []).append(s)
    all_stats = [_stats(p, b, ss) for (p, b), ss in by_key.items()]
    # 정렬: pattern 순 → backend 순 (REST 먼저)
    all_stats.sort(key=lambda s: (list(PATTERNS).index(s.pattern), s.backend))

    _print_table(all_stats)
    warm_ttfb_d, cold_ttfb_d = _print_delta(all_stats)
    _decision(warm_ttfb_d, cold_ttfb_d)


if __name__ == "__main__":
    asyncio.run(main())
