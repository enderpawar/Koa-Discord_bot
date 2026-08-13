"""Party recruitment commands, persistent buttons, reminders, and expiry."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.party_store import Party, PartyMutation, PartyStore
from cogs.ui import BRAND_COLOR, INFO_COLOR, notice_embed

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
MAX_SCHEDULE_DAYS = 30
_CLOCK_RE = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})$")
_RELATIVE_RE = re.compile(r"^(?P<amount>\d{1,3})\s*(?P<unit>분|시간)\s*(?:뒤|후)$")
MAX_TITLE_LENGTH = 50
# "지금"으로 연 파티는 시작 시각이 곧 생성 시각이다. 시작 시각에 자동 마감하면
# 올리자마자 닫히므로, 즉시 모집에는 별도의 모집 창을 준다.
INSTANT_PARTY_WINDOW = timedelta(hours=2)
# 이 안쪽으로 시작하는 파티는 예약이 아니라 "지금 하자"로 본다.
INSTANT_START_SLACK = timedelta(minutes=1)
_NOW_WORDS = {"지금", "바로", "즉시", "now"}
# 마감/취소된 파티를 지우기까지 남겨 두는 기간. 시작 시각 기준이다.
# 짧게 두면 "어제 그 파티 누구 왔었지" 를 못 보고, 길게 두면 행이 계속 쌓인다.
PARTY_RETENTION_DAYS = 7
PARTY_CLEANUP_INTERVAL_HOURS = 6


def parse_party_start(value: str, *, now: datetime | None = None) -> datetime:
    """Parse a compact Korean party time into a KST datetime.

    빈 문자열과 `지금` 은 "지금 바로" 다. 실제 모집 글의 대부분이 시간을 안 적고
    올린 즉시 시작하므로, 그게 기본값이어야 한다.
    """
    current = (now or datetime.now(KST)).astimezone(KST)
    normalized = " ".join(value.strip().split())
    if not normalized or normalized.lower() in _NOW_WORDS:
        return current

    relative = _RELATIVE_RE.fullmatch(normalized)
    if relative is not None:
        amount = int(relative.group("amount"))
        delta = (
            timedelta(minutes=amount)
            if relative.group("unit") == "분"
            else timedelta(hours=amount)
        )
        if delta > timedelta(days=MAX_SCHEDULE_DAYS):
            raise ValueError(
                f"파티는 최대 {MAX_SCHEDULE_DAYS}일 뒤까지만 모집할 수 있습니다."
            )
        return current + delta

    day_offset: int | None = None
    clock_text = normalized
    for prefix, offset in (("오늘 ", 0), ("내일 ", 1)):
        if normalized.startswith(prefix):
            day_offset = offset
            clock_text = normalized[len(prefix) :]
            break

    match = _CLOCK_RE.fullmatch(clock_text)
    if match:
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
        if hour > 23 or minute > 59:
            raise ValueError("시간은 `HH:MM` 형식으로 입력해 주세요.")
        base_date = (current + timedelta(days=day_offset or 0)).date()
        parsed = datetime(
            base_date.year,
            base_date.month,
            base_date.day,
            hour,
            minute,
            tzinfo=KST,
        )
        if day_offset is None and parsed <= current:
            parsed += timedelta(days=1)
    else:
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M", "%m-%d %H:%M"):
            try:
                candidate = datetime.strptime(normalized, fmt)
            except ValueError:
                continue
            year = candidate.year if fmt.startswith("%Y") else current.year
            parsed = candidate.replace(year=year, tzinfo=KST)
            if fmt.startswith("%m") and parsed <= current:
                parsed = parsed.replace(year=current.year + 1)
            break
        if parsed is None:
            raise ValueError(
                "시작 시간은 `지금`, `30분 뒤`, `오늘 21:00`, `내일 19:30`, "
                "`2026-08-01 20:00` 형식으로 입력해 주세요."
            )

    if parsed <= current:
        raise ValueError("시작 시간은 현재보다 이후여야 합니다.")
    if parsed > current + timedelta(days=MAX_SCHEDULE_DAYS):
        raise ValueError(f"파티는 최대 {MAX_SCHEDULE_DAYS}일 뒤까지만 모집할 수 있습니다.")
    return parsed


def _member_name(guild: discord.Guild | None, user_id: int) -> str:
    member = guild.get_member(user_id) if guild is not None else None
    return member.display_name if member is not None else f"<@{user_id}>"


def can_mention_game_role(
    role: discord.Role, permissions: discord.Permissions
) -> bool:
    """Whether Discord will allow the bot to notify the selected role."""
    return not role.is_default() and (
        role.mentionable or permissions.mention_everyone
    )


_STATUS_LABELS = {
    "open": "🟢 모집 중",
    "cancelled": "🚫 모집 취소",
}
_CLOSED_LABEL = "⚫ 모집 마감"


def is_instant_party(party: Party) -> bool:
    """올리자마자 시작하는 모집인지. 예약 파티와 마감·표시 방식이 다르다."""
    return party.starts_at <= party.created_at + INSTANT_START_SLACK.total_seconds()


def format_headcount(party: Party) -> str:
    if party.capacity <= 0:
        return f"**{len(party.members)}명** · 제한 없음"
    return f"**{len(party.members)} / {party.capacity}명**"


def _start_label(party: Party) -> str:
    """평문 시작 시각. 자동완성 항목처럼 `<t:…>` 가 안 통하는 곳에서 쓴다."""
    if is_instant_party(party):
        return "지금"
    return datetime.fromtimestamp(party.starts_at, KST).strftime("%m/%d %H:%M")


def party_embed(party: Party, guild: discord.Guild | None = None) -> discord.Embed:
    is_open = party.status == "open"
    embed = discord.Embed(
        title=f"🎮 {party.title}",
        description=party.note or "같이 플레이할 파티원을 모집합니다.",
        color=BRAND_COLOR if is_open else INFO_COLOR,
    )
    timestamp = int(party.starts_at)
    embed.add_field(
        name="시작",
        value=(
            "**지금 바로**"
            if is_instant_party(party)
            else f"<t:{timestamp}:t> (<t:{timestamp}:R>)"
        ),
        inline=True,
    )
    embed.add_field(name="인원", value=format_headcount(party), inline=True)
    embed.add_field(
        name="상태",
        value=_STATUS_LABELS.get(party.status, _CLOSED_LABEL),
        inline=True,
    )
    member_lines = [
        f"{index}. {_member_name(guild, user_id)}"
        for index, user_id in enumerate(party.members, start=1)
    ]
    embed.add_field(
        name="참가자",
        value="\n".join(member_lines) or "아직 참가자가 없습니다.",
        inline=False,
    )
    if party.waitlist:
        wait_lines = [
            f"{index}. {_member_name(guild, user_id)}"
            for index, user_id in enumerate(party.waitlist, start=1)
        ]
        embed.add_field(
            name=f"대기자 ({len(party.waitlist)}명)",
            value="\n".join(wait_lines),
            inline=False,
        )
    if party.expires_at > party.starts_at:
        # 즉시 모집. footer 는 <t:…> 를 렌더링하지 않아서 상대 시간으로 적는다.
        hours = max(1, round((party.expires_at - party.starts_at) / 3600))
        open_note = f"{hours}시간 뒤 자동 마감"
    else:
        open_note = "시작 시 자동 마감"
    footer_note = {
        "open": open_note,
        "cancelled": "모집자가 취소함",
    }.get(party.status, "모집 종료")

    # 모집자를 아바타와 함께 상단에 세운다. 누가 여는 파티인지가 한눈에 들어온다.
    # 멤버 캐시에 없으면(재시작 직후, 서버를 떠난 경우) 아바타를 못 구하므로
    # 기존처럼 footer 에 이름만 남긴다 — footer 는 멘션을 렌더링하지 않아서
    # 그 경우 ID 가 그대로 보이지만, 임베드가 깨지는 것보다는 낫다.
    owner = guild.get_member(party.owner_id) if guild is not None else None
    if owner is not None:
        avatar = owner.display_avatar.url
        embed.set_author(name=f"{owner.display_name} 님이 모집", icon_url=avatar)
        # author 아이콘은 이름 옆 24px 라 잘 안 보인다. 실제로 눈에 들어오는
        # 크기는 썸네일 슬롯이므로 프로필 사진은 이쪽에도 건다.
        embed.set_thumbnail(url=avatar)
        embed.set_footer(text=footer_note)
    else:
        embed.set_footer(
            text=f"모집자: {_member_name(guild, party.owner_id)} · {footer_note}"
        )
    return embed


def disabled_party_view() -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="참가",
            style=discord.ButtonStyle.success,
            custom_id="party:join",
            disabled=True,
        )
    )
    view.add_item(
        discord.ui.Button(
            label="참가 취소",
            style=discord.ButtonStyle.secondary,
            custom_id="party:cancel",
            disabled=True,
        )
    )
    view.add_item(
        discord.ui.Button(
            label="모집 마감",
            style=discord.ButtonStyle.danger,
            custom_id="party:close",
            disabled=True,
        )
    )
    return view


MAX_CAPACITY = 20
_CAPACITY_RE = re.compile(r"^(?P<size>\d{1,3})\s*(?:명|인)?$")
_UNLIMITED_WORDS = {"제한 없음", "제한없음", "무제한", "없음", "자유", "0"}


def parse_capacity(value: str) -> int:
    """`5`, `5명`, `제한 없음`, 빈칸을 정원 숫자로. 0 은 제한 없음이다."""
    normalized = " ".join(value.strip().split())
    if not normalized or normalized in _UNLIMITED_WORDS:
        return 0

    match = _CAPACITY_RE.fullmatch(normalized)
    if match is None:
        raise ValueError(
            "정원은 `5`, `5명` 처럼 숫자로 적거나, 비워 두면 제한 없음이 됩니다."
        )
    size = int(match.group("size"))
    if size == 1:
        raise ValueError(
            "정원은 모집자를 포함해 2명 이상이어야 합니다. "
            "인원을 정하지 않으려면 비워 두세요."
        )
    if size > MAX_CAPACITY:
        raise ValueError(f"정원은 최대 {MAX_CAPACITY}명까지 정할 수 있습니다.")
    return size


class PartyCreateModal(discord.ui.Modal, title="파티 모집"):
    """`/파티모집` 이 여는 입력 폼.

    슬래시 옵션으로 받던 것을 한 화면으로 옮겼다. 옵션 방식은 제목을 넣고 나면
    남은 항목이 이름만 나열돼서 흐름이 끊겼고, `시작`·`정원` 후보를 보려면 항목을
    하나씩 눌러 봇에 왕복해야 했다. 모달은 다섯 칸이 한 번에 뜨고, 제출하면
    저절로 닫힌다.

    `시작`·`정원` 은 자유 입력이다. 드롭다운이면 `2026-08-01 20:00` 이나 7명 같은
    값을 아예 못 넣는데, 목록을 넉넉히 채워도 결국 누군가는 목록 밖을 원한다.
    대신 잘못 적으면 되물어야 해서, 그때 쓰라고 `draft` 로 다시 채워 연다.
    """

    def __init__(
        self,
        cog: "PartyCog",
        *,
        recent_title: str = "",
        draft: dict[str, str] | None = None,
    ) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        filled = draft or {}
        self.title_field = discord.ui.Label(
            text="제목",
            description="무엇을 하는 모집인지",
            component=discord.ui.TextInput(
                placeholder=recent_title or "리썰 하실분",
                default=filled.get("title") or None,
                max_length=MAX_TITLE_LENGTH,
            ),
        )
        self.start_field = discord.ui.Label(
            text="시작",
            description="비워 두면 지금 바로 (21:00, 내일 19:30, 30분 뒤 …)",
            component=discord.ui.TextInput(
                placeholder="지금",
                default=filled.get("start") or None,
                required=False,
                max_length=32,
            ),
        )
        self.capacity_field = discord.ui.Label(
            text="정원",
            description="비워 두면 제한 없음 (2~20명)",
            component=discord.ui.TextInput(
                placeholder="제한 없음",
                default=filled.get("capacity") or None,
                required=False,
                max_length=8,
            ),
        )
        self.note_field = discord.ui.Label(
            text="메모",
            description="파티 설명이나 조건 (선택)",
            component=discord.ui.TextInput(
                style=discord.TextStyle.paragraph,
                default=filled.get("note") or None,
                required=False,
                max_length=500,
            ),
        )
        self.role_field = discord.ui.Label(
            text="알림 역할",
            description="이 역할에게 모집 알림을 보냅니다 (선택)",
            component=discord.ui.RoleSelect(required=False),
        )
        for field in (
            self.title_field,
            self.start_field,
            self.capacity_field,
            self.note_field,
            self.role_field,
        ):
            self.add_item(field)

    def draft(self) -> dict[str, str]:
        """사용자가 방금 친 값. 입력을 되물을 때 그대로 다시 채운다."""
        return {
            "title": str(self.title_field.component.value or ""),
            "start": str(self.start_field.component.value or ""),
            "capacity": str(self.capacity_field.component.value or ""),
            "note": str(self.note_field.component.value or ""),
        }

    async def on_submit(self, interaction: discord.Interaction) -> None:
        roles = getattr(self.role_field.component, "values", None) or []
        entered = self.draft()
        await self.cog.open_party(
            interaction,
            title=entered["title"],
            start=entered["start"],
            capacity=entered["capacity"],
            note=entered["note"],
            ping_role=roles[0] if roles else None,
            draft=entered,
        )

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        log.exception(
            "party modal failed: guild_id=%s", interaction.guild_id, exc_info=error
        )
        embed = notice_embed(
            "처리 실패", "파티 모집을 열지 못했습니다. 다시 시도해 주세요.", tone="error"
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            log.exception("party modal error reply failed: guild_id=%s", interaction.guild_id)


class PartyRetryView(discord.ui.View):
    """입력이 반려됐을 때 쓰는 `다시 입력` 버튼.

    모달은 제출과 동시에 닫혀서, 되물으면 사용자가 친 것이 전부 날아간다. 이
    버튼은 방금 친 값을 그대로 채운 폼을 다시 연다.
    """

    def __init__(self, cog: "PartyCog", draft: dict[str, str]) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.draft = draft

    @discord.ui.button(label="다시 입력", style=discord.ButtonStyle.primary)
    async def retry(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            PartyCreateModal(self.cog, draft=self.draft)
        )


class PartyView(discord.ui.View):
    def __init__(self, cog: "PartyCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="참가",
        style=discord.ButtonStyle.success,
        custom_id="party:join",
    )
    async def join_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self.cog.handle_party_action(interaction, "join")

    @discord.ui.button(
        label="참가 취소",
        style=discord.ButtonStyle.secondary,
        custom_id="party:cancel",
    )
    async def cancel_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self.cog.handle_party_action(interaction, "cancel")

    @discord.ui.button(
        label="모집 마감",
        style=discord.ButtonStyle.danger,
        custom_id="party:close",
    )
    async def close_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self.cog.handle_party_action(interaction, "close")


class PartyCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = PartyStore()
        self.view = PartyView(self)
        self._message_locks: dict[tuple[int, int], asyncio.Lock] = {}

    async def cog_load(self) -> None:
        self.bot.add_view(self.view)
        self.party_scheduler.start()
        self.party_cleanup.start()

    async def cog_unload(self) -> None:
        self.party_scheduler.cancel()
        self.party_cleanup.cancel()

    async def _recent_title_hint(self, guild_id: int) -> str:
        """모달 제목 칸의 placeholder. 실패해도 모달은 떠야 한다 (Rule 03).

        `send_modal` 은 defer 를 못 해서 3초 안에 응답해야 한다. 저장소 락이
        길게 잡혀 있는 순간에 걸려도 명령이 죽지 않도록 시간을 끊는다.
        """
        try:
            titles = await asyncio.wait_for(
                self.store.recent_titles(guild_id, limit=1), 1.0
            )
        except Exception:
            log.exception("party title hint failed: guild_id=%s", guild_id)
            return ""
        return titles[0] if titles else ""

    @app_commands.command(name="파티모집", description="함께 플레이할 파티원을 모집합니다")
    async def create_party(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or interaction.channel_id is None:
            await interaction.response.send_message(
                embed=notice_embed(
                    "사용 불가", "서버 채널에서만 파티를 모집할 수 있습니다.", tone="warn"
                ),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            PartyCreateModal(
                self, recent_title=await self._recent_title_hint(interaction.guild_id)
            )
        )

    async def _reject(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        draft: dict[str, str] | None = None,
    ) -> None:
        """입력을 되묻는다. 친 값이 있으면 그대로 채워 다시 열 버튼을 붙인다."""
        view = PartyRetryView(self, draft) if draft else discord.utils.MISSING
        await interaction.response.send_message(
            embed=notice_embed(title, description, tone="warn"),
            view=view,
            ephemeral=True,
        )

    async def open_party(
        self,
        interaction: discord.Interaction,
        *,
        title: str,
        start: str,
        capacity: str,
        note: str,
        ping_role: discord.Role | None,
        draft: dict[str, str] | None = None,
    ) -> None:
        """모달 제출을 받아 모집글을 연다. `시작`·`정원` 은 자유 입력이라 여기서 판다."""
        if (
            interaction.guild is None
            or interaction.guild_id is None
            or interaction.channel_id is None
        ):
            await interaction.response.send_message(
                embed=notice_embed(
                    "사용 불가", "서버 채널에서만 파티를 모집할 수 있습니다.", tone="warn"
                ),
                ephemeral=True,
            )
            return
        # 제목은 순수 텍스트다. 알림 대상은 `알림 역할` 칸에서만 온다.
        party_title = " ".join(title.split())
        mention_role = (
            ping_role
            if ping_role is not None
            and can_mention_game_role(ping_role, interaction.app_permissions)
            else None
        )

        if not party_title:
            await self._reject(interaction, "입력 확인", "제목을 입력해 주세요.", draft)
            return
        if len(party_title) > MAX_TITLE_LENGTH or len(note.strip()) > 500:
            await self._reject(
                interaction,
                "입력 확인",
                f"제목은 {MAX_TITLE_LENGTH}자, 메모는 500자 이하로 입력해 주세요.",
                draft,
            )
            return
        try:
            party_capacity = parse_capacity(capacity)
        except ValueError as exc:
            await self._reject(interaction, "정원 확인", str(exc), draft)
            return
        try:
            starts_at = parse_party_start(start)
        except ValueError as exc:
            await self._reject(interaction, "시간 확인", str(exc), draft)
            return

        # 지금 시작하는 모집은 시작 시각에 닫으면 올리자마자 마감된다.
        instant = starts_at <= datetime.now(KST) + INSTANT_START_SLACK
        expires_at = starts_at + INSTANT_PARTY_WINDOW if instant else starts_at

        party = await self.store.create(
            interaction.guild_id,
            interaction.channel_id,
            interaction.user.id,
            title=party_title,
            capacity=party_capacity,
            starts_at=starts_at.timestamp(),
            expires_at=expires_at.timestamp(),
            note=note,
        )
        allowed_mentions = discord.AllowedMentions(
            everyone=False,
            users=False,
            roles=[mention_role] if mention_role is not None else False,
            replied_user=False,
        )
        try:
            await interaction.response.send_message(
                content=mention_role.mention if mention_role is not None else None,
                embed=party_embed(party, interaction.guild),
                view=self.view,
                allowed_mentions=allowed_mentions,
            )
            message = await interaction.original_response()
            await self.store.bind_message(
                interaction.guild_id, party.id, message.id
            )
        except Exception:
            await self.store.delete(interaction.guild_id, party.id)
            log.exception(
                "party create response failed: guild_id=%s party_id=%s",
                interaction.guild_id,
                party.id,
            )
            if interaction.response.is_done():
                await interaction.followup.send(
                    embed=notice_embed(
                        "생성 실패",
                        "파티 모집 메시지를 저장하지 못했습니다. 다시 시도해 주세요.",
                        tone="error",
                    ),
                    ephemeral=True,
                )
            return
        log.info(
            "party created: guild_id=%s party_id=%s owner_id=%s role_ping=%s",
            interaction.guild_id,
            party.id,
            interaction.user.id,
            mention_role.id if mention_role is not None else None,
        )
        if ping_role is not None and mention_role is None:
            # 파티는 이미 열렸다. 태그만 못 붙은 이유를 모집자에게만 알린다.
            await interaction.followup.send(
                embed=notice_embed(
                    "태그 생략",
                    f"파티는 열렸지만 {ping_role.name} 역할에는 알림을 보내지 "
                    "못했습니다. 역할의 멘션 허용 설정을 켜거나 봇에 "
                    "`모든 역할 멘션` 권한을 주세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )

    @app_commands.command(name="파티목록", description="현재 모집 중인 파티를 확인합니다")
    async def party_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=notice_embed(
                    "사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"
                ),
                ephemeral=True,
            )
            return
        parties = await self.store.list_open(interaction.guild_id, limit=10)
        await interaction.response.send_message(
            embed=self._party_list_embed(parties, title="현재 모집 중인 파티"),
            ephemeral=True,
        )

    @app_commands.command(name="내파티", description="내가 참가하거나 대기 중인 파티를 확인합니다")
    async def my_parties(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=notice_embed(
                    "사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"
                ),
                ephemeral=True,
            )
            return
        parties = await self.store.list_for_user(
            interaction.guild_id, interaction.user.id, limit=10
        )
        await interaction.response.send_message(
            embed=self._party_list_embed(parties, title="내 파티"),
            ephemeral=True,
        )

    async def _owned_party_choices(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        try:
            parties = await self.store.list_owned(
                interaction.guild_id, interaction.user.id, limit=25
            )
        except Exception:
            # 자동완성이 실패해도 명령 자체는 살아 있어야 한다 (Rule 03).
            log.exception(
                "party cancel autocomplete failed: guild_id=%s", interaction.guild_id
            )
            return []
        keyword = current.strip().lower()
        return [
            app_commands.Choice(
                # 자동완성 항목은 평문이다. `<t:…>` 는 여기서 렌더링되지 않는다.
                name=f"{party.title} · {_start_label(party)}"[:100],
                value=str(party.id),
            )
            for party in parties
            if not keyword or keyword in party.title.lower()
        ][:25]

    @app_commands.command(name="파티취소", description="내가 연 파티 모집을 취소합니다")
    @app_commands.rename(party="파티")
    @app_commands.describe(party="취소할 파티 (내가 연 모집만 보입니다)")
    @app_commands.autocomplete(party=_owned_party_choices)
    async def cancel_party(
        self, interaction: discord.Interaction, party: str
    ) -> None:
        if interaction.guild is None or interaction.guild_id is None:
            await interaction.response.send_message(
                embed=notice_embed(
                    "사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"
                ),
                ephemeral=True,
            )
            return
        try:
            party_id = int(party)
        except ValueError:
            # 자동완성을 쓰지 않고 아무 문자열이나 입력한 경우.
            await interaction.response.send_message(
                embed=notice_embed(
                    "입력 확인",
                    "목록에서 취소할 파티를 골라 주세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        mutation = await self.store.delete_owned(
            interaction.guild_id, party_id, interaction.user.id
        )
        if mutation.outcome == "party_cancelled" and mutation.party is not None:
            await self._mark_party_cancelled(mutation.party, interaction.guild)
            log.info(
                "party cancelled: guild_id=%s party_id=%s owner_id=%s",
                interaction.guild_id,
                mutation.party.id,
                interaction.user.id,
            )
        await interaction.followup.send(
            embed=notice_embed(
                "파티 모집",
                self._mutation_message(mutation),
                tone="ok" if mutation.outcome == "party_cancelled" else "warn",
            ),
            ephemeral=True,
        )

    async def _mark_party_cancelled(
        self, party: Party, guild: discord.Guild | None
    ) -> None:
        """모집 메시지를 취소 상태로 바꾸고 참가자에게 알린다.

        저장소 행은 이미 지워졌다. 메시지 편집이나 알림이 실패해도 취소 자체는
        되돌리지 않는다 — 남는 것은 버튼이 죽은 낡은 메시지 하나뿐이다 (Rule 03).
        """
        cancelled = replace(party, status="cancelled")
        channel = self.bot.get_channel(party.channel_id)
        if channel is not None and hasattr(channel, "get_partial_message"):
            try:
                await channel.get_partial_message(party.message_id).edit(
                    embed=party_embed(cancelled, guild),
                    view=disabled_party_view(),
                )
            except discord.HTTPException:
                log.exception(
                    "party cancel message edit failed: guild_id=%s party_id=%s",
                    party.guild_id,
                    party.id,
                )

        others = [
            user_id
            for user_id in (*party.members, *party.waitlist)
            if user_id != party.owner_id
        ]
        if not others or channel is None or not hasattr(channel, "send"):
            return
        try:
            await channel.send(
                " ".join(f"<@{user_id}>" for user_id in others)
                + f"\n🚫 **{party.title}** 파티 모집이 취소됐습니다.",
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False
                ),
            )
        except discord.HTTPException:
            log.exception(
                "party cancel notice failed: guild_id=%s party_id=%s",
                party.guild_id,
                party.id,
            )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        try:
            removed = await self.store.remove_guild(guild.id)
            self._message_locks = {
                key: lock
                for key, lock in self._message_locks.items()
                if key[0] != guild.id
            }
            log.info(
                "party guild data removed: guild_id=%s parties=%s",
                guild.id,
                removed,
            )
        except Exception:
            log.exception("party guild cleanup failed: guild_id=%s", guild.id)

    async def handle_party_action(
        self, interaction: discord.Interaction, action: str
    ) -> None:
        if (
            interaction.guild is None
            or interaction.guild_id is None
            or interaction.message is None
        ):
            await interaction.response.send_message(
                embed=notice_embed(
                    "처리 불가", "파티 정보를 찾을 수 없습니다.", tone="warn"
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        key = (interaction.guild_id, interaction.message.id)
        lock = self._message_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if action == "join":
                mutation = await self.store.join(
                    interaction.guild_id,
                    interaction.message.id,
                    interaction.user.id,
                )
            elif action == "cancel":
                mutation = await self.store.cancel(
                    interaction.guild_id,
                    interaction.message.id,
                    interaction.user.id,
                )
            elif action == "close":
                permissions = getattr(interaction, "permissions", None)
                if permissions is None:
                    permissions = getattr(
                        interaction.user, "guild_permissions", None
                    )
                can_manage = bool(permissions and permissions.manage_messages)
                mutation = await self.store.close(
                    interaction.guild_id,
                    interaction.message.id,
                    interaction.user.id,
                    can_manage_messages=can_manage,
                )
            else:
                mutation = PartyMutation(None, "invalid")

            if mutation.party is not None:
                await interaction.message.edit(
                    embed=party_embed(mutation.party, interaction.guild),
                    view=(
                        self.view
                        if mutation.party.status == "open"
                        else disabled_party_view()
                    ),
                )

        description = self._mutation_message(mutation)
        await interaction.followup.send(
            embed=notice_embed(
                "파티 모집",
                description,
                tone=(
                    "ok"
                    if mutation.outcome
                    in {"joined", "waitlisted", "cancelled", "closed_now"}
                    else "info"
                ),
            ),
            ephemeral=True,
        )
        if (
            mutation.promoted_user_id is not None
            and mutation.party is not None
            and interaction.channel is not None
        ):
            try:
                await interaction.channel.send(
                    (
                        f"<@{mutation.promoted_user_id}>님, **{mutation.party.title}** "
                        "파티의 빈자리가 생겨 대기에서 참가로 변경됐습니다."
                    ),
                    allowed_mentions=discord.AllowedMentions(
                        users=True, roles=False, everyone=False
                    ),
                )
            except discord.HTTPException:
                log.exception(
                    "party promotion notice failed: guild_id=%s party_id=%s",
                    interaction.guild_id,
                    mutation.party.id,
                )
        if mutation.outcome in {"joined", "waitlisted", "cancelled", "closed_now"}:
            log.info(
                "party action: guild_id=%s message_id=%s user_id=%s action=%s outcome=%s",
                interaction.guild_id,
                interaction.message.id,
                interaction.user.id,
                action,
                mutation.outcome,
            )

    @staticmethod
    def _mutation_message(mutation: PartyMutation) -> str:
        messages = {
            "joined": "파티에 참가했습니다.",
            "waitlisted": "정원이 가득 차 대기열에 등록했습니다.",
            "already_member": "이미 참가 중인 파티입니다.",
            "already_waiting": "이미 대기 중인 파티입니다.",
            "cancelled": "참가 또는 대기를 취소했습니다.",
            "not_joined": "참가하거나 대기 중인 파티가 아닙니다.",
            "owner_cannot_cancel": "모집자는 참가를 취소할 수 없습니다. 모집 마감을 사용해 주세요.",
            "closed_now": "파티 모집을 마감했습니다.",
            "closed": "이미 마감된 파티입니다.",
            "forbidden": "모집자 또는 메시지 관리 권한이 있는 사용자만 마감할 수 있습니다.",
            "party_cancelled": "파티 모집을 취소했습니다.",
            "not_owner": "모집자만 파티를 취소할 수 있습니다. 마감만 하려면 모집 메시지의 `모집 마감` 버튼을 눌러 주세요.",
            "not_found": "저장된 파티 정보를 찾을 수 없습니다.",
            "invalid": "지원하지 않는 동작입니다.",
        }
        text = messages.get(mutation.outcome, "파티 요청을 처리하지 못했습니다.")
        if mutation.promoted_user_id is not None:
            text += f"\n대기 중이던 <@{mutation.promoted_user_id}>님이 참가자로 이동했습니다."
        return text

    @staticmethod
    def _party_list_embed(parties: list[Party], *, title: str) -> discord.Embed:
        embed = discord.Embed(title=title, color=BRAND_COLOR)
        if not parties:
            embed.description = "현재 해당하는 파티가 없습니다."
            return embed
        for party in parties:
            url = (
                f"https://discord.com/channels/{party.guild_id}/"
                f"{party.channel_id}/{party.message_id}"
            )
            headcount = (
                f"{len(party.members)}명"
                if party.capacity <= 0
                else f"{len(party.members)}/{party.capacity}명"
            )
            when = (
                "지금 진행 중"
                if is_instant_party(party)
                else f"<t:{int(party.starts_at)}:R>"
            )
            embed.add_field(
                name=f"{party.title} · {headcount}",
                value=f"{when} · [모집 메시지로 이동]({url})",
                inline=False,
            )
        embed.set_footer(text="최대 10개까지 표시합니다.")
        return embed

    @tasks.loop(seconds=30)
    async def party_scheduler(self) -> None:
        try:
            reminders = await self.store.claim_due_reminders()
        except Exception:
            log.exception("party reminder scan failed: guild_id=all")
            reminders = []
        for party in reminders:
            try:
                await self._send_reminder(party)
            except Exception:
                log.exception(
                    "party reminder failed: guild_id=%s party_id=%s",
                    party.guild_id,
                    party.id,
                )

        try:
            expired = await self.store.claim_expired()
        except Exception:
            log.exception("party expiry scan failed: guild_id=all")
            return
        for party in expired:
            try:
                await self._update_party_message(party)
            except Exception:
                log.exception(
                    "party expiry update failed: guild_id=%s party_id=%s",
                    party.guild_id,
                    party.id,
                )

    @party_scheduler.before_loop
    async def before_party_scheduler(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=PARTY_CLEANUP_INTERVAL_HOURS)
    async def party_cleanup(self) -> None:
        """오래된 마감/취소 파티를 지운다.

        30초짜리 스케줄러에 얹지 않는 이유: 이 DELETE 는 parties 전체를 훑는데,
        지울 것이 하루에 몇 건 생기는 작업을 30초마다 돌릴 이유가 없다.
        """
        try:
            removed = await self.store.purge_old(
                retention_sec=PARTY_RETENTION_DAYS * 86400
            )
        except Exception:
            log.exception("party cleanup failed: guild_id=all")
            return
        if removed:
            log.info("party cleanup removed %d finished parties", removed)

    @party_cleanup.before_loop
    async def before_party_cleanup(self) -> None:
        await self.bot.wait_until_ready()

    async def _send_reminder(self, party: Party) -> None:
        channel = self.bot.get_channel(party.channel_id)
        if channel is None or not hasattr(channel, "send"):
            log.warning(
                "party reminder channel missing: guild_id=%s party_id=%s",
                party.guild_id,
                party.id,
            )
            return
        mentions = " ".join(f"<@{user_id}>" for user_id in party.members)
        url = (
            f"https://discord.com/channels/{party.guild_id}/"
            f"{party.channel_id}/{party.message_id}"
        )
        await channel.send(
            f"{mentions}\n⏰ **{party.title}** 파티 시작까지 30분 이하 남았습니다.\n{url}",
            allowed_mentions=discord.AllowedMentions(
                users=True, roles=False, everyone=False
            ),
        )
        log.info(
            "party reminder sent: guild_id=%s party_id=%s",
            party.guild_id,
            party.id,
        )

    async def _update_party_message(self, party: Party) -> None:
        guild = self.bot.get_guild(party.guild_id)
        channel = self.bot.get_channel(party.channel_id)
        if channel is None or not hasattr(channel, "get_partial_message"):
            log.warning(
                "party message channel missing: guild_id=%s party_id=%s",
                party.guild_id,
                party.id,
            )
            return
        message = channel.get_partial_message(party.message_id)
        await message.edit(
            embed=party_embed(party, guild),
            view=disabled_party_view(),
        )
        log.info(
            "party expired: guild_id=%s party_id=%s",
            party.guild_id,
            party.id,
        )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        log.exception(
            "party command error: guild_id=%s cmd=%s",
            interaction.guild_id,
            interaction.command.name if interaction.command else "?",
            exc_info=error,
        )
        embed = notice_embed(
            "처리 실패", "파티 명령 처리 중 오류가 발생했습니다.", tone="error"
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            log.exception(
                "party error response failed: guild_id=%s",
                interaction.guild_id,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PartyCog(bot))
