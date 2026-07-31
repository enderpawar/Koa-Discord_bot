"""`/클라우드 상태` 진단 번역 및 Compute API 실패 기록 테스트."""
from __future__ import annotations

import json
import time

import pytest

import cogs.gcp_compute as gcp_compute
from cogs.gcp_compute import (
    ApiFailure,
    _extract_http_error_code,
    _extract_operation_error,
    last_failure,
)
from cogs.gcp_status import (
    CATALOG,
    build_status_embed,
    classify,
    cloud_failure_embed,
    vm_status_label,
)


@pytest.fixture(autouse=True)
def _reset_failure():
    gcp_compute._clear_failure()
    yield
    gcp_compute._clear_failure()


# ---- classify ---------------------------------------------------------------


def test_zone_exhausted_is_explained_as_transient_capacity_issue() -> None:
    diagnosis = classify("ZONE_RESOURCE_POOL_EXHAUSTED")

    assert diagnosis.transient is True
    assert "자리가 없어요" in diagnosis.title
    # 원문 코드가 사용자에게 새어 나가면 안 된다.
    assert "ZONE_RESOURCE" not in diagnosis.summary
    assert "ZONE_RESOURCE" not in diagnosis.action


def test_classify_is_case_and_dash_insensitive() -> None:
    assert classify("zone-resource-pool-exhausted") is classify(
        "ZONE_RESOURCE_POOL_EXHAUSTED"
    )


def test_classify_matches_suffixed_variants_by_prefix() -> None:
    """`..._WITH_DETAILS` 같은 변형을 표에 없이도 잡아야 한다."""
    assert classify("ZONE_RESOURCE_POOL_EXHAUSTED_WITH_EXTRA_SUFFIX").transient is True


@pytest.mark.parametrize(
    ("code", "transient"),
    [
        ("QUOTA_EXCEEDED", False),
        ("PERMISSION_DENIED", False),
        ("UNAUTHENTICATED", False),
        ("NOT_FOUND", False),
        ("BILLING_DISABLED", False),
        ("CONFIG_MISSING", False),
        ("RATELIMITEXCEEDED", True),
        ("BACKENDERROR", True),
        ("RESOURCE_NOT_READY", True),
        ("TIMEOUT", True),
        ("NETWORK_ERROR", True),
    ],
)
def test_known_codes_carry_correct_transience(code: str, transient: bool) -> None:
    assert classify(code).transient is transient


def test_unknown_code_falls_back_to_http_status() -> None:
    assert classify("SOMETHING_NEW", 403) is CATALOG["FORBIDDEN"]
    assert classify(None, 503) is CATALOG["BACKENDERROR"]


def test_fully_unknown_error_still_gives_actionable_text() -> None:
    diagnosis = classify("TOTALLY_UNSEEN", 418)

    assert "알 수 없는" in diagnosis.title
    assert diagnosis.action  # 빈 문자열이면 사용자가 뭘 할지 알 수 없다


def test_every_catalog_entry_has_all_three_sentences() -> None:
    for code, diagnosis in CATALOG.items():
        assert diagnosis.title, code
        assert diagnosis.summary, code
        assert diagnosis.action, code
        assert diagnosis.tone in {"ok", "info", "warn", "error"}, code


# ---- vm_status_label --------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("RUNNING", "켜져 있어요"),
        ("TERMINATED", "꺼져 있어요"),
        ("STOPPING", "끄는 중이에요"),
        ("PROVISIONING", "켜는 중이에요"),
        ("STAGING", "켜는 중이에요"),
    ],
)
def test_vm_status_label_uses_plain_korean(status: str, expected: str) -> None:
    assert vm_status_label(status) == expected


def test_vm_status_label_handles_unknown_value() -> None:
    assert vm_status_label("SOMETHING_ELSE") == "상태를 확인하는 중이에요"


# ---- build_status_embed -----------------------------------------------------


def test_embed_reports_healthy_state_when_no_failure() -> None:
    embed = build_status_embed("RUNNING", None)

    assert "문제가 없었어요" in embed.description
    assert embed.fields[0].value == "켜져 있어요"


