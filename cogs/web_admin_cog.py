"""Token-protected web dashboard for bot administration."""
from __future__ import annotations

import hmac
import logging
import os
import re
from pathlib import Path
from typing import Any

import discord
from aiohttp import web
from discord.ext import commands

from cogs.config_store import ConfigStore
from cogs.rank_cog import DEFAULT_LEADERBOARD_POST_TIME
from cogs.rank_store import weekly_reset_anchor

log = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_COOKIE_NAME = "koa_admin_token"

# 관리자 페이지 HTML 은 파이썬 문자열이 아니라 templates/*.html 에 둔다.
# 에디터 문법 강조와 diff 가 살아나고, 마크업을 고칠 때 cog 를 건드리지 않는다.
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_LOGIN_TEMPLATE = "admin_login.html"
_DASHBOARD_TEMPLATE = "admin_dashboard.html"
_template_cache: dict[str, str] = {}


def _template_reload() -> bool:
    return (os.getenv("ADMIN_WEB_TEMPLATE_RELOAD") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _template(name: str) -> str:
    """templates/<name> 을 읽어 돌려준다.

    기본은 프로세스 수명 동안 1회만 읽는다 (요청마다 디스크를 때리면 이벤트 루프가
    막힌다). ADMIN_WEB_TEMPLATE_RELOAD 를 켜면 매 요청 다시 읽어, 봇을 재시작하지
    않고 마크업 수정을 바로 확인할 수 있다.
    """
    if _template_reload():
        return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")
    if name not in _template_cache:
        _template_cache[name] = (_TEMPLATE_DIR / name).read_text(encoding="utf-8")
    return _template_cache[name]


def _web_enabled() -> bool:
    value = os.getenv("ADMIN_WEB_ENABLED")
    if value is not None:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(os.getenv("ADMIN_WEB_TOKEN"))


def _web_host() -> str:
    # 외부 공개는 항상 명시적으로만. 호스팅 환경을 추측해 0.0.0.0 으로 여는 분기를
    # 두지 않는다 — 토큰 하나로 보호되는 어드민이라 기본값은 로컬 바인딩이어야 한다.
    # 외부에서 접근하려면 ADMIN_WEB_HOST=0.0.0.0 을 직접 설정한다 (docs/deploy-oracle.md §6).
    return os.getenv("ADMIN_WEB_HOST") or "127.0.0.1"


def _web_port() -> int:
    raw = os.getenv("ADMIN_WEB_PORT") or os.getenv("PORT") or "8080"
    try:
        return int(raw)
    except ValueError:
        return 8080


def _clean_time(value: str | None) -> str:
    candidate = (value or DEFAULT_LEADERBOARD_POST_TIME).strip()
    if not _TIME_RE.match(candidate):
        raise ValueError("invalid post_time")
    return candidate


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _id(value: int) -> str:
    return str(value)


def _channel_payload(channel: discord.abc.GuildChannel) -> dict[str, str]:
    return {"id": _id(channel.id), "name": channel.name}


def _row_with_name(guild: discord.Guild, row: dict[str, Any]) -> dict[str, Any]:
    user_id = int(row.get("user_id", 0) or 0)
    member = guild.get_member(user_id) if user_id else None
    if member is not None:
        name = member.display_name
    elif user_id:
        name = f"ID {user_id}"
    else:
        name = "unknown"
    return {
        "user_id": _id(user_id),
        "name": name,
        "voice_seconds": int(row.get("voice_seconds", 0) or 0),
        "message_count": int(row.get("message_count", 0) or 0),
        "score": int(row.get("score", 0) or 0),
    }


def _allowed_guild_ids() -> set[int]:
    raw = os.getenv("ADMIN_WEB_GUILD_IDS") or os.getenv("TEST_GUILD_ID") or ""
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        value = part.strip()
        if value.isdigit():
            ids.add(int(value))
    return ids


class WebAdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = ConfigStore()
        self._token = os.getenv("ADMIN_WEB_TOKEN", "")
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def cog_load(self) -> None:
        if not _web_enabled():
            log.info("web admin disabled")
            return
        if not self._token:
            log.warning("web admin requested but ADMIN_WEB_TOKEN is not set")
            return

        # 템플릿을 미리 읽어 둔다. 여기서 걸러야 첫 접속 때 500 을 보는 대신
        # 기동 로그에서 원인을 알 수 있다 (Rule 03: 봇 전체를 죽이지는 않는다).
        try:
            _template(_LOGIN_TEMPLATE)
            _template(_DASHBOARD_TEMPLATE)
        except OSError:
            log.exception("web admin templates not readable under %s", _TEMPLATE_DIR)
            return

        app = web.Application(middlewares=[self._auth_middleware])
        app.add_routes(
            [
                web.get("/", self._index),
                web.get("/login", self._login),
                web.post("/login", self._login_submit),
                web.post("/logout", self._logout),
                web.get("/api/state", self._api_state),
                web.get("/api/leaderboard", self._api_leaderboard),
                web.get("/api/leaderboard-history", self._api_leaderboard_history),
                web.post("/api/config", self._api_config),
                web.post("/api/post-leaderboard", self._api_post_leaderboard),
                web.post("/api/clear-leaderboard", self._api_clear_leaderboard),
            ]
        )
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, _web_host(), _web_port())
        await self._site.start()
        log.info("web admin listening on http://%s:%s", _web_host(), _web_port())

    async def cog_unload(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        if request.path == "/login":
            return await handler(request)
        if self._authorized(request):
            return await handler(request)
        if request.path.startswith("/api/"):
            return web.json_response({"error": "unauthorized"}, status=401)
        raise web.HTTPFound("/login")

    def _authorized(self, request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        bearer = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        supplied = (
            bearer
            or request.headers.get("X-Admin-Token", "")
            or request.cookies.get(_COOKIE_NAME, "")
            or request.query.get("token", "")
        )
        return bool(supplied) and hmac.compare_digest(supplied, self._token)

    async def _login(self, _: web.Request) -> web.Response:
        return web.Response(text=_template(_LOGIN_TEMPLATE), content_type="text/html")

    async def _login_submit(self, request: web.Request) -> web.Response:
        data = await request.post()
        token = str(data.get("token", ""))
        if not hmac.compare_digest(token, self._token):
            return web.Response(text=_template(_LOGIN_TEMPLATE).replace("<!--ERROR-->", "<p class=\"error\">토큰이 올바르지 않습니다.</p>"), content_type="text/html", status=401)
        response = web.HTTPFound("/")
        response.set_cookie(
            _COOKIE_NAME,
            token,
            httponly=True,
            secure=False,
            samesite="Strict",
            max_age=60 * 60 * 12,
        )
        raise response

    async def _logout(self, _: web.Request) -> web.Response:
        response = web.json_response({"ok": True})
        response.del_cookie(_COOKIE_NAME)
        return response

    async def _index(self, _: web.Request) -> web.Response:
        return web.Response(text=_template(_DASHBOARD_TEMPLATE), content_type="text/html")

    async def _api_state(self, request: web.Request) -> web.Response:
        guild_id = request.query.get("guild_id")
        guilds = self._visible_guilds()
        guild = self._guild(guild_id) if guild_id else (guilds[0] if guilds else None)
        if guild is None:
            return web.json_response(
                {
                    "guilds": [{"id": _id(item.id), "name": item.name} for item in guilds],
                    "selected": None,
                }
            )

        cfg = await self.store.get(guild.id)
        return web.json_response(
            {
                "guilds": [{"id": _id(item.id), "name": item.name} for item in guilds],
                "selected": {
                    "id": _id(guild.id),
                    "name": guild.name,
                    "config": cfg,
                    "text_channels": [_channel_payload(ch) for ch in guild.text_channels],
                    "voice_channels": [_channel_payload(ch) for ch in guild.voice_channels],
                },
                "env": {
                    "host": _web_host(),
                    "port": _web_port(),
                    "config_path": os.getenv("CONFIG_PATH", "config.json"),
                    "rank_path": os.getenv("RANK_PATH", "rank_stats.json"),
                    "test_guild_id": os.getenv("TEST_GUILD_ID", ""),
                },
            }
        )

    async def _api_config(self, request: web.Request) -> web.Response:
        payload = await request.json()
        guild = self._guild(payload.get("guild_id"))
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)

        fields: dict[str, Any] = {}
        try:
            if "tts_channel_id" in payload:
                fields["tts_channel_id"] = self._text_channel_id(guild, payload["tts_channel_id"])
            if "voice_channel_id" in payload:
                fields["voice_channel_id"] = self._voice_channel_id(guild, payload["voice_channel_id"])
            if "leaderboard_channel_id" in payload:
                fields["leaderboard_channel_id"] = self._text_channel_id(guild, payload["leaderboard_channel_id"])
            if "leaderboard_daily_enabled" in payload:
                fields["leaderboard_daily_enabled"] = _bool_value(payload["leaderboard_daily_enabled"])
            if "leaderboard_post_time" in payload:
                fields["leaderboard_post_time"] = _clean_time(payload["leaderboard_post_time"])
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

        await self.store.set(guild.id, **fields)
        return web.json_response({"ok": True, "config": await self.store.get(guild.id)})

    async def _api_leaderboard(self, request: web.Request) -> web.Response:
        guild = self._guild(request.query.get("guild_id"))
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)
        rank_cog = self.bot.get_cog("RankCog")
        if rank_cog is None or not hasattr(rank_cog, "store"):
            return web.json_response(
                {"anchor": weekly_reset_anchor(), "rows": [], "available": False}
            )
        await rank_cog.store.ensure_week()
        rows = await rank_cog.store.leaderboard(guild.id, limit=10)
        return web.json_response(
            {
                "anchor": weekly_reset_anchor(),
                "rows": [_row_with_name(guild, row) for row in rows],
                "available": True,
            }
        )

    async def _api_leaderboard_history(self, request: web.Request) -> web.Response:
        guild = self._guild(request.query.get("guild_id"))
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)
        rank_cog = self.bot.get_cog("RankCog")
        if rank_cog is None or not hasattr(rank_cog, "store"):
            return web.json_response({"weeks": [], "available": False})
        weeks = await rank_cog.store.list_history(guild.id)
        return web.json_response(
            {
                "weeks": [
                    {
                        "anchor": entry.get("anchor"),
                        "archived_at": entry.get("archived_at"),
                        "top": [_row_with_name(guild, row) for row in entry.get("top", [])],
                    }
                    for entry in weeks
                ],
                "available": True,
            }
        )

    async def _api_post_leaderboard(self, request: web.Request) -> web.Response:
        payload = await request.json()
        guild = self._guild(payload.get("guild_id"))
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)
        cfg = await self.store.get(guild.id)
        channel = guild.get_channel(cfg.get("leaderboard_channel_id"))
        if not isinstance(channel, discord.TextChannel):
            return web.json_response({"error": "leaderboard channel is not configured"}, status=400)
        rank_cog = self.bot.get_cog("RankCog")
        if rank_cog is None or not hasattr(rank_cog, "_leaderboard_embed"):
            return web.json_response({"error": "rank cog unavailable"}, status=500)
        await rank_cog.store.ensure_week()
        embed = await rank_cog._leaderboard_embed(guild, limit=10)
        if embed is None:
            return web.json_response({"error": "no activity stats"}, status=400)
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return web.json_response({"ok": True})

    async def _api_clear_leaderboard(self, request: web.Request) -> web.Response:
        payload = await request.json()
        guild = self._guild(payload.get("guild_id"))
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)
        if payload.get("confirm") != "CLEAR":
            return web.json_response({"error": "confirmation required"}, status=400)
        rank_cog = self.bot.get_cog("RankCog")
        if rank_cog is None or not hasattr(rank_cog, "store"):
            return web.json_response({"error": "rank cog unavailable"}, status=500)
        result = await rank_cog.store.clear_guild(guild.id)
        return web.json_response({"ok": True, **result})

    def _guild(self, guild_id: Any) -> discord.Guild | None:
        try:
            target_id = int(guild_id)
        except (TypeError, ValueError):
            return None
        allowed_ids = _allowed_guild_ids()
        if allowed_ids and target_id not in allowed_ids:
            return None
        return self.bot.get_guild(target_id)

    def _visible_guilds(self) -> list[discord.Guild]:
        allowed_ids = _allowed_guild_ids()
        if not allowed_ids:
            return list(self.bot.guilds)
        return [guild for guild in self.bot.guilds if guild.id in allowed_ids]

    @staticmethod
    def _text_channel_id(guild: discord.Guild, value: Any) -> int:
        if value in (None, "", 0, "0"):
            return 0
        try:
            channel_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid text channel") from exc
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("invalid text channel")
        return channel.id

    @staticmethod
    def _voice_channel_id(guild: discord.Guild, value: Any) -> int:
        if value in (None, "", 0, "0"):
            return 0
        try:
            channel_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid voice channel") from exc
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            raise ValueError("invalid voice channel")
        return channel.id


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WebAdminCog(bot))
