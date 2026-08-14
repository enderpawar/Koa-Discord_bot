#!/usr/bin/env python3
"""롤·발로란트 공식 티어 그림을 봇의 애플리케이션 이모지로 올린다.

파티 모집 임베드의 티어 뱃지(`cogs/tier_badge.py`)는 이 이모지를 쓴다.
안 올려도 유니코드 이모지로 동작하므로, 이건 "그림을 예쁘게" 하는 선택 단계다.

**길드 이모지가 아니라 애플리케이션 이모지다.** 봇 애플리케이션에 붙는 자원이라
초대된 어느 서버의 이모지 목록도 건드리지 않고, 봇이 들어간 모든 서버에서
그대로 쓸 수 있다.

    python scripts/sync_tier_emojis.py            # 없는 것만 올린다
    python scripts/sync_tier_emojis.py --dry-run  # 무엇을 올릴지만 출력
    python scripts/sync_tier_emojis.py --force    # 이미 있는 것도 다시 올린다
    python scripts/sync_tier_emojis.py --game lol # 한 게임만

에셋 출처가 게임마다 다르다. 롤 엠블럼은 CommunityDragon 에 고정 주소로 있어
`tier_badge` 가 직접 만들지만, 발로란트 랭크 아이콘 주소에는 시즌마다 바뀌는
UUID 가 들어가 valorant-api.com 의 티어 표를 그때그때 조회해야 한다.

`DISCORD_TOKEN` 을 `.env` 또는 환경변수에서 읽는다. 게이트웨이에 붙지 않고
REST 만 쓰므로 봇을 멈추지 않고 실행해도 된다. 올린 뒤에는 봇을 재시작하거나
재연결하면 `on_ready` 에서 새 이모지를 집어 간다.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cogs.tier_badge import (  # noqa: E402
    GAME_LOL,
    GAME_VALORANT,
    emoji_name,
    is_known_tier,
    lol_emblem_urls,
    parse_valorant_tier_name,
)

_API = "https://discord.com/api/v10"
# 발로란트 랭크 아이콘 표. 에피소드마다 새 UUID 가 붙어서 마지막 항목이 최신이다.
_VALORANT_TIERS_URL = "https://valorant-api.com/v1/competitivetiers"
# Discord 이모지 업로드 상한. CommunityDragon 엠블럼은 가장 큰 것이 약 230KB 라
# 모두 이 안에 들어오지만, 에셋이 바뀌면 조용히 400 이 나므로 미리 걸러 준다.
_MAX_EMOJI_BYTES = 256 * 1024
_TIMEOUT = aiohttp.ClientTimeout(total=30)
GAMES = (GAME_LOL, GAME_VALORANT)


class SyncError(RuntimeError):
    pass


async def _json(response: aiohttp.ClientResponse) -> dict:
    payload = await response.json(content_type=None)
    return payload if isinstance(payload, dict) else {}


async def _application_id(session: aiohttp.ClientSession) -> str:
    async with session.get(f"{_API}/oauth2/applications/@me") as response:
        if response.status == 401:
            raise SyncError("DISCORD_TOKEN 이 거부됐습니다(401). 토큰을 확인하세요.")
        if response.status != 200:
            raise SyncError(f"애플리케이션 조회 실패: HTTP {response.status}")
        return str((await _json(response)).get("id", ""))


async def _existing_emojis(session: aiohttp.ClientSession, app_id: str) -> dict[str, str]:
    async with session.get(f"{_API}/applications/{app_id}/emojis") as response:
        if response.status != 200:
            raise SyncError(f"이모지 목록 조회 실패: HTTP {response.status}")
        items = (await _json(response)).get("items") or []
    return {
        str(item.get("name")): str(item.get("id"))
        for item in items
        if isinstance(item, dict)
    }


async def _valorant_targets(assets: aiohttp.ClientSession) -> dict[str, str]:
    """valorant-api.com 의 최신 티어 표에서 `{이모지 이름: 아이콘 주소}` 를 만든다.

    발로란트는 같은 티어라도 단계마다 화살표 수가 달라 그림이 전부 다르다.
    그래서 롤과 달리 단계별로 올린다 (`koa_valorant_gold_2`).
    """
    async with assets.get(_VALORANT_TIERS_URL) as response:
        if response.status != 200:
            raise SyncError(f"발로란트 티어 표 조회 실패: HTTP {response.status}")
        episodes = (await _json(response)).get("data") or []
    if not episodes:
        raise SyncError("발로란트 티어 표가 비어 있습니다.")

    targets: dict[str, str] = {}
    for row in episodes[-1].get("tiers") or []:
        if not isinstance(row, dict):
            continue
        parsed = parse_valorant_tier_name(str(row.get("tierName") or ""))
        # 표에는 자리만 잡아 둔 `Unused1` 같은 항목이 섞여 있다.
        if parsed is None or not is_known_tier(GAME_VALORANT, parsed[0]):
            continue
        icon = row.get("smallIcon")
        if not icon:
            continue
        targets[emoji_name(GAME_VALORANT, parsed[0], parsed[1])] = str(icon)
    if not targets:
        raise SyncError("발로란트 티어 아이콘을 하나도 찾지 못했습니다.")
    return targets


async def _download(session: aiohttp.ClientSession, url: str) -> bytes:
    async with session.get(url) as response:
        if response.status != 200:
            raise SyncError(f"이미지 내려받기 실패({response.status}): {url}")
        return await response.read()


async def _create(
    session: aiohttp.ClientSession, app_id: str, name: str, image: bytes
) -> str:
    payload = {
        "name": name,
        "image": "data:image/png;base64," + base64.b64encode(image).decode("ascii"),
    }
    async with session.post(
        f"{_API}/applications/{app_id}/emojis", json=payload
    ) as response:
        if response.status not in (200, 201):
            body = (await response.text())[:200]
            raise SyncError(f"이모지 생성 실패({response.status}) {name}: {body}")
        return str((await _json(response)).get("id", ""))


async def _delete(session: aiohttp.ClientSession, app_id: str, emoji_id: str) -> None:
    async with session.delete(
        f"{_API}/applications/{app_id}/emojis/{emoji_id}"
    ) as response:
        if response.status not in (200, 204):
            raise SyncError(f"기존 이모지 삭제 실패: HTTP {response.status}")


async def sync(*, games: tuple[str, ...], dry_run: bool, force: bool) -> int:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        print("DISCORD_TOKEN 이 설정되지 않았습니다. .env 를 확인하세요.", file=sys.stderr)
        return 1

    headers = {"Authorization": f"Bot {token}", "User-Agent": "KoaBot/1.0"}
    # 세션을 둘로 나눈다. 봇 토큰이 붙은 세션으로 CommunityDragon 이나
    # valorant-api.com 을 부르면 제3자 CDN 에 토큰을 그대로 넘기게 된다.
    async with (
        aiohttp.ClientSession(headers=headers, timeout=_TIMEOUT) as discord,
        aiohttp.ClientSession(
            headers={"User-Agent": "KoaBot/1.0"}, timeout=_TIMEOUT
        ) as assets,
    ):
        targets: dict[str, str] = {}
        if GAME_LOL in games:
            targets.update(lol_emblem_urls())
        if GAME_VALORANT in games:
            targets.update(await _valorant_targets(assets))

        app_id = await _application_id(discord)
        existing = await _existing_emojis(discord, app_id)

        planned = [name for name in targets if force or name not in existing]
        skipped = len(targets) - len(planned)
        if skipped:
            print(f"이미 올라간 이모지 {skipped}개는 건너뜁니다 (--force 로 덮어쓰기).")
        if not planned:
            print("올릴 것이 없습니다.")
            return 0
        if dry_run:
            for name in planned:
                print(f"  [dry-run] {name}  ←  {targets[name]}")
            print(f"\n대상 {len(planned)}개.")
            return 0

        for name in planned:
            image = await _download(assets, targets[name])
            if len(image) > _MAX_EMOJI_BYTES:
                print(
                    f"  건너뜀 {name}: {len(image):,}바이트로 상한"
                    f"({_MAX_EMOJI_BYTES:,})을 넘습니다.",
                    file=sys.stderr,
                )
                continue
            if name in existing:
                await _delete(discord, app_id, existing[name])
            emoji_id = await _create(discord, app_id, name, image)
            print(f"  올림 {name}  →  <:{name}:{emoji_id}>")

    print("\n완료. 봇을 재시작하면 다음 on_ready 에서 새 이모지를 읽어 갑니다.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="업로드 없이 대상만 출력"
    )
    parser.add_argument(
        "--force", action="store_true", help="이미 있는 이모지도 지우고 다시 올림"
    )
    parser.add_argument(
        "--game",
        choices=GAMES,
        action="append",
        help="이 게임만 올림 (여러 번 지정 가능, 기본은 전부)",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(
            sync(
                games=tuple(args.game) if args.game else GAMES,
                dry_run=args.dry_run,
                force=args.force,
            )
        )
    except SyncError as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
