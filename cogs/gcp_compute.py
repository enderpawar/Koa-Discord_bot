"""GCP Compute Engine 전원 제어 (마인크래프트 VM 켜기/끄기).

서비스 계정 키로 OAuth2 토큰을 받아 Compute REST API를 직접 호출한다.
gcloud SDK(수백 MB)를 컨테이너에 넣지 않기 위해 REST를 쓴다.

권한은 인스턴스 단위로 바인딩된 커스텀 역할 `mcPowerToggle` 하나뿐이다
(compute.instances.get / start / stop). 키가 유출돼도 이 VM의 전원 외에는
아무것도 못 한다.

끄기가 `instances.stop` 단독인 이유:
GCE는 stop 시 ACPI 종료 신호를 보내고 약 90초를 기다린다. 그 사이 systemd가
minecraft.service를 `KillSignal=SIGINT`로 정상 정지시키므로 청크 저장 훅이 돈다.
실측상 정상 정지는 0~1초에 끝나 유예 안에 충분히 들어온다
(cobblemon-server/gcp/RUNBOOK.md 참고).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_API_ROOT = "https://compute.googleapis.com/compute/v1"
# 만료 직전 토큰으로 요청을 보내 401을 맞지 않도록 앞당겨 폐기한다.
_TOKEN_EARLY_EXPIRY_SEC = 120
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)


class GcpConfigError(RuntimeError):
    """자격증명/설정 누락. 사용자에게 '설정 필요'로 안내할 수 있는 오류."""


class GcpApiError(RuntimeError):
    """Compute API 호출 실패."""


@dataclass(frozen=True)
class ApiFailure:
    """마지막으로 관측된 Compute API 실패.

    `/클라우드 상태`가 이걸 읽어 비전문가용 문구로 번역한다. `detail`은 프로젝트
    ID·인스턴스명이 섞일 수 있으므로 로그용이며 디스코드에 그대로 노출하지 않는다.
    """

    code: str
    http_status: int | None
    action: str  # "start" / "stop" / "get"
    at: float
    detail: str


_last_failure: ApiFailure | None = None


def last_failure() -> ApiFailure | None:
    """가장 최근 실패. 이후 호출이 성공했다면 None."""
    return _last_failure


def _record_failure(
    *, code: str, http_status: int | None, action: str, detail: str
) -> None:
    global _last_failure
    _last_failure = ApiFailure(
        code=code,
        http_status=http_status,
        action=action,
        at=time.time(),
        detail=detail[:300],
    )
    log.warning("gcp %s failed: code=%s http=%s", action, code, http_status)


def _clear_failure() -> None:
    """호출이 성공하면 지운다 — 이미 해결된 문제를 계속 보고하지 않기 위해."""
    global _last_failure
    _last_failure = None


def _extract_http_error_code(body: str) -> str:
    """4xx/5xx 응답 본문에서 `error.errors[0].reason`을 뽑는다."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return "UNKNOWN"
    if not isinstance(data, dict):
        return "UNKNOWN"
    error = data.get("error")
    if not isinstance(error, dict):
        return "UNKNOWN"
    errors = error.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        reason = errors[0].get("reason")
        if reason:
            return str(reason)
    status = error.get("status")
    return str(status) if status else "UNKNOWN"


def _extract_operation_error(data: Any) -> tuple[str, str] | None:
    """Operation 본문에 담겨 오는 실패를 뽑는다.

    `instances.start`는 자원이 없어도 HTTP 200 + Operation 을 돌려주고, 실제
    사유는 `error.errors[0].code`(예: ZONE_RESOURCE_POOL_EXHAUSTED)에 들어온다.
    이걸 보지 않으면 기동 실패가 5분 폴링 타임아웃으로만 드러나 원인을 알 수 없다.
    """
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    if not isinstance(error, dict):
        return None
    errors = error.get("errors")
    if not (isinstance(errors, list) and errors and isinstance(errors[0], dict)):
        return None
    first = errors[0]
    return str(first.get("code") or "UNKNOWN"), str(first.get("message") or "")


def _load_service_account_info() -> dict[str, Any]:
    """SA 키 JSON을 환경변수에서 읽는다.

    `GCP_SA_KEY_B64`(base64) 우선, 없으면 `GCP_SA_KEY_JSON`(원본 JSON).
    .env는 줄바꿈을 못 담으므로 base64 쪽이 기본 경로다.
    """
    b64 = os.getenv("GCP_SA_KEY_B64", "").strip()
    if b64:
        try:
            raw = base64.b64decode(b64, validate=True).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
            raise GcpConfigError("GCP_SA_KEY_B64 디코딩 실패") from exc
    else:
        raw = os.getenv("GCP_SA_KEY_JSON", "").strip()
        if not raw:
            raise GcpConfigError("GCP_SA_KEY_B64 또는 GCP_SA_KEY_JSON이 설정되지 않았습니다")

    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GcpConfigError("서비스 계정 키가 올바른 JSON이 아닙니다") from exc

    missing = [k for k in ("client_email", "private_key", "token_uri") if not info.get(k)]
    if missing:
        raise GcpConfigError(f"서비스 계정 키에 필드가 없습니다: {', '.join(missing)}")
    return info


