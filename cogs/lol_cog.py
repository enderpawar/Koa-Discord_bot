"""리그 오브 레전드 전적 조회 명령 (op.gg 형태).

Riot 공식 API 로 라이엇ID → 솔로/자유 랭크 + 최근 경기를 임베드로 보여준다.
`/롤 등록`으로 한 번 등록하면 이후 `/롤 전적`으로 바로 조회된다.

키(RIOT_API_KEY) 미설정이면 명령이 '설정 필요'로 응답한다. 개발 키는 24시간마다
만료되므로 만료 시 키 거부로 처리해 안내한다.
"""
from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from cogs import lol_api as api
from cogs.lol_store import LolStore
from cogs.ui import BRAND_COLOR, notice_embed

log = logging.getLogger(__name__)

# 개발 키 한도(2분당 100 / 초당 20) 보호 + 남용 방지. profile 1회가 매치 상세까지
# 여러 요청을 쓰므로 발로란트보다 넉넉히 잡는다.
_COOLDOWN_SEC = 8.0
_RECENT_MATCH_COUNT = 3

_SOLO_QUEUE = "RANKED_SOLO_5x5"
_FLEX_QUEUE = "RANKED_FLEX_SR"

_PLATFORM_CHOICES = [
    app_commands.Choice(name="한국", value="kr"),
    app_commands.Choice(name="일본", value="jp1"),
    app_commands.Choice(name="북미", value="na1"),
    app_commands.Choice(name="유럽 서부", value="euw1"),
    app_commands.Choice(name="유럽 북동부", value="eun1"),
    app_commands.Choice(name="브라질", value="br1"),
    app_commands.Choice(name="라틴아메리카 북부", value="la1"),
    app_commands.Choice(name="라틴아메리카 남부", value="la2"),
    app_commands.Choice(name="오세아니아", value="oc1"),
    app_commands.Choice(name="튀르키예", value="tr1"),
    app_commands.Choice(name="러시아", value="ru"),
    app_commands.Choice(name="필리핀", value="ph2"),
    app_commands.Choice(name="싱가포르", value="sg2"),
    app_commands.Choice(name="태국", value="th2"),
    app_commands.Choice(name="대만", value="tw2"),
    app_commands.Choice(name="베트남", value="vn2"),
]

_TIER_EMOJI = {
    "IRON": "⚙️",
    "BRONZE": "🥉",
    "SILVER": "🥈",
    "GOLD": "🥇",
    "PLATINUM": "💠",
    "EMERALD": "💚",
    "DIAMOND": "💎",
    "MASTER": "🔮",
    "GRANDMASTER": "👑",
    "CHALLENGER": "☀️",
}


class _Cooldown:
    """유저별 최소 호출 간격(인메모리). 봇 재시작 시 초기화."""

    def __init__(self, interval: float = _COOLDOWN_SEC) -> None:
        self._interval = interval
        self._last: dict[int, float] = {}

    def retry_after(self, user_id: int) -> float:
        now = time.monotonic()
        return max(0.0, self._interval - (now - self._last.get(user_id, 0.0)))

    def stamp(self, user_id: int) -> None:
        self._last[user_id] = time.monotonic()


def _parse_riot_id(riot_id: str) -> tuple[str, str] | None:
    """'닉네임#태그' → (name, tag). '#' 없거나 어느 한쪽이 비면 None."""
    raw = riot_id.strip()
    if "#" not in raw:
        return None
    name, _, tag = raw.rpartition("#")
    name, tag = name.strip(), tag.strip()
    if not name or not tag:
        return None
    return name, tag


def _tier_emoji(tier: str) -> str:
    return _TIER_EMOJI.get((tier or "").upper(), "🎮")


def _entry_for_queue(entries: list[dict], queue_type: str) -> dict | None:
    for entry in entries:
        if entry.get("queueType") == queue_type:
            return entry
    return None


def _rank_value(entry: dict | None) -> str:
    if not entry:
        return "언랭크"
    tier = entry.get("tier", "")
    rank = entry.get("rank", "")
    lp = entry.get("leaguePoints", 0)
    wins = int(entry.get("wins", 0))
    losses = int(entry.get("losses", 0))
    total = wins + losses
    winrate = round(wins / total * 100) if total else 0
    tier_label = f"{tier.title()} {rank}".strip()
    return (
        f"{_tier_emoji(tier)} {tier_label} · {lp} LP\n"
        f"`{wins}승 {losses}패 · {winrate}%`"
    )