def test_embed_never_leaks_raw_detail() -> None:
    failure = ApiFailure(
        code="ZONE_RESOURCE_POOL_EXHAUSTED",
        http_status=None,
        action="start",
        at=time.time(),
        detail="projects/project-fd838f7d-b8bf-4a93-b48/zones/asia-northeast3-a",
    )

    embed = build_status_embed("TERMINATED", failure)
    rendered = json.dumps(embed.to_dict(), ensure_ascii=False)

    assert "project-fd838f7d" not in rendered
    assert "asia-northeast3" not in rendered
    assert "ZONE_RESOURCE_POOL_EXHAUSTED" not in rendered


def test_embed_omits_vm_line_when_status_unavailable() -> None:
    failure = ApiFailure(
        code="AUTH_FAILED",
        http_status=None,
        action="get",
        at=time.time(),
        detail="",
    )

    embed = build_status_embed(None, failure)

    assert all(field.name != "지금 서버는" for field in embed.fields)


# ---- 오류 본문 파싱 ----------------------------------------------------------


def test_extract_http_error_code_reads_reason() -> None:
    body = json.dumps(
        {"error": {"code": 403, "errors": [{"reason": "forbidden"}], "message": "no"}}
    )

    assert _extract_http_error_code(body) == "forbidden"


def test_extract_http_error_code_survives_non_json() -> None:
    assert _extract_http_error_code("<html>502 Bad Gateway</html>") == "UNKNOWN"


def test_extract_operation_error_reads_code_from_operation_body() -> None:
    """start 는 HTTP 200 + Operation 으로 실패를 알려 오기도 한다."""
    operation = {
        "kind": "compute#operation",
        "status": "DONE",
        "error": {
            "errors": [
                {
                    "code": "ZONE_RESOURCE_POOL_EXHAUSTED",
                    "message": "The zone does not have enough resources available.",
                }
            ]
        },
    }

    result = _extract_operation_error(operation)

    assert result is not None
    assert result[0] == "ZONE_RESOURCE_POOL_EXHAUSTED"


def test_extract_operation_error_returns_none_for_successful_operation() -> None:
    assert _extract_operation_error({"kind": "compute#operation", "status": "DONE"}) is None


# ---- 실패 기록 --------------------------------------------------------------


def test_successful_get_does_not_erase_a_start_failure() -> None:
    """상태 조회는 VM이 꺼져 있어도 성공한다. 그걸로 start 실패를 지우면
    `/클라우드 상태`가 항상 '문제 없어요'만 말하게 된다."""
    gcp_compute._record_failure(
        code="ZONE_RESOURCE_POOL_EXHAUSTED",
        http_status=None,
        action="start",
        detail="",
    )

    gcp_compute._clear_failure("get")

    assert last_failure() is not None
    assert last_failure().code == "ZONE_RESOURCE_POOL_EXHAUSTED"


def test_successful_start_clears_its_own_failure() -> None:
    gcp_compute._record_failure(
        code="ZONE_RESOURCE_POOL_EXHAUSTED",
        http_status=None,
        action="start",
        detail="",
    )

    gcp_compute._clear_failure("start")

    assert last_failure() is None


def test_unscoped_clear_wipes_everything() -> None:
    gcp_compute._record_failure(
        code="ZONE_RESOURCE_POOL_EXHAUSTED", http_status=None, action="start", detail=""
    )

    gcp_compute._clear_failure()

    assert last_failure() is None


def test_start_not_applied_is_reported_as_transient_capacity_issue() -> None:
    gcp_compute.record_start_not_applied()
    failure = last_failure()

    assert failure is not None
    assert failure.action == "start"

    diagnosis = classify(failure.code)
    assert diagnosis.transient is True
    assert "켜지지 않았어요" in diagnosis.title


def test_failure_embed_explains_without_leaking_code() -> None:
    gcp_compute.record_start_not_applied()

    embed = cloud_failure_embed(last_failure())
    rendered = json.dumps(embed.to_dict(), ensure_ascii=False)

    assert "START_NOT_APPLIED" not in rendered
    assert any(f.name == "기다리면 풀리나요" for f in embed.fields)


def test_record_and_clear_failure_roundtrip() -> None:
    assert last_failure() is None

    gcp_compute._record_failure(
        code="ZONE_RESOURCE_POOL_EXHAUSTED",
        http_status=None,
        action="start",
        detail="x" * 500,
    )
    recorded = last_failure()

    assert recorded is not None
    assert recorded.code == "ZONE_RESOURCE_POOL_EXHAUSTED"
    assert recorded.action == "start"
    assert len(recorded.detail) == 300  # 원문은 잘라서 보관

    gcp_compute._clear_failure()
    assert last_failure() is None