class GcpComputeClient:
    """VM 하나의 전원만 다루는 최소 클라이언트."""

    def __init__(
        self,
        *,
        project: str | None = None,
        zone: str | None = None,
        instance: str | None = None,
    ) -> None:
        self.project = project or os.getenv("GCP_PROJECT_ID", "").strip()
        self.zone = zone or os.getenv("GCP_ZONE", "").strip()
        self.instance = instance or os.getenv("GCP_INSTANCE_NAME", "").strip()
        self._token: str | None = None
        self._token_expiry: float = 0.0
        # 토큰 갱신이 동시에 여러 번 일어나지 않게 직렬화한다.
        self._token_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        """자격증명/대상 설정이 모두 갖춰졌는지. 명령 등록 전 확인용."""
        if not (self.project and self.zone and self.instance):
            return False
        try:
            _load_service_account_info()
        except GcpConfigError:
            return False
        return True

    def missing_settings(self) -> list[str]:
        missing = [
            name
            for name, value in (
                ("GCP_PROJECT_ID", self.project),
                ("GCP_ZONE", self.zone),
                ("GCP_INSTANCE_NAME", self.instance),
            )
            if not value
        ]
        try:
            _load_service_account_info()
        except GcpConfigError:
            missing.append("GCP_SA_KEY_B64")
        return missing

    # ---- 인증 -------------------------------------------------------------

    def _build_assertion(self, info: dict[str, Any]) -> str:
        """RS256 서명된 JWT bearer assertion 생성 (동기 — CPU 작업)."""
        # google-auth를 쓰지 않는 이유: requests/urllib3까지 끌고 들어와
        # 이미지가 커지는 데 비해 여기서 필요한 건 서명 한 줄뿐이다.
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        if info.get("private_key_id"):
            header["kid"] = info["private_key_id"]
        payload = {
            "iss": info["client_email"],
            "scope": _SCOPE,
            "aud": info["token_uri"],
            "iat": now,
            "exp": now + 3600,
        }

        def b64u(data: bytes) -> bytes:
            return base64.urlsafe_b64encode(data).rstrip(b"=")

        signing_input = b".".join(
            (
                b64u(json.dumps(header, separators=(",", ":")).encode()),
                b64u(json.dumps(payload, separators=(",", ":")).encode()),
            )
        )
        key = serialization.load_pem_private_key(
            info["private_key"].encode("utf-8"), password=None
        )
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return (signing_input + b"." + b64u(signature)).decode("ascii")

    async def _access_token(self, session: aiohttp.ClientSession) -> str:
        async with self._token_lock:
            if self._token and time.time() < self._token_expiry - _TOKEN_EARLY_EXPIRY_SEC:
                return self._token

            info = _load_service_account_info()
            # RSA 서명은 수십 ms 걸린다. 이벤트 루프를 막지 않도록 스레드로 뺀다.
            assertion = await asyncio.to_thread(self._build_assertion, info)

            async with session.post(
                info["token_uri"],
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    # 본문에 토큰은 없지만 키 관련 정보가 섞일 수 있어 앞부분만 남긴다.
                    raise GcpApiError(f"토큰 발급 실패 (HTTP {resp.status}): {body[:200]}")
                data = json.loads(body)

            self._token = data["access_token"]
            self._token_expiry = time.time() + int(data.get("expires_in", 3600))
            return self._token

    # ---- API 호출 ---------------------------------------------------------

    async def _request(self, method: str, path: str, *, action: str) -> dict[str, Any]:
        try:
            async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
                try:
                    token = await self._access_token(session)
                except GcpConfigError:
                    _record_failure(
                        code="CONFIG_MISSING",
                        http_status=None,
                        action=action,
                        detail="서비스 계정 설정 누락",
                    )
                    raise
                except GcpApiError:
                    _record_failure(
                        code="AUTH_FAILED",
                        http_status=None,
                        action=action,
                        detail="액세스 토큰 발급 실패",
                    )
                    raise

                url = f"{_API_ROOT}/projects/{self.project}/zones/{self.zone}/instances/{self.instance}{path}"
                async with session.request(
                    method, url, headers={"Authorization": f"Bearer {token}"}
                ) as resp:
                    body = await resp.text()
                    if resp.status >= 400:
                        _record_failure(
                            code=_extract_http_error_code(body),
                            http_status=resp.status,
                            action=action,
                            detail=body,
                        )
                        raise GcpApiError(
                            f"{method} {path} 실패 (HTTP {resp.status}): {body[:300]}"
                        )
                    data = json.loads(body) if body else {}
        except asyncio.TimeoutError as exc:
            _record_failure(
                code="TIMEOUT", http_status=None, action=action, detail=str(exc)
            )
            raise GcpApiError(f"{method} {path} 시간 초과") from exc
        except aiohttp.ClientError as exc:
            _record_failure(
                code="NETWORK_ERROR", http_status=None, action=action, detail=str(exc)
            )
            raise GcpApiError(f"{method} {path} 연결 실패: {exc}") from exc

        op_error = _extract_operation_error(data)
        if op_error is not None:
            code, message = op_error
            _record_failure(
                code=code, http_status=None, action=action, detail=message
            )
            raise GcpApiError(f"{method} {path} 실패 ({code}): {message[:300]}")

        _clear_failure()
        return data

    async def get_status(self) -> str:
        """RUNNING / TERMINATED / STOPPING / PROVISIONING / STAGING 등."""
        data = await self._request("GET", "", action="get")
        return str(data.get("status", "UNKNOWN"))

    async def start(self) -> None:
        await self._request("POST", "/start", action="start")

    async def stop(self) -> None:
        await self._request("POST", "/stop", action="stop")
