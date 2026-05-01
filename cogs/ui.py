"""Shared Discord UI formatting helpers."""
from __future__ import annotations

import discord


BRAND_COLOR = discord.Color.from_rgb(88, 101, 242)
OK_COLOR = discord.Color.green()
WARN_COLOR = discord.Color.orange()
ERROR_COLOR = discord.Color.red()
INFO_COLOR = discord.Color.dark_grey()


def channel_ref(channel_id: int | None) -> str:
    return f"<#{channel_id}>" if channel_id else "미설정"


def enabled_label(enabled: bool) -> str:
    return "켜짐" if enabled else "꺼짐"


def notice_embed(
    title: str,
    description: str,
    *,
    tone: str = "info",
) -> discord.Embed:
    colors = {
        "ok": OK_COLOR,
        "warn": WARN_COLOR,
        "error": ERROR_COLOR,
        "info": INFO_COLOR,
    }
    return discord.Embed(
        title=title,
        description=description,
        color=colors.get(tone, INFO_COLOR),
    )