def _match_line(match: dict, puuid: str) -> str | None:
    info = match.get("info") or {}
    participants = info.get("participants") or []
    me = next((p for p in participants if p.get("puuid") == puuid), None)
    if me is None:
        return None
    champ = me.get("championName", "?")
    k = me.get("kills", 0)
    d = me.get("deaths", 0)
    a = me.get("assists", 0)
    result = "✅" if me.get("win") else "❌"
    return f"{result} `{champ}` {k}/{d}/{a}"


def _error_embed(exc: Exception) -> discord.Embed:
    if isinstance(exc, api.LolConfigError):
        return notice_embed(
            "설정이 필요합니다",
            "롤 조회가 아직 설정되지 않았거나 API 키가 만료되었습니다. 관리자에게 문의하세요.",
            tone="warn",
        )
    if isinstance(exc, api.LolNotFound):
        return notice_embed(
            "찾을 수 없습니다",
            "해당 라이엇ID 또는 전적을 찾지 못했습니다. `닉네임#태그`와 지역을 확인하세요.",
            tone="warn",
        )
    if isinstance(exc, api.LolRateLimited):
        return notice_embed(
            "잠시 후 다시 시도하세요",
            "조회 요청이 많아 잠시 제한되었습니다. 잠깐 뒤에 다시 시도해 주세요.",
            tone="warn",
        )
    log.warning("lol lookup failed: %s", exc)
    return notice_embed("조회 실패", "전적 정보를 가져오지 못했습니다.", tone="error")


def _profile_embed(
    name: str,
    tag: str,
    platform: str,
    summoner: dict,
    entries: list[dict],
    matches: list[str],
) -> discord.Embed:
    embed = discord.Embed(title=f"{name}#{tag}", color=BRAND_COLOR)
    embed.set_author(name=f"리그 오브 레전드 · {platform.upper()}")

    icon_id = summoner.get("profileIconId")
    if isinstance(icon_id, int):
        embed.set_thumbnail(url=api.profile_icon_url(icon_id))

    embed.add_field(
        name="솔로 랭크", value=_rank_value(_entry_for_queue(entries, _SOLO_QUEUE)), inline=True
    )
    embed.add_field(
        name="자유 랭크", value=_rank_value(_entry_for_queue(entries, _FLEX_QUEUE)), inline=True
    )

    level = summoner.get("summonerLevel")
    if isinstance(level, int):
        embed.add_field(name="레벨", value=f"{level}", inline=True)

    if matches:
        embed.add_field(name="최근 경기", value="\n".join(matches), inline=False)

    embed.set_footer(text="제공: Riot Games API")
    return embed


class LolCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = LolStore()
        self.cooldown = _Cooldown()

    async def cog_unload(self) -> None:
        await api.close_session()

    # 등록은 서버 단위로 격리된다. DM 에서는 대상 서버를 특정할 수 없어
    # guild_id 가 None 이 되고, 그러면 모든 DM 사용자가 한 버킷을 공유한다.
    # 서버 전용으로 못박아 그 경로를 없앤다.
    lol = app_commands.Group(
        name="롤", description="리그 오브 레전드 전적 조회", guild_only=True
    )

    async def _check_cooldown(self, interaction: discord.Interaction) -> bool:
        wait = self.cooldown.retry_after(interaction.user.id)
        if wait > 0:
            await interaction.response.send_message(
                embed=notice_embed(
                    "잠시만요", f"{wait:.0f}초 뒤에 다시 시도해 주세요.", tone="warn"
                ),
                ephemeral=True,
            )
            return False
        self.cooldown.stamp(interaction.user.id)
        return True

    async def _fetch_recent_lines(self, regional: str, puuid: str) -> list[str]:
        """최근 경기 요약. 실패해도 프로필은 나오도록 best-effort."""
        try:
            match_ids = await api.get_recent_match_ids(
                regional, puuid, _RECENT_MATCH_COUNT
            )
            lines: list[str] = []
            for match_id in match_ids:
                match = await api.get_match(regional, match_id)
                line = _match_line(match, puuid)
                if line:
                    lines.append(line)
            return lines
        except (
            api.LolApiError,
            api.LolRateLimited,
            api.LolNotFound,
            api.LolConfigError,
        ):
            return []

    async def _send_profile(
        self, interaction: discord.Interaction, name: str, tag: str, platform: str
    ) -> None:
        regional = api.regional_for(platform)
        try:
            account = await api.get_account(regional, name, tag)
            puuid = account.get("puuid")
            if not puuid:
                await interaction.followup.send(
                    embed=_error_embed(api.LolNotFound("no puuid")), ephemeral=True
                )
                return
            summoner = await api.get_summoner(platform, puuid)
            entries = await api.get_league_entries(platform, puuid)
        except Exception as exc:  # noqa: BLE001 — 타입별 사용자 메시지 분기
            await interaction.followup.send(embed=_error_embed(exc), ephemeral=True)
            return

        matches = await self._fetch_recent_lines(regional, puuid)
        # account 가 정규화한 표기 사용
        disp_name = account.get("gameName", name)
        disp_tag = account.get("tagLine", tag)
        await interaction.followup.send(
            embed=_profile_embed(
                disp_name, disp_tag, platform, summoner, entries, matches
            )
        )

    # ---- /롤 등록 ----------------------------------------------------------

    @lol.command(name="등록", description="내 디스코드 계정에 라이엇ID를 등록합니다")
    @app_commands.rename(riot_id="라이엇아이디", region="지역")
    @app_commands.describe(riot_id="닉네임#태그 (예: Hide on bush#KR1)", region="계정 지역")
    @app_commands.choices(region=_PLATFORM_CHOICES)
    async def register(
        self,
        interaction: discord.Interaction,
        riot_id: str,
        region: app_commands.Choice[str],
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
        platform = region.value
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            account = await api.get_account(api.regional_for(platform), name, tag)
            if not account.get("puuid"):
                raise api.LolNotFound("no puuid")
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(embed=_error_embed(exc), ephemeral=True)
            return

        name = account.get("gameName", name)
        tag = account.get("tagLine", tag)
        await self.store.set(
            interaction.guild_id, interaction.user.id, name=name, tag=tag, platform=platform
        )
        await interaction.followup.send(
            embed=notice_embed(
                "등록 완료",
                f"`{name}#{tag}` ({region.name}) 계정을 등록했습니다. "
                "이제 `/롤 전적`으로 바로 조회할 수 있어요.",
                tone="ok",
            ),
            ephemeral=True,
        )

    # ---- /롤 전적 ----------------------------------------------------------

    @lol.command(name="전적", description="등록된 라이엇ID로 전적을 조회합니다")
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
                    f"{who} `/롤 등록`으로 라이엇ID를 등록해야 합니다.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True)
        await self._send_profile(interaction, reg["name"], reg["tag"], reg["platform"])

    # ---- /롤 검색 ----------------------------------------------------------

    @lol.command(name="검색", description="등록하지 않고 라이엇ID로 전적을 조회합니다")
    @app_commands.rename(riot_id="라이엇아이디", region="지역")
    @app_commands.describe(riot_id="닉네임#태그", region="계정 지역")
    @app_commands.choices(region=_PLATFORM_CHOICES)
    async def lookup(
        self,
        interaction: discord.Interaction,
        riot_id: str,
        region: app_commands.Choice[str] | None = None,
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
        platform = region.value if region else api.default_platform()
        await interaction.response.defer(thinking=True)
        await self._send_profile(interaction, name, tag, platform)

    # ---- /롤 등록해제 ------------------------------------------------------

    @lol.command(name="등록해제", description="내 계정에 등록한 라이엇ID를 삭제합니다")
    async def unregister(self, interaction: discord.Interaction) -> None:
        removed = await self.store.remove(interaction.guild_id, interaction.user.id)
        if removed:
            embed = notice_embed("삭제 완료", "등록된 라이엇ID를 삭제했습니다.", tone="ok")
        else:
            embed = notice_embed("등록 없음", "등록된 라이엇ID가 없습니다.", tone="info")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LolCog(bot))
