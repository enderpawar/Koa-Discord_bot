"""마인크래프트 서버 리스트 핑 (asyncio).

VM이 RUNNING이어도 자바 프로세스는 1~2분 더 로딩한다. "켜짐"을 VM 상태가
아니라 이 핑의 성공으로 판정해야 파티원이 헛접속하지 않는다.

프로토콜은 cobblemon-server/gcp/mc-idle-check.py 와 동일한 핸드셰이크 →
status request → JSON 응답이다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
from dataclasses import dataclass

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 767  # MC 1.21.1
_MAX_RESPONSE_BYTES = 1 << 20  # 1MB. 손상된 응답으로 메모리가 튀는 걸 막는다.


@dataclass(frozen=True)
class ServerStatus:
    online: int
    max_players: int
    version: str


def _write_varint(value: int) -> bytes:
    out = b""
    while True:
        b = value & 0x7F
        value >>= 7
        out += bytes([b | 0x80]) if value else bytes([b])
        if not value:
            return out


async def _read_varint(reader: asyncio.StreamReader) -> int:
    result = 0
    shift = 0
    while True:
        data = await reader.readexactly(1)
        b = data[0]
        result |= (b & 0x7F) << shift
        shift += 7
        if not (b & 0x80):
            return result
        if shift > 35:
            raise ValueError("VarInt가 너무 김")


async def ping(host: str, port: int = 25565, timeout: float = 5.0) -> ServerStatus:
    """서버 상태를 반환한다. 실패 시 예외를 던진다."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout
    )
    try:
        addr = host.encode()
        handshake = (
            _write_varint(0x00)
            + _write_varint(PROTOCOL_VERSION)
            + _write_varint(len(addr))
            + addr
            + struct.pack(">H", port)
            + _write_varint(1)  # next state: status
        )
        writer.write(_write_varint(len(handshake)) + handshake)
        writer.write(_write_varint(1) + _write_varint(0x00))  # status request
        await writer.drain()

        async def _read_payload() -> bytes:
            await _read_varint(reader)  # 패킷 전체 길이
            if await _read_varint(reader) != 0x00:
                raise ValueError("예상치 못한 패킷 ID")
            length = await _read_varint(reader)
            if length <= 0 or length > _MAX_RESPONSE_BYTES:
                raise ValueError(f"응답 길이가 비정상: {length}")
            return await reader.readexactly(length)

        raw = await asyncio.wait_for(_read_payload(), timeout=timeout)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            # 상대가 이미 끊은 경우. 응답은 이미 읽었으므로 무시해도 된다.
            pass

    data = json.loads(raw.decode("utf-8"))
    players = data.get("players") or {}
    version = (data.get("version") or {}).get("name") or "?"
    return ServerStatus(
        online=int(players.get("online", 0)),
        max_players=int(players.get("max", 0)),
        version=str(version),
    )


async def try_ping(host: str, port: int = 25565, timeout: float = 5.0) -> ServerStatus | None:
    """실패를 None으로 바꿔주는 래퍼. 폴링 루프용."""
    try:
        return await ping(host, port, timeout=timeout)
    except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError, ValueError,
            json.JSONDecodeError) as exc:
        log.debug("mc ping failed: %s: %s", type(exc).__name__, exc)
        return None
