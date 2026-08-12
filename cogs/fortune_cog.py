"""Deterministic, entertainment-only daily fortune for community engagement."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from cogs.ui import BRAND_COLOR, notice_embed

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

_GAME_FORTUNES = (
    "혼자 캐리하려 하기보다 팀원의 콜을 들으면 흐름이 좋아져요.",
    "첫 판은 손풀기라고 생각하세요. 두 번째 판부터 감각이 살아납니다.",
    "오늘은 익숙한 포지션과 편안한 캐릭터가 좋은 선택이에요.",
    "새로운 전략을 시도하기 좋은 날이에요. 단, 팀원에게 먼저 알려주세요.",
    "무리한 연승 도전보다 기분 좋을 때 마무리하는 판단이 빛나요.",
    "친구와 함께할수록 게임운이 올라갑니다. 먼저 파티를 제안해 보세요.",
    "침착하게 한 번 더 확인하는 습관이 결정적인 실수를 막아줘요.",
    "오늘의 승부처는 피지컬보다 멘탈입니다. 급해질수록 천천히!",
)

_RELATION_FORTUNES = (
    "먼저 건넨 짧은 인사가 즐거운 대화로 이어질 수 있어요.",
    "친구의 작은 활약을 알아봐 주면 분위기가 한층 좋아집니다.",
    "오해가 생기면 농담으로 넘기기보다 차분히 설명하는 게 좋아요.",
    "평소 조용했던 사람의 의견에 귀 기울이면 좋은 아이디어를 얻어요.",
    "오늘은 도움을 요청해도 괜찮은 날이에요. 생각보다 흔쾌히 응해줄 거예요.",
    "함께할 사람을 기다리기보다 먼저 모집하면 좋은 인연이 따라옵니다.",
    "가벼운 칭찬 한마디가 오래 남는 하루가 될 수 있어요.",
    "약속 시간을 한 번 더 확인하면 모두가 편안해집니다.",
)

_TOTAL_FORTUNES = (
    "작은 선택이 기분 좋은 흐름을 만드는 날이에요.",
    "서두르지 않으면 기대보다 매끄럽게 풀릴 거예요.",
    "익숙한 일에서 의외의 재미를 발견할 수 있어요.",
    "하던 일을 마무리한 뒤 새 일을 시작하면 운이 따라옵니다.",
    "계획에 없던 제안이 들어오면 한 번쯤 긍정적으로 살펴보세요.",
    "오늘의 행운은 거창한 결과보다 좋은 사람들과의 시간에 있어요.",
    "조금 쉬어가는 선택이 오히려 다음 흐름을 좋게 만듭니다.",
    "망설이던 일을 가볍게 시작해 보기 좋은 날이에요.",
)

_COLORS = ("보라색", "하늘색", "초록색", "주황색", "분홍색", "남색", "은색", "노란색")
_ITEMS = ("따뜻한 음료", "헤드셋", "작은 메모장", "초콜릿", "물 한 잔", "쿠션", "키링", "이어폰")
_NUMBERS = (2, 3, 5, 7, 8, 11, 17, 21)


@dataclass(frozen=True)
class DailyFortune:
    fortune_date: date
    score: int
    total: str
    game: str
    relationship: str
    lucky_color: str
    lucky_item: str
    lucky_number: int


def daily_fortune(
    user_id: int, *, now: datetime | None = None
) -> DailyFortune:
    """Return the same fortune for the same user and KST calendar date."""
    current = (now or datetime.now(KST)).astimezone(KST)
    fortune_date = current.date()
    digest = hashlib.sha256(
        f"koa-bot-fortune-v1:{fortune_date.isoformat()}:{user_id}".encode("utf-8")
    ).digest()
    return DailyFortune(
        fortune_date=fortune_date,
        score=55 + digest[0] % 46,
        total=_TOTAL_FORTUNES[digest[1] % len(_TOTAL_FORTUNES)],
        game=_GAME_FORTUNES[digest[2] % len(_GAME_FORTUNES)],
        relationship=_RELATION_FORTUNES[digest[3] % len(_RELATION_FORTUNES)],
        lucky_color=_COLORS[digest[4] % len(_COLORS)],
        lucky_item=_ITEMS[digest[5] % len(_ITEMS)],
        lucky_number=_NUMBERS[digest[6] % len(_NUMBERS)],
    )


def fortune_embed(display_name: str, fortune: DailyFortune) -> discord.Embed:
    embed = discord.Embed(
        title=f"🔮 {display_name}님의 오늘의 운세",
        description=f"### 총운 {fortune.score}점\n{fortune.total}",
        color=BRAND_COLOR,
    )
    embed.add_field(name="🎮 게임운", value=fortune.game, inline=False)
    embed.add_field(name="🤝 관계운", value=fortune.relationship, inline=False)
    embed.add_field(
        name="🍀 행운 포인트",
        value=(
            f"색상: **{fortune.lucky_color}** · "
            f"아이템: **{fortune.lucky_item}** · "
            f"숫자: **{fortune.lucky_number}**"
        ),
        inline=False,
    )
    embed.set_footer(
        text=(
            f"{fortune.fortune_date:%Y-%m-%d} KST · "
            "같은 날에는 같은 결과가 나옵니다 · 재미로만 봐주세요"
        )
    )
    return embed


class FortuneShareView(discord.ui.View):
    def __init__(
        self,
        *,
        owner_id: int,
        display_name: str,
        fortune: DailyFortune,
    ) -> None:
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.display_name = display_name
        self.fortune = fortune
        self._shared = False

    @discord.ui.button(
        label="서버에 공유",
        emoji="📣",
        style=discord.ButtonStyle.primary,
    )
    async def share_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=notice_embed(
                    "공유 불가", "운세를 확인한 본인만 공유할 수 있습니다.", tone="warn"
                ),
                ephemeral=True,
            )
            return
        if self._shared:
            await interaction.response.send_message(
                embed=notice_embed(
                    "이미 공유됨", "오늘의 운세를 이미 공유했습니다.", tone="info"
                ),
                ephemeral=True,
            )
            return
        self._shared = True
        button.disabled = True
        await interaction.response.send_message(
            embed=fortune_embed(self.display_name, self.fortune),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            await interaction.edit_original_response(view=self)
        except discord.HTTPException:
            log.exception(
                "fortune share button update failed: guild_id=%s user_id=%s",
                interaction.guild_id,
                interaction.user.id,
            )


class FortuneCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="오늘의운세", description="오늘의 게임·관계 운세를 확인합니다")
    async def today_fortune(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=notice_embed(
                    "사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"
                ),
                ephemeral=True,
            )
            return
        display_name = getattr(interaction.user, "display_name", interaction.user.name)
        fortune = daily_fortune(interaction.user.id)
        await interaction.response.send_message(
            embed=fortune_embed(display_name, fortune),
            view=FortuneShareView(
                owner_id=interaction.user.id,
                display_name=display_name,
                fortune=fortune,
            ),
            ephemeral=True,
        )
        log.info(
            "fortune viewed: guild_id=%s user_id=%s date=%s",
            interaction.guild_id,
            interaction.user.id,
            fortune.fortune_date,
        )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        log.exception(
            "fortune command error: guild_id=%s",
            interaction.guild_id,
            exc_info=error,
        )
        embed = notice_embed(
            "처리 실패", "오늘의 운세를 불러오지 못했습니다.", tone="error"
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            log.exception(
                "fortune error response failed: guild_id=%s",
                interaction.guild_id,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FortuneCog(bot))
