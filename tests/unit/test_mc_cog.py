"""마인크래프트 전원 제어 cog 단위 테스트.

암호 검증 / 시도 횟수 제한 / 설정 누락 판정 / 주소 표기 / 핑 파서를 다룬다.
Discord 상호작용 자체는 통합 테스트 영역이라 여기서는 모킹한다.
"""
from __future__ import annotations

import asyncio
import base64
import json
import struct

import pytest

from cogs import mc_cog, mc_ping
from cogs.gcp_compute import GcpComputeClient, GcpConfigError, _load_service_account_info


# ---- AttemptLimiter -------------------------------------------------------


def test_limiter_allows_until_max():
    limiter = mc_cog.AttemptLimiter(max_fails=3, lockout_sec=60)
    assert limiter.record_failure(1) == 2
    assert limiter.record_failure(1) == 1
    assert limiter.record_failure(1) == 0
    assert limiter.locked_for(1) > 0


def test_limiter_isolates_users():
    limiter = mc_cog.AttemptLimiter(max_fails=2, lockout_sec=60)
    limiter.record_failure(1)
    limiter.record_failure(1)
    assert limiter.locked_for(1) > 0
    assert limiter.locked_for(2) == 0


def test_limiter_success_clears_failures():
    limiter = mc_cog.AttemptLimiter(max_fails=3, lockout_sec=60)
    limiter.record_failure(7)
    limiter.record_failure(7)
    limiter.record_success(7)
    # 실패 카운터가 초기화됐으므로 다시 최대 시도를 쓸 수 있어야 한다.
    assert limiter.record_failure(7) == 2


def test_limiter_lock_expires(monkeypatch):
    limiter = mc_cog.AttemptLimiter(max_fails=1, lockout_sec=10)
    now = [1000.0]
    monkeypatch.setattr(mc_cog.time, "monotonic", lambda: now[0])
    limiter.record_failure(5)
    assert limiter.locked_for(5) > 0
    now[0] += 11
    assert limiter.locked_for(5) == 0


# ---- 암호 -----------------------------------------------------------------


def test_password_has_no_hardcoded_default(monkeypatch):
    # 저장소가 공개라 소스에 기본 암호를 두면 암호가 아니게 된다.
    monkeypatch.delenv("MC_CONTROL_PASSWORD", raising=False)
    assert mc_cog._password() == ""


def test_password_comes_from_env(monkeypatch):
    monkeypatch.setenv("MC_CONTROL_PASSWORD", "hunter2")
    assert mc_cog._password() == "hunter2"


def test_source_contains_no_literal_password():
    """실제 암호가 소스에 섞여 들어가는 회귀를 막는다."""
    import pathlib

    src = pathlib.Path(mc_cog.__file__).read_text(encoding="utf-8")
    assert '"1224"' not in src and "'1224'" not in src


def test_power_commands_require_no_discord_member_permissions():
    """켜기/끄기는 Discord 역할이 아니라 암호만으로 사용 권한을 판정한다."""
    group = mc_cog.MCControlCog.mc
    commands = {command.name: command for command in group.commands}

    # None은 Discord API의 default_member_permissions=null이다.
    # Permissions.none()(값 0)은 관리자 외 전원 차단이므로 허용하지 않는다.
    assert group.name == "마크"
    assert group.default_permissions is None
    for name in ("켜기", "끄기"):
        assert commands[name].default_permissions is None
        assert commands[name].checks == []


# ---- 주소 표기 -------------------------------------------------------------


def test_address_label_hides_default_port(monkeypatch):
    monkeypatch.setenv("MC_SERVER_HOST", "1.2.3.4")
    monkeypatch.setenv("MC_SERVER_PORT", "25565")
    assert mc_cog._address_label() == "1.2.3.4"


def test_address_label_shows_custom_port(monkeypatch):
    monkeypatch.setenv("MC_SERVER_HOST", "1.2.3.4")
    monkeypatch.setenv("MC_SERVER_PORT", "25570")
    assert mc_cog._address_label() == "1.2.3.4:25570"


def test_address_label_unset(monkeypatch):
    monkeypatch.setenv("MC_SERVER_HOST", "")
    assert mc_cog._address_label() == "미설정"


