"""Party recruitment commands, persistent buttons, reminders, and expiry."""
from __future__ import annotations

import asyncio
import logging
import re
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
MAX_GAME_NAME_LENGTH = 50


def parse_party_start(value: str, *, now: datetime | None = None) -> datetime:
    """Parse a compact Korean party time into a future KST datetime."""
    current = (now or datetime.now(KST)).astimezone(KST)
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError("시작 시간을 입력해 주세요.")

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
                "시작 시간은 `오늘 21:00`, `내일 19:30`, `21:00`, "
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


def party_embed(party: Party, guild: discord.Guild | None = None) -> discord.Embed:
    is_open = party.status == "open"
    embed = discord.Embed(
        title=f"🎮 {party.game} 파티 모집",
        description=party.note or "같이 플레이할 파티원을 모집합니다.",
        color=BRAND_COLOR if is_open else INFO_COLOR,
    )
    timestamp = int(party.starts_at)
    embed.add_field(
        name="시작",
        value=f"<t:{timestamp}:t> (<t:{timestamp}:R>)",
        inline=True,
    )
    embed.add_field(
        name="인원",
        value=f"**{len(party.members)} / {party.capacity}명**",
        inline=True,
    )
    embed.add_field(
        name="상태",
        value="🟢 모집 중" if is_open else "⚫ 모집 마감",
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
    embed.set_footer(
        text=f"모집자: {_member_name(guild, party.owner_id)} · 시작 시 자동 마감"
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

    async def cog_unload(self) -> None:
        self.party_scheduler.cancel()

    @app_commands.command(name="파티모집", description="함께 플레이할 파티원을 모집합니다")
    @app_commands.rename(
        game="게임", capacity="정원", start="시작", note="메모", ping_role="태그"
    )
    @app_commands.describe(
        game="게임 이름 (롤, 발로 …)",
        capacity="모집 정원(2~20명, 모집자 포함)",
        start="오늘 21:00, 내일 19:30, 2026-08-01 20:00",
        note="파티 설명이나 조건",
        ping_role="알림을 보낼 역할 (선택)",
    )
    async def create_party(
        self,
        interaction: discord.Interaction,
        game: str,
        capacity: app_commands.Range[int, 2, 20],
        start: str,
        note: str = "",
        ping_role: discord.Role | None = None,
    ) -> None:
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
        # 게임 이름은 순수 텍스트다. 알림 대상은 `태그` 옵션에서만 온다.
        game_name = " ".join(game.split())
        mention_role = (
            ping_role
            if ping_role is not None
            and can_mention_game_role(ping_role, interaction.app_permissions)
            else None
        )

        if not game_name:
            await interaction.response.send_message(
                embed=notice_embed(
                    "입력 확인", "게임 이름을 입력해 주세요.", tone="warn"
                ),
                ephemeral=True,
            )
            return
        if len(game_name) > MAX_GAME_NAME_LENGTH or len(note.strip()) > 500:
            await interaction.response.send_message(
                embed=notice_embed(
                    "입력 확인",
                    f"게임 이름은 {MAX_GAME_NAME_LENGTH}자, 메모는 500자 이하로 "
                    "입력해 주세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return
        try:
            starts_at = parse_party_start(start)
        except ValueError as exc:
            await interaction.response.send_message(
                embed=notice_embed("시간 확인", str(exc), tone="warn"),
                ephemeral=True,
            )
            return

        party = await self.store.create(
            interaction.guild_id,
            interaction.channel_id,
            interaction.user.id,
            game=game_name,
            capacity=int(capacity),
            starts_at=starts_at.timestamp(),
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
                        f"<@{mutation.promoted_user_id}>님, **{mutation.party.game}** "
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
            embed.add_field(
                name=f"{party.game} · {len(party.members)}/{party.capacity}명",
                value=(
                    f"<t:{int(party.starts_at)}:R> · "
                    f"[모집 메시지로 이동]({url})"
                ),
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
            f"{mentions}\n⏰ **{party.game}** 파티 시작까지 30분 이하 남았습니다.\n{url}",
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
