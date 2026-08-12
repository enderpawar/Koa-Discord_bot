"""발로란트 전적 조회 명령 (op.gg 형태).

HenrikDev(비공식) API 로 라이엇ID → 현재/최고 랭크 + 최근 경기를 임베드로 보여준다.
`/발로란트 등록`으로 디스코드 계정에 라이엇ID를 한 번 묶어두면 이후
`/발로란트 전적`만으로 조회된다.

키(VALORANT_API_KEY) 미설정이면 mc_cog 와 동일하게 명령이 '설정 필요'로 응답한다.
"""
from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from cogs import valorant_api as api
from cogs.ui import BRAND_COLOR, notice_embed
from cogs.valorant_store import ValorantStore

log = logging.getLogger(__name__)

# 분당 30회(Basic 키) 제한 보호 + 남용 방지. 유저별 최소 호출 간격.
_COOLDOWN_SEC = 6.0

_REGION_CHOICES = [
    app_commands.Choice(name="한국", value="kr"),
    app_commands.Choice(name="아시아 태평양", value="ap"),
    app_commands.Choice(name="북미", value="na"),
    app_commands.Choice(name="유럽", value="eu"),
]

_PLATFORM_CHOICES = [
    app_commands.Choice(name="컴퓨터", value="pc"),
    app_commands.Choice(name="콘솔", value="console"),
]

_REGION_LABELS = {choice.value: choice.name for choice in _REGION_CHOICES}

_TIER_EMOJI = {
    "iron": "⚙️",
    "bronze": "🥉",
    "silver": "🥈",
    "gold": "🥇",
    "platinum": "💠",
    "diamond": "💎",
    "ascendant": "🌿",
    "immortal": "🔮",
    "radiant": "☀️",
}


class _Cooldown:
    """유저별 최소 호출 간격(인메모리). 봇 재시작 시 초기화."""

    def __init__(self, interval: float = _COOLDOWN_SEC) -> None:
        self._interval = interval
        self._last: dict[int, float] = {}

    def retry_after(self, user_id: int) -> float:
        now = time.monotonic()
        remaining = self._interval - (now - self._last.get(user_id, 0.0))
        return max(0.0, remaining)

    def stamp(self, user_id: int) -> None:
        self._last[user_id] = time.monotonic()


def _parse_riot_id(riot_id: str) -> tuple[str, str] | None:
    """'닉네임#TAG' → (name, tag). '#' 없거나 어느 한쪽이 비면 None."""
    raw = riot_id.strip()
    if "#" not in raw:
        return None
    name, _, tag = raw.rpartition("#")
    name, tag = name.strip(), tag.strip()
    if not name or not tag:
        return None
    return name, tag


def _tier_emoji(tier_name: str) -> str:
    key = tier_name.split()[0].lower() if tier_name else ""
    return _TIER_EMOJI.get(key, "🎯")


def _resolved_region(account: dict, fallback: str) -> str:
    """계정 응답의 실제 리전을 우선하고, 없으면 사용자가 고른 값을 쓴다."""
    region = str(account.get("region", "")).strip().lower()
    return region if region in api.VALID_REGIONS else fallback


def _card_image_url(account: dict) -> str | None:
    """HenrikDev account v1/v2의 서로 다른 card 형식을 모두 수용한다."""
    card = account.get("card")
    if isinstance(card, dict):
        candidate = card.get("small")
    elif isinstance(card, str) and card.startswith(("https://", "http://")):
        candidate = card
    else:
        candidate = None
    return candidate if isinstance(candidate, str) and candidate else None


def _match_line(match: dict) -> str:
    meta = match.get("meta") or {}
    stats = match.get("stats") or {}
    teams = match.get("teams") or {}

    map_name = (meta.get("map") or {}).get("name", "?")
    kills = stats.get("kills", 0)
    deaths = stats.get("deaths", 0)
    assists = stats.get("assists", 0)

    team = str(stats.get("team", "")).lower()
    mine = teams.get(team)
    other = teams.get("blue" if team == "red" else "red")
    if isinstance(mine, int) and isinstance(other, int):
        if mine > other:
            result = "✅"
        elif mine < other:
            result = "❌"
        else:
            result = "➖"
        score = f"{mine}-{other}"
    else:
        result = "▫️"
        score = "?"

    return f"{result} `{map_name}` {kills}/{deaths}/{assists} · `{score}`"