def test_server_port_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("MC_SERVER_PORT", "not-a-number")
    assert mc_cog._server_port() == 25565


# ---- GcpComputeClient 설정 판정 --------------------------------------------


def _fake_key() -> str:
    payload = {
        "client_email": "x@y.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
        "token_uri": "https://oauth2.googleapis.com/token",
        "private_key_id": "abc",
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_missing_settings_lists_all(monkeypatch):
    for var in ("GCP_PROJECT_ID", "GCP_ZONE", "GCP_INSTANCE_NAME",
                "GCP_SA_KEY_B64", "GCP_SA_KEY_JSON"):
        monkeypatch.delenv(var, raising=False)
    client = GcpComputeClient()
    assert client.missing_settings() == [
        "GCP_PROJECT_ID",
        "GCP_ZONE",
        "GCP_INSTANCE_NAME",
        "GCP_SA_KEY_B64",
    ]
    assert client.configured is False


def test_configured_when_all_present(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCP_ZONE", "z")
    monkeypatch.setenv("GCP_INSTANCE_NAME", "i")
    monkeypatch.setenv("GCP_SA_KEY_B64", _fake_key())
    monkeypatch.delenv("GCP_SA_KEY_JSON", raising=False)
    client = GcpComputeClient()
    assert client.missing_settings() == []
    assert client.configured is True


def test_key_loader_rejects_bad_base64(monkeypatch):
    monkeypatch.setenv("GCP_SA_KEY_B64", "!!!not-base64!!!")
    monkeypatch.delenv("GCP_SA_KEY_JSON", raising=False)
    with pytest.raises(GcpConfigError):
        _load_service_account_info()


def test_key_loader_rejects_missing_fields(monkeypatch):
    monkeypatch.delenv("GCP_SA_KEY_B64", raising=False)
    monkeypatch.setenv("GCP_SA_KEY_JSON", json.dumps({"client_email": "a"}))
    with pytest.raises(GcpConfigError) as exc:
        _load_service_account_info()
    assert "private_key" in str(exc.value)


def test_key_loader_prefers_b64(monkeypatch):
    monkeypatch.setenv("GCP_SA_KEY_B64", _fake_key())
    monkeypatch.setenv("GCP_SA_KEY_JSON", "{}")
    info = _load_service_account_info()
    assert info["client_email"] == "x@y.iam.gserviceaccount.com"


# ---- 서버 리스트 핑 ---------------------------------------------------------


def _status_packet(payload: dict) -> bytes:
    body = json.dumps(payload).encode()
    inner = mc_ping._write_varint(0x00) + mc_ping._write_varint(len(body)) + body
    return mc_ping._write_varint(len(inner)) + inner


async def _serve_once(payload: dict) -> int:
    """테스트용 가짜 MC 서버를 띄우고 포트를 반환한다."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read(256)  # 핸드셰이크 + status request
            writer.write(_status_packet(payload))
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def closer() -> None:
        await asyncio.sleep(2)
        server.close()

    asyncio.ensure_future(closer())
    return port


async def test_ping_parses_player_counts():
    port = await _serve_once(
        {"players": {"online": 3, "max": 20}, "version": {"name": "1.21.1"}}
    )
    status = await mc_ping.ping("127.0.0.1", port, timeout=3)
    assert status.online == 3
    assert status.max_players == 20
    assert status.version == "1.21.1"


async def test_ping_handles_missing_fields():
    port = await _serve_once({})
    status = await mc_ping.ping("127.0.0.1", port, timeout=3)
    assert status.online == 0
    assert status.version == "?"


async def test_try_ping_returns_none_when_closed():
    # 아무도 듣지 않는 포트. 연결 거부 → None.
    assert await mc_ping.try_ping("127.0.0.1", 1, timeout=1) is None


def test_varint_roundtrip():
    assert mc_ping._write_varint(0) == b"\x00"
    assert mc_ping._write_varint(767) == b"\xff\x05"
    assert mc_ping._write_varint(127) == b"\x7f"


def test_handshake_packs_port_big_endian():
    assert struct.pack(">H", 25565) == b"\x63\xdd"
