"""서버별 대시보드 키: 발급·조회·폐기와 범위 격리."""
from __future__ import annotations

import json

import pytest

from cogs.admin_key_store import AdminKeyStore, hash_key


@pytest.fixture
def store(tmp_path) -> AdminKeyStore:
    AdminKeyStore._reset_instances_for_tests()
    return AdminKeyStore(tmp_path / "admin_keys.json")


async def test_issued_key_resolves_to_its_guild(store: AdminKeyStore) -> None:
    key = await store.issue(111, issued_to=999)

    assert await store.find_guild(key) == 111
    assert await store.find_guild(key + "x") is None
    assert await store.find_guild("") is None


async def test_plaintext_key_is_never_written_to_disk(
    store: AdminKeyStore, tmp_path
) -> None:
    key = await store.issue(111)

    raw = (tmp_path / "admin_keys.json").read_text(encoding="utf-8")
    assert key not in raw
    assert hash_key(key) in raw


async def test_reissue_invalidates_the_previous_key(store: AdminKeyStore) -> None:
    old = await store.issue(111)
    new = await store.issue(111)

    assert old != new
    assert await store.find_guild(old) is None
    assert await store.find_guild(new) == 111


async def test_keys_are_isolated_per_guild(store: AdminKeyStore) -> None:
    a = await store.issue(111)
    b = await store.issue(222)

    assert await store.find_guild(a) == 111
    assert await store.find_guild(b) == 222


async def test_revoke_removes_access(store: AdminKeyStore) -> None:
    key = await store.issue(111)

    assert await store.revoke(111) is True
    assert await store.find_guild(key) is None
    assert await store.revoke(111) is False


async def test_info_never_exposes_the_hash(store: AdminKeyStore) -> None:
    await store.issue(111, issued_to=999)

    info = await store.info(111)
    assert info is not None
    assert info["issued_to"] == "999"
    assert "hash" not in info
    assert await store.info(222) is None


async def test_corrupt_file_is_backed_up_and_does_not_raise(tmp_path) -> None:
    path = tmp_path / "admin_keys.json"
    path.write_text("{ not json", encoding="utf-8")
    AdminKeyStore._reset_instances_for_tests()

    store = AdminKeyStore(path)

    assert await store.find_guild("anything") is None
    assert path.with_suffix(".json.corrupt").exists()
    # 손상 이후에도 정상 발급이 이어져야 한다 (Rule 03).
    key = await store.issue(111)
    assert await store.find_guild(key) == 111
    assert json.loads(path.read_text(encoding="utf-8"))
