"""`/클라우드 상태` — GCP 오류를 비전문가용 한국어로 번역해 보여준다.

디스코드에서 `/마크 켜기`가 실패하면 지금까지는 `ZONE_RESOURCE_POOL_EXHAUSTED`
같은 원문이 그대로 노출됐다. 서버에 들어오는 사람 대부분은 이게 무슨 뜻인지,
기다리면 되는 건지 관리자를 불러야 하는 건지 알 수 없다. 이 모듈은 그 코드를
'무슨 일이 났는지 / 그래서 뭘 하면 되는지' 두 문장으로 바꾼다.

Cloud Logging API를 읽지 않는 이유:
로그 조회에는 `logging.logEntries.list` 권한이 추가로 필요한데, 이 봇의 서비스
계정은 인스턴스 전원 토글 3개 권한만 갖는 최소 권한 설계다(cogs/gcp_compute.py).
어차피 사용자가 겪는 실패는 봇이 직접 받은 그 오류이므로, 호출 지점에서 기록해
둔 것을 번역하는 편이 권한을 늘리지 않으면서 같은 답을 준다.

공개 응답인 이유:
서버원 누구나 "지금 왜 안 켜지는지"를 물어보지 않고 확인할 수 있어야 한다.
따라서 응답에는 프로젝트 ID·인스턴스명·API 원문을 절대 싣지 않는다. 그 정보는
`ApiFailure.detail`에 남아 로그로만 흐른다.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from cogs.gcp_compute import (
    ApiFailure,
    GcpApiError,
    GcpComputeClient,
    GcpConfigError,
    last_failure,
)
from cogs.ui import notice_embed

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Diagnosis:
    """오류 하나에 대한 사람이 읽는 설명."""

    title: str
    summary: str  # 무슨 일이 일어났는지
    action: str  # 그래서 뭘 하면 되는지
    tone: str  # ui.notice_embed 의 톤
    transient: bool  # True = 기다리면 풀림, False = 관리자 개입 필요


# 기다리면 풀리는 문제 ---------------------------------------------------------

_CAPACITY = Diagnosis(
    title="지금 서울 데이터센터에 자리가 없어요",
    summary=(
        "우리 서버가 올라갈 컴퓨터를 구글이 지금 내주지 못하는 상태예요. "
        "고장이 난 게 아니라, 같은 지역을 쓰는 사람이 몰려서 자리가 찬 것뿐이에요."
    ),
    action="조금 뒤에 `/마크 켜기`를 다시 눌러 주세요. 보통 수십 분 안에 자리가 나요.",
    tone="warn",
    transient=True,
)

_START_NOT_APPLIED = Diagnosis(
    title="서버를 켜라고 했는데 켜지지 않았어요",
    summary=(
        "구글이 요청은 받아 놓고 실제로 컴퓨터를 내주지는 못한 상태예요. "
        "대개 그 지역에 자리가 없을 때 이렇게 돼요."
    ),
    action="조금 뒤에 `/마크 켜기`를 다시 눌러 주세요. 보통 수십 분 안에 자리가 나요.",
    tone="warn",
    transient=True,
)

_BUSY = Diagnosis(
    title="서버가 아직 이전 작업을 처리 중이에요",
    summary="방금 껐거나 켜는 중이라 새 명령을 받지 못하는 상태예요.",
    action="1~2분 기다렸다가 다시 시도해 주세요.",
    tone="warn",
    transient=True,
)

_RATE_LIMIT = Diagnosis(
    title="너무 자주 요청했어요",
    summary="짧은 시간에 명령이 많이 들어와서 구글이 잠시 요청을 막았어요.",
    action="1분쯤 기다렸다가 다시 시도해 주세요.",
    tone="warn",
    transient=True,
)

_GOOGLE_DOWN = Diagnosis(
    title="구글 클라우드가 잠깐 불안정해요",
    summary="구글 쪽에서 일시적인 문제가 났어요. 우리 서버나 봇 문제는 아니에요.",
    action="몇 분 뒤에 다시 시도해 주세요.",
    tone="warn",
    transient=True,
)

_NETWORK = Diagnosis(
    title="클라우드에 연결하지 못했어요",
    summary="봇이 구글 클라우드까지 연락이 닿지 않았어요. 대개 잠깐 있다 풀리는 문제예요.",
    action="잠시 뒤 다시 시도해 주세요. 계속 이러면 관리자에게 알려 주세요.",
    tone="warn",
    transient=True,
)

# 관리자가 손봐야 하는 문제 -----------------------------------------------------

_QUOTA = Diagnosis(
    title="이번 기간에 쓸 수 있는 양을 다 썼어요",
    summary="구글이 정해 둔 사용 한도에 걸렸어요. 기다린다고 풀리지 않아요.",
    action="관리자가 GCP에서 할당량을 올리거나 다른 자원을 정리해야 해요.",
    tone="error",
    transient=False,
)

_PERMISSION = Diagnosis(
    title="봇에게 서버를 켤 권한이 없어요",
    summary="봇 계정의 권한 설정이 빠졌거나 바뀌었어요.",
    action="관리자가 GCP에서 봇 계정 권한(mcPowerToggle)을 다시 확인해야 해요.",
    tone="error",
    transient=False,
)

_AUTH = Diagnosis(
    title="봇의 클라우드 출입증이 만료됐어요",
    summary="봇이 구글에 로그인하는 데 쓰는 열쇠가 더 이상 통하지 않아요.",
    action="관리자가 서비스 계정 키를 새로 발급해 넣어야 해요.",
    tone="error",
    transient=False,
)

_NOT_FOUND = Diagnosis(
    title="켜야 할 서버를 찾지 못했어요",
    summary="봇이 바라보는 위치에 마인크래프트 서버가 없어요. 이름이나 지역 설정이 어긋났을 수 있어요.",
    action="관리자가 봇 설정(인스턴스 이름·존)을 확인해야 해요.",
    tone="error",
    transient=False,
)

_BILLING = Diagnosis(
    title="결제 문제로 클라우드가 잠겼어요",
    summary="구글 클라우드 결제가 막혀서 서버를 켤 수 없는 상태예요.",
    action="관리자가 GCP 결제 정보를 확인해야 해요.",
    tone="error",
    transient=False,
)

_CONFIG = Diagnosis(
    title="봇 설정이 덜 돼 있어요",
    summary="클라우드에 접속하는 데 필요한 설정값이 비어 있어요.",
    action="관리자가 봇 환경변수를 채워야 해요.",
    tone="error",
    transient=False,
)

_UNKNOWN = Diagnosis(
    title="원인을 알 수 없는 오류예요",
    summary="처음 보는 문제라 자동으로 설명하지 못했어요.",
    action="한 번 더 시도해 보고, 그래도 안 되면 관리자에게 알려 주세요.",
    tone="error",
    transient=False,
)


# GCP 오류 코드 → 설명. 키는 대문자로 정규화해 비교한다.
CATALOG: dict[str, Diagnosis] = {
    # 자원 고갈
    "ZONE_RESOURCE_POOL_EXHAUSTED": _CAPACITY,
    "ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS": _CAPACITY,
    "RESOURCE_POOL_EXHAUSTED": _CAPACITY,
    "RESOURCE_EXHAUSTED": _CAPACITY,
    "IP_SPACE_EXHAUSTED": _CAPACITY,
    # Operation 조회 권한이 없어 정확한 코드를 못 받았을 때의 추정치.
    "START_NOT_APPLIED": _START_NOT_APPLIED,
    # 할당량
    "QUOTA_EXCEEDED": _QUOTA,
    "QUOTAEXCEEDED": _QUOTA,
    "LIMITEXCEEDED": _QUOTA,
    # 권한 / 인증
    "FORBIDDEN": _PERMISSION,
    "PERMISSION_DENIED": _PERMISSION,
    "PERMISSIONDENIED": _PERMISSION,
    "UNAUTHENTICATED": _AUTH,
    "AUTH_FAILED": _AUTH,
    "INVALID_GRANT": _AUTH,
    "AUTHERROR": _AUTH,
    # 대상 없음
    "NOT_FOUND": _NOT_FOUND,
    "NOTFOUND": _NOT_FOUND,
    # 상태 충돌
    "RESOURCE_NOT_READY": _BUSY,
    "RESOURCENOTREADY": _BUSY,
    "RESOURCEINUSEBYANOTHERRESOURCE": _BUSY,
    "CONDITIONNOTMET": _BUSY,
    "PRECONDITION_FAILED": _BUSY,
    "OPERATION_ABORTED": _BUSY,
    # 속도 제한
    "RATELIMITEXCEEDED": _RATE_LIMIT,
    "USERRATELIMITEXCEEDED": _RATE_LIMIT,
    "TOO_MANY_REQUESTS": _RATE_LIMIT,
    # 구글 측 장애
    "BACKENDERROR": _GOOGLE_DOWN,
    "INTERNALERROR": _GOOGLE_DOWN,
    "SERVICE_UNAVAILABLE": _GOOGLE_DOWN,
    "UNAVAILABLE": _GOOGLE_DOWN,
    # 결제
    "BILLING_DISABLED": _BILLING,
    "BILLINGNOTENABLED": _BILLING,
    "ACCOUNTDISABLED": _BILLING,
    # 봇 자체 사정
    "TIMEOUT": _NETWORK,
    "NETWORK_ERROR": _NETWORK,
    "CONFIG_MISSING": _CONFIG,
}

# 코드로 못 잡았을 때 HTTP 상태로 한 번 더 시도한다.
_HTTP_FALLBACK: dict[int, Diagnosis] = {
    401: _AUTH,
    403: _PERMISSION,
    404: _NOT_FOUND,
    409: _BUSY,
    412: _BUSY,
    429: _RATE_LIMIT,
    500: _GOOGLE_DOWN,
    502: _GOOGLE_DOWN,
    503: _GOOGLE_DOWN,
    504: _GOOGLE_DOWN,
}

# VM 상태 → 사람 말. 원문(RUNNING 등)은 굳이 같이 보여주지 않는다.
_VM_STATUS_LABEL: dict[str, str] = {
    "RUNNING": "켜져 있어요",
    "TERMINATED": "꺼져 있어요",
    "STOPPED": "꺼져 있어요",
    "STOPPING": "끄는 중이에요",
    "SUSPENDING": "잠시 멈추는 중이에요",
    "SUSPENDED": "잠시 멈춰 있어요",
    "PROVISIONING": "켜는 중이에요",
    "STAGING": "켜는 중이에요",
    "REPAIRING": "구글이 복구 작업 중이에요",
}


def classify(code: str | None, http_status: int | None = None) -> Diagnosis:
    """오류 코드를 사람이 읽는 설명으로 바꾼다.

    코드 우선, 없으면 HTTP 상태, 그래도 모르면 `_UNKNOWN`.
    """
    if code:
        normalized = code.strip().upper().replace("-", "_")
        hit = CATALOG.get(normalized)
        if hit is not None:
            return hit
        # `ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS` 처럼 접미사가 붙는 변형을
        # 매번 표에 추가하지 않아도 되도록 접두 일치를 한 번 더 본다.
        for key, diagnosis in CATALOG.items():
            if normalized.startswith(key) and len(key) >= 12:
                return diagnosis
    if http_status is not None:
        fallback = _HTTP_FALLBACK.get(http_status)
        if fallback is not None:
            return fallback
    return _UNKNOWN


def vm_status_label(status: str) -> str:
    """VM 상태 문자열을 사람 말로. 모르는 값이면 '확인 중'."""
    return _VM_STATUS_LABEL.get(status.strip().upper(), "상태를 확인하는 중이에요")


def cloud_failure_embed(failure: ApiFailure | None) -> discord.Embed:
    """전원 명령이 실패했을 때 그 자리에서 보여줄 안내.

    `/클라우드 상태`와 같은 문구를 쓴다 — 실패 직후에 이미 이유를 읽을 수 있어야
    사용자가 굳이 다른 명령을 더 칠 이유가 없다.
    """
    diagnosis = classify(failure.code, failure.http_status) if failure else _UNKNOWN
    embed = notice_embed(diagnosis.title, diagnosis.summary, tone=diagnosis.tone)
    embed.add_field(name="어떻게 하면 되나요", value=diagnosis.action, inline=False)
    embed.add_field(
        name="기다리면 풀리나요",
        value="네, 시간이 지나면 대개 풀려요." if diagnosis.transient else "아니요, 관리자가 손봐야 해요.",
        inline=False,
    )
    return embed


def build_status_embed(
    vm_status: str | None,
    failure: ApiFailure | None,
    *,
    now: float | None = None,
) -> discord.Embed:
    """`/클라우드 상태` 응답 embed. 원문 오류는 절대 싣지 않는다."""
    if failure is None:
        embed = notice_embed(
            "클라우드 상태",
            "최근에 문제가 없었어요. 서버를 켜고 끄는 데 지장이 없는 상태예요.",
            tone="ok",
        )
    else:
        diagnosis = classify(failure.code, failure.http_status)
        embed = notice_embed(diagnosis.title, diagnosis.summary, tone=diagnosis.tone)
        embed.add_field(name="어떻게 하면 되나요", value=diagnosis.action, inline=False)
        embed.add_field(
            name="기다리면 풀리나요",
            value="네, 시간이 지나면 대개 풀려요." if diagnosis.transient else "아니요, 관리자가 손봐야 해요.",
            inline=False,
        )
        moment = dt.datetime.fromtimestamp(failure.at, tz=dt.timezone.utc)
        embed.add_field(
            name="마지막으로 문제가 난 때",
            value=discord.utils.format_dt(moment, "R"),
            inline=False,
        )

    if vm_status is not None:
        embed.insert_field_at(
            0, name="지금 서버는", value=vm_status_label(vm_status), inline=False
        )
    return embed


class CloudStatusCog(commands.Cog):
    cloud = app_commands.Group(
        name="클라우드",
        description="마인크래프트 서버가 올라가는 클라우드 상태 안내",
        default_permissions=None,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.gcp = GcpComputeClient()

    @cloud.command(
        name="상태", description="서버가 왜 안 켜지는지 쉬운 말로 알려줍니다"
    )
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)

        # 조회보다 먼저 읽는다. get_status()가 VM=RUNNING 을 확인하면 기록을
        # 지우는데, 그건 정상 동작이지만 그 전의 실패도 보고 대상이므로
        # 순서를 뒤집으면 진단 명령이 스스로 증거를 지우게 된다.
        failure = last_failure()

        vm_status: str | None = None
        try:
            vm_status = await self.gcp.get_status()
        except (GcpConfigError, GcpApiError):
            # 조회 자체가 실패했다면 그게 가장 최신 문제다.
            log.exception("cloud status: lookup failed")
            failure = last_failure() or failure

        if vm_status == "RUNNING":
            failure = None  # 서버가 떠 있으면 지난 실패는 의미 없다

        embed = build_status_embed(vm_status, failure)
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CloudStatusCog(bot))