def _error_embed(exc: Exception) -> discord.Embed:
    if isinstance(exc, api.ValorantConfigError):
        return notice_embed(
            "설정이 필요합니다",
            "발로란트 조회가 아직 설정되지 않았습니다. 관리자에게 문의하세요.",
            tone="warn",
        )
    if isinstance(exc, api.ValorantNotFound):
        return notice_embed(
            "찾을 수 없습니다",
            "해당 라이엇ID 또는 전적을 찾지 못했습니다. `닉네임#태그`와 리전을 확인하세요.",
            tone="warn",
        )
    if isinstance(exc, api.ValorantRateLimited):
        return notice_embed(
            "잠시 후 다시 시도하세요",
            "조회 요청이 많아 잠시 제한되었습니다. 잠깐 뒤에 다시 시도해 주세요.",
            tone="warn",
        )
    log.warning("valorant lookup failed: %s", exc)
    return notice_embed("조회 실패", "전적 정보를 가져오지 못했습니다.", tone="error")


def _profile_embed(
    name: str,
    tag: str,
    region: str,
    account: dict,
    mmr: dict,
    matches: list[dict],
) -> discord.Embed:
    current = mmr.get("current") or {}
    peak = mmr.get("peak") or {}
    current_tier = (current.get("tier") or {}).get("name") or "무순위"
    peak_tier = (peak.get("tier") or {}).get("name") or "기록 없음"
    rr = current.get("rr")

    embed = discord.Embed(
        title=f"{name}#{tag}",
        color=BRAND_COLOR,
    )
    embed.set_author(name=f"발로란트 · {region.upper()}")

    card_img = _card_image_url(account)
    if card_img:
        embed.set_thumbnail(url=card_img)

    rank_value = f"{_tier_emoji(current_tier)} {current_tier}"
    if isinstance(rr, int):
        rank_value += f" · {rr} RR"
    embed.add_field(name="현재 랭크", value=rank_value, inline=True)
    embed.add_field(
        name="최고 랭크", value=f"{_tier_emoji(peak_tier)} {peak_tier}", inline=True
    )

    level = account.get("account_level")
    if isinstance(level, int):
        embed.add_field(name="레벨", value=f"{level}", inline=True)

    if matches:
        lines = [_match_line(match) for match in matches[:5]]
        embed.add_field(name="최근 경기", value="\n".join(lines), inline=False)

    embed.set_footer(text="전적 제공: HenrikDev (비공식 API)")
    return embed


class ValorantCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = ValorantStore()
        self.cooldown = _Cooldown()

    async def cog_unload(self) -> None:
        await api.close_session()

    # 등록은 서버 단위로 격리된다. DM 에서는 대상 서버를 특정할 수 없어
    # guild_id 가 None 이 되고, 그러면 모든 DM 사용자가 한 버킷을 공유한다.
    # 서버 전용으로 못박아 그 경로를 없앤다.
    valorant = app_commands.Group(
        name="발로란트", description="발로란트 전적 조회", guild_only=True
    )

    # ---- 공통 --------------------------------------------------------------

    async def _check_cooldown(self, interaction: discord.Interaction) -> bool:
        wait = self.cooldown.retry_after(interaction.user.id)
        if wait > 0:
            await interaction.response.send_message(
                embed=notice_embed(
                    "잠시만요",
                    f"{wait:.0f}초 뒤에 다시 시도해 주세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return False
        self.cooldown.stamp(interaction.user.id)
        return True

    async def _send_profile(
        self,
        interaction: discord.Interaction,
        name: str,
        tag: str,
        region: str,
        platform: str = api.DEFAULT_PLATFORM,
    ) -> None:
        try:
            account = await api.get_account(name, tag)
            region = _resolved_region(account, region)
            name = str(account.get("name") or name)
            tag = str(account.get("tag") or tag)
            puuid = account.get("puuid")
            if not isinstance(puuid, str) or not puuid:
                raise api.ValorantApiError("account response has no puuid")

            mmr = await api.get_mmr(region, name, tag, platform=platform)
            try:
                matches = await api.get_recent_matches(region, puuid)
            except (
                api.ValorantApiError,
                api.ValorantNotFound,
                api.ValorantRateLimited,
                api.ValorantConfigError,
            ):
                matches = []  # 전적은 best-effort — 실패해도 랭크는 보여준다
        except Exception as exc:  # noqa: BLE001 — 타입별로 사용자 메시지 분기
            await interaction.followup.send(embed=_error_embed(exc), ephemeral=True)
            return
        await interaction.followup.send(
            embed=_profile_embed(name, tag, region, account, mmr, matches)
        )

    # ---- /발로란트 등록 ----------------------------------------------------

    @valorant.command(name="등록", description="내 디스코드 계정에 라이엇ID를 등록합니다")
    @app_commands.rename(
        riot_id="라이엇아이디",
        region="지역",
        platform="플랫폼",
    )
    @app_commands.describe(
        riot_id="닉네임#태그 (예: Hide on bush#KR1)",
        region="계정 리전 (생략 시 API 자동 감지)",
        platform="플레이 플랫폼",
    )
    @app_commands.choices(region=_REGION_CHOICES)
    @app_commands.choices(platform=_PLATFORM_CHOICES)
    async def register(
        self,
        interaction: discord.Interaction,
        riot_id: str,
        region: app_commands.Choice[str] | None = None,
        platform: app_commands.Choice[str] | None = None,
    ) -> None:
        if not await self._check_cooldown(interaction):
            return
        parsed = _parse_riot_id(riot_id)
        if parsed is None:
            await interaction.response.send_message(
                embed=notice_embed(
                    "형식 오류",
                    "`닉네임#태그` 형식으로 입력하세요. 예: `Hide on bush#KR1`",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return
        name, tag = parsed
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            account = await api.get_account(name, tag)
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(embed=_error_embed(exc), ephemeral=True)
            return

        # API 가 정규화한 표기를 그대로 저장 (대소문자/공백 보정)
        name = str(account.get("name") or name)
        tag = str(account.get("tag") or tag)
        region_value = _resolved_region(
            account, region.value if region else api.default_region()
        )
        platform_value = platform.value if platform else api.DEFAULT_PLATFORM
        await self.store.set(
            interaction.guild_id,
            interaction.user.id,
            name=name,
            tag=tag,
            region=region_value,
            platform=platform_value,
        )
        await interaction.followup.send(
            embed=notice_embed(
                "등록 완료",
                f"`{name}#{tag}` ({_REGION_LABELS.get(region_value, region_value.upper())}, "
                f"{platform_value.upper()}) 계정을 등록했습니다. "
                "이제 `/발로란트 전적`으로 바로 조회할 수 있어요.",
                tone="ok",
            ),
            ephemeral=True,
        )

    # ---- /발로란트 전적 ----------------------------------------------------

    @valorant.command(name="전적", description="등록된 라이엇ID로 전적을 조회합니다")
    @app_commands.rename(member="멤버")
    @app_commands.describe(member="조회할 멤버 (생략 시 본인)")
    async def profile(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ) -> None:
        if not await self._check_cooldown(interaction):
            return
        target = member or interaction.user
        reg = await self.store.get(interaction.guild_id, target.id)
        if reg is None:
            who = "해당 멤버는" if member else "먼저"
            await interaction.response.send_message(
                embed=notice_embed(
                    "등록이 필요합니다",
                    f"{who} `/발로란트 등록`으로 라이엇ID를 등록해야 합니다.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True)
        await self._send_profile(
            interaction,
            reg["name"],
            reg["tag"],
            reg.get("region", api.default_region()),
            reg.get("platform", api.DEFAULT_PLATFORM),
        )

    # ---- /발로란트 검색 ----------------------------------------------------

    @valorant.command(name="검색", description="등록하지 않고 라이엇ID로 전적을 조회합니다")
    @app_commands.rename(
        riot_id="라이엇아이디",
        region="지역",
        platform="플랫폼",
    )
    @app_commands.describe(
        riot_id="닉네임#태그",
        region="계정 리전 (생략 시 API 자동 감지)",
        platform="플레이 플랫폼",
    )
    @app_commands.choices(region=_REGION_CHOICES)
    @app_commands.choices(platform=_PLATFORM_CHOICES)
    async def lookup(
        self,
        interaction: discord.Interaction,
        riot_id: str,
        region: app_commands.Choice[str] | None = None,
        platform: app_commands.Choice[str] | None = None,
    ) -> None:
        if not await self._check_cooldown(interaction):
            return
        parsed = _parse_riot_id(riot_id)
        if parsed is None:
            await interaction.response.send_message(
                embed=notice_embed(
                    "형식 오류",
                    "`닉네임#태그` 형식으로 입력하세요. 예: `Hide on bush#KR1`",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return
        name, tag = parsed
        region_value = region.value if region else api.default_region()
        platform_value = platform.value if platform else api.DEFAULT_PLATFORM
        await interaction.response.defer(thinking=True)
        await self._send_profile(interaction, name, tag, region_value, platform_value)

    # ---- /발로란트 등록해제 ------------------------------------------------

    @valorant.command(name="등록해제", description="내 계정에 등록한 라이엇ID를 삭제합니다")
    async def unregister(self, interaction: discord.Interaction) -> None:
        removed = await self.store.remove(interaction.guild_id, interaction.user.id)
        if removed:
            embed = notice_embed("삭제 완료", "등록된 라이엇ID를 삭제했습니다.", tone="ok")
        else:
            embed = notice_embed("등록 없음", "등록된 라이엇ID가 없습니다.", tone="info")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ValorantCog(bot))
