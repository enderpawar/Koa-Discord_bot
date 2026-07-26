from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

from cogs.fortune_cog import daily_fortune, fortune_embed

KST = ZoneInfo("Asia/Seoul")


def test_daily_fortune_is_stable_for_same_user_and_kst_date() -> None:
    morning = datetime(2026, 7, 26, 1, 0, tzinfo=KST)
    evening = datetime(2026, 7, 26, 23, 59, tzinfo=KST)

    assert daily_fortune(123, now=morning) == daily_fortune(123, now=evening)


def test_daily_fortune_uses_kst_day_boundary() -> None:
    before_midnight = datetime(2026, 7, 26, 23, 59, tzinfo=KST)
    after_midnight = before_midnight + timedelta(minutes=2)

    before = daily_fortune(123, now=before_midnight)
    after = daily_fortune(123, now=after_midnight)

    assert before.fortune_date.isoformat() == "2026-07-26"
    assert after.fortune_date.isoformat() == "2026-07-27"
    assert before != after


def test_daily_fortune_fields_are_in_expected_ranges() -> None:
    fortune = daily_fortune(
        456, now=datetime(2026, 7, 26, 12, 0, tzinfo=KST)
    )

    assert 55 <= fortune.score <= 100
    assert fortune.total
    assert fortune.game
    assert fortune.relationship
    assert fortune.lucky_color
    assert fortune.lucky_item
    assert fortune.lucky_number > 0


def test_fortune_embed_is_clearly_entertainment_only() -> None:
    fortune = daily_fortune(
        789, now=datetime(2026, 7, 26, 12, 0, tzinfo=KST)
    )
    embed = fortune_embed("테스터", fortune)

    assert embed.title == "🔮 테스터님의 오늘의 운세"
    assert [field.name for field in embed.fields] == [
        "🎮 게임운",
        "🤝 관계운",
        "🍀 행운 포인트",
    ]
    assert "재미로만 봐주세요" in embed.footer.text


async def test_fortune_extension_registers_command() -> None:
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    try:
        await bot.load_extension("cogs.fortune_cog")
        names = {command.name for command in bot.tree.get_commands()}

        assert "오늘의운세" in names
    finally:
        if bot.get_cog("FortuneCog") is not None:
            await bot.unload_extension("cogs.fortune_cog")
        await bot.close()
