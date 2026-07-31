"""Token-protected web dashboard for bot administration."""
from __future__ import annotations

import hmac
import logging
import os
import re
from typing import Any

import discord
from aiohttp import web
from discord.ext import commands

from cogs.config_store import ConfigStore
from cogs.rank_cog import DEFAULT_LEADERBOARD_POST_TIME
from cogs.rank_store import weekly_reset_anchor

log = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_COOKIE_NAME = "nothing_admin_token"


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
        return web.Response(text=_LOGIN_HTML, content_type="text/html")

    async def _login_submit(self, request: web.Request) -> web.Response:
        data = await request.post()
        token = str(data.get("token", ""))
        if not hmac.compare_digest(token, self._token):
            return web.Response(text=_LOGIN_HTML.replace("<!--ERROR-->", "<p class=\"error\">토큰이 올바르지 않습니다.</p>"), content_type="text/html", status=401)
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
        return web.Response(text=_INDEX_HTML, content_type="text/html")

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


_LOGIN_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nothing Bot Admin</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f7f9;color:#202225;font-family:Inter,Arial,sans-serif}
main{width:min(420px,calc(100vw - 32px));background:#fff;border:1px solid #d8dce2;border-radius:8px;padding:28px;box-shadow:0 12px 40px rgba(0,0,0,.08)}
h1{font-size:24px;margin:0 0 8px}.muted{color:#626b78;margin:0 0 22px;line-height:1.5}
label{display:block;font-size:13px;font-weight:700;margin-bottom:8px}input{box-sizing:border-box;width:100%;height:42px;border:1px solid #c7ccd4;border-radius:6px;padding:0 12px;font-size:15px}
button{margin-top:16px;width:100%;height:42px;border:0;border-radius:6px;background:#5865f2;color:#fff;font-weight:700;font-size:15px;cursor:pointer}.error{color:#b42318;font-weight:700}
</style></head><body><main><h1>Nothing Bot Admin</h1><p class="muted">관리자 토큰을 입력해 서버 설정 대시보드에 접속합니다.</p><!--ERROR--><form method="post" action="/login"><label for="token">Admin token</label><input id="token" name="token" type="password" autocomplete="current-password" autofocus><button>로그인</button></form></main></body></html>"""


_INDEX_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nothing Bot Admin</title><style>
:root{--bg:#f4f6f8;--panel:#fff;--line:#d8dee8;--line-strong:#c5ceda;--text:#16181d;--muted:#667085;--brand:#5865f2;--brand-soft:#eef0ff;--ok:#15803d;--ok-soft:#e8f7ee;--warn:#b7791f;--warn-soft:#fff7e6;--danger:#b42318;--gold:#b58105;--gold-soft:#fff7d6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,Arial,sans-serif;font-size:15px}
header{height:64px;display:flex;align-items:center;justify-content:space-between;padding:0 24px;background:#fff;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
h1{font-size:20px;margin:0}.shell{display:grid;grid-template-columns:300px 1fr;min-height:calc(100vh - 64px)}
aside{border-right:1px solid var(--line);background:#fff;padding:20px}.content{padding:24px;max-width:1180px;width:100%}
.section{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:18px;margin-bottom:16px}.section h2{font-size:17px;margin:0 0 14px;display:flex;align-items:center;justify-content:space-between;gap:10px}
.section .sub{font-size:12px;font-weight:600;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.field label{display:flex;align-items:center;justify-content:space-between;font-size:12px;font-weight:800;color:#4b5563;margin-bottom:7px}
select,input{width:100%;height:42px;border:1px solid var(--line-strong);border-radius:7px;background:#fff;padding:0 11px;font-size:14px;color:var(--text)}select:focus,input:focus{outline:2px solid rgba(88,101,242,.18);border-color:var(--brand)}
.toggle{display:flex;align-items:center;justify-content:space-between;gap:16px;border:1px solid var(--line);border-radius:8px;padding:13px 14px;margin-top:14px;background:#fbfcfe}
.status{display:inline-flex;align-items:center;gap:8px;border-radius:999px;padding:7px 10px;font-weight:800;font-size:13px;background:var(--warn-soft);color:var(--warn)}.status.on{background:var(--ok-soft);color:var(--ok)}.dot{width:8px;height:8px;border-radius:50%;background:currentColor}
button{height:40px;border:0;border-radius:7px;background:var(--brand);color:#fff;font-weight:800;padding:0 15px;cursor:pointer}button.secondary{background:#eef1f6;color:#1f2937}button.danger{background:#fff1f0;color:var(--danger);border:1px solid #f3b7b2}button:disabled{opacity:.55;cursor:not-allowed}
.actions{display:flex;gap:10px;flex-wrap:wrap}.muted{color:var(--muted);line-height:1.55}.list{display:grid;gap:8px}.guild{width:100%;text-align:left;background:#f1f4f8;color:var(--text);border:1px solid transparent;border-radius:7px;padding:10px 12px}.guild.active{background:var(--brand-soft);border-color:var(--brand);color:#2730a8;font-weight:800}
#message.ok{color:var(--ok);font-weight:800}#message.error{color:var(--danger);font-weight:800}
.lb-rows{display:grid;gap:8px}
.lb-row{display:grid;grid-template-columns:36px 1fr auto;align-items:center;gap:12px;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:#fbfcfe}
.lb-row .rank{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:999px;background:#eef1f6;font-weight:800;font-size:13px;color:#1f2937}
.lb-row.top1 .rank{background:var(--gold-soft);color:var(--gold)}
.lb-row.top2 .rank,.lb-row.top3 .rank{background:var(--brand-soft);color:#2730a8}
.lb-row .name{font-weight:700}.lb-row .meta{font-size:12px;color:var(--muted);margin-top:2px}
.lb-row .score{font-weight:800;font-variant-numeric:tabular-nums}
.history{display:grid;gap:10px}
.history-week{border:1px solid var(--line);border-radius:8px;background:#fbfcfe;overflow:hidden}
.history-week summary{cursor:pointer;list-style:none;padding:12px 14px;display:flex;align-items:center;justify-content:space-between;gap:10px;font-weight:700}
.history-week summary::-webkit-details-marker{display:none}
.history-week summary::after{content:'▾';color:var(--muted);font-size:12px;transition:transform .15s}
.history-week[open] summary::after{transform:rotate(180deg)}
.history-week .week-label{display:flex;align-items:center;gap:10px}
.history-week .top3{font-size:12px;color:var(--muted)}
.history-week .body{padding:0 14px 14px;display:grid;gap:6px}
.history-row{display:grid;grid-template-columns:28px 1fr auto;align-items:center;gap:10px;font-size:13px;padding:6px 8px;border-radius:6px}
.history-row .rank{font-weight:800;color:#4b5563}
.history-row .meta{color:var(--muted);font-size:11px}
@media(max-width:900px){.shell{grid-template-columns:1fr}aside{border-right:0;border-bottom:1px solid var(--line)}.grid{grid-template-columns:1fr}.content{padding:16px}}
</style></head><body><header><h1>Nothing Bot Admin</h1><button class="secondary" id="logout">로그아웃</button></header><div class="shell"><aside><div class="section"><h2>서버</h2><div id="guilds" class="list"></div></div><p class="muted">웹 대시보드는 ADMIN_WEB_TOKEN으로 보호됩니다. 토큰은 서버 환경변수에서 관리하세요.</p></aside><main class="content"><div class="section"><h2>운영 상태</h2><div id="status" class="muted">불러오는 중</div></div><div class="section"><h2>이번 주 리더보드 미리보기 <span class="sub" id="lb_anchor"></span></h2><div id="lb_rows" class="lb-rows"><p class="muted">불러오는 중…</p></div></div><div class="section"><h2>지난 주차 히스토리 <span class="sub" id="history_count"></span></h2><div id="history" class="history"><p class="muted">불러오는 중…</p></div></div><div class="section"><h2>TTS 설정</h2><div class="grid"><div class="field"><label>TTS 입력 채널</label><select id="tts_channel"></select></div><div class="field"><label>음성 출력 채널</label><select id="voice_channel"></select></div></div></div><div class="section"><h2>일일 리더보드</h2><div class="grid"><div class="field"><label>발송 채널</label><select id="leaderboard_channel"></select></div><div class="field"><label>발송 시각 KST</label><input id="post_time" placeholder="00:00" maxlength="5"></div></div><div style="height:14px"></div><label class="toggle"><input id="daily_enabled" type="checkbox" style="width:auto;height:auto"> 매일 자동 발송 사용</label></div><div class="section"><h2>작업</h2><div class="actions"><button id="save">설정 저장</button><button class="secondary" id="post">리더보드 즉시 발송</button><button class="secondary" id="refresh">새로고침</button><button class="danger" id="clear">리더보드 데이터 초기화</button></div><p id="message" class="muted"></p></div></main></div><script>
let state=null;let guildId=null;
const $=id=>document.getElementById(id);
function option(value,name){const o=document.createElement('option');o.value=value;o.textContent=name;return o}
function setMessage(text,type=''){const el=$('message');el.textContent=text;el.className='muted '+type}
function fillSelect(el,items,current,empty){el.innerHTML='';el.appendChild(option('0',empty));for(const item of items){el.appendChild(option(String(item.id),'# '+item.name))}el.value=String(current||0)}
function fmtScore(score){return (Math.max(0,score|0)/100).toFixed(2)+'점'}
function fmtDuration(secs){const s=Math.max(0,Math.floor(secs|0));const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),r=s%60;if(h)return `${h}시간 ${m}분`;if(m)return `${m}분 ${r}초`;return `${r}초`}
function fmtAnchorRange(anchor){if(!anchor)return '';const start=new Date(anchor);if(isNaN(start))return anchor;const end=new Date(start.getTime()+6*86400000);const f=d=>`${String(d.getMonth()+1).padStart(2,'0')}/${String(d.getDate()).padStart(2,'0')}`;return `${start.getFullYear()} ${f(start)}–${f(end)}`}
function rankIcon(i){return i===1?'🥇':i===2?'🥈':i===3?'🥉':String(i)}
function render(){const g=state.selected;if(!g){$('status').textContent='봇이 접속한 서버가 없습니다.';$('lb_rows').innerHTML='<p class="muted">서버를 선택하세요.</p>';$('history').innerHTML='<p class="muted">서버를 선택하세요.</p>';return}guildId=String(g.id);const cfg=g.config||{};$('guilds').innerHTML='';for(const item of state.guilds){const b=document.createElement('button');b.className='guild'+(String(item.id)===guildId?' active':'');b.textContent=item.name;b.onclick=()=>load(item.id);$('guilds').appendChild(b)}fillSelect($('tts_channel'),g.text_channels,cfg.tts_channel_id,'미설정');fillSelect($('leaderboard_channel'),g.text_channels,cfg.leaderboard_channel_id,'미설정');fillSelect($('voice_channel'),g.voice_channels,cfg.voice_channel_id,'미설정');$('post_time').value=cfg.leaderboard_post_time||'00:00';$('daily_enabled').checked=!!cfg.leaderboard_daily_enabled;const on=cfg.leaderboard_daily_enabled?'on':'';$('status').innerHTML=`<span class="status ${on}"><span class="dot"></span>일일 리더보드 ${cfg.leaderboard_daily_enabled?'켜짐':'꺼짐'}</span><p>TTS 입력: ${cfg.tts_channel_id?'#'+channelName(g.text_channels,cfg.tts_channel_id):'미설정'} · 음성 출력: ${cfg.voice_channel_id?channelName(g.voice_channels,cfg.voice_channel_id):'미설정'}</p>`}
function channelName(items,id){const found=items.find(x=>String(x.id)===String(id));return found?found.name:id}
function renderLeaderboard(data){const el=$('lb_rows');$('lb_anchor').textContent=data&&data.anchor?fmtAnchorRange(data.anchor):'';if(!data||!data.rows||!data.rows.length){el.innerHTML='<p class="muted">아직 집계된 활동이 없습니다.</p>';return}el.innerHTML='';data.rows.forEach((row,idx)=>{const i=idx+1;const div=document.createElement('div');div.className='lb-row'+(i<=3?` top${i}`:'');div.innerHTML=`<span class="rank">${rankIcon(i)}</span><div><div class="name"></div><div class="meta">음성 <b></b> · 메시지 <b></b>개</div></div><div class="score"></div>`;div.querySelector('.name').textContent=row.name;const metaB=div.querySelectorAll('.meta b');metaB[0].textContent=fmtDuration(row.voice_seconds);metaB[1].textContent=row.message_count;div.querySelector('.score').textContent=fmtScore(row.score);el.appendChild(div)})}
function renderHistory(data){const el=$('history');const weeks=(data&&data.weeks)||[];$('history_count').textContent=weeks.length?`${weeks.length}주 보관 중`:'';if(!weeks.length){el.innerHTML='<p class="muted">아직 보관된 주차 기록이 없습니다. 다음 주 금요일 자동 초기화부터 쌓입니다.</p>';return}el.innerHTML='';weeks.forEach((wk,idx)=>{const det=document.createElement('details');det.className='history-week';if(idx===0)det.open=true;const top3=wk.top.slice(0,3).map((r,i)=>`${rankIcon(i+1)} ${escapeHtml(r.name)}`).join(' · ');det.innerHTML=`<summary><span class="week-label">${fmtAnchorRange(wk.anchor)} <span class="top3">${top3||'기록 없음'}</span></span><span class="muted" style="font-size:12px">${wk.top.length}명</span></summary><div class="body"></div>`;const body=det.querySelector('.body');if(!wk.top.length){body.innerHTML='<p class="muted">활동이 없었던 주차입니다.</p>'}else{wk.top.forEach((row,i)=>{const r=document.createElement('div');r.className='history-row';r.innerHTML=`<span class="rank"></span><div><div class="name"></div><div class="meta">음성 <b></b> · 메시지 <b></b>개</div></div><div class="score"></div>`;r.querySelector('.rank').textContent=rankIcon(i+1);r.querySelector('.name').textContent=row.name;const mb=r.querySelectorAll('.meta b');mb[0].textContent=fmtDuration(row.voice_seconds);mb[1].textContent=row.message_count;r.querySelector('.score').textContent=fmtScore(row.score);body.appendChild(r)})}el.appendChild(det)})}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function load(id){const q=id?`?guild_id=${id}`:'';const r=await fetch('/api/state'+q);if(r.status===401){location.href='/login';return}state=await r.json();render();await loadActivity()}
async function loadActivity(){if(!guildId)return;try{const [lb,hist]=await Promise.all([fetch('/api/leaderboard?guild_id='+guildId),fetch('/api/leaderboard-history?guild_id='+guildId)]);if(lb.ok)renderLeaderboard(await lb.json());if(hist.ok)renderHistory(await hist.json())}catch(e){console.error(e)}}
async function save(){const body={guild_id:guildId,tts_channel_id:$('tts_channel').value,voice_channel_id:$('voice_channel').value,leaderboard_channel_id:$('leaderboard_channel').value,leaderboard_post_time:$('post_time').value,leaderboard_daily_enabled:$('daily_enabled').checked};const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await r.json();setMessage(r.ok?'설정을 저장했습니다.':(data.error||'저장 실패'),r.ok?'ok':'error');await load(guildId)}
async function post(){const r=await fetch('/api/post-leaderboard',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({guild_id:guildId})});const data=await r.json();setMessage(r.ok?'리더보드를 발송했습니다.':(data.error||'발송 실패'),r.ok?'ok':'error')}
async function clearLeaderboard(){const answer=prompt('선택한 서버의 리더보드 데이터를 초기화하려면 CLEAR를 입력하세요.');if(answer!=='CLEAR'){setMessage('초기화를 취소했습니다.');return}const r=await fetch('/api/clear-leaderboard',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({guild_id:guildId,confirm:'CLEAR'})});const data=await r.json();setMessage(r.ok?`리더보드 데이터를 초기화했습니다. (${data.cleared_users||0}명)`:(data.error||'초기화 실패'),r.ok?'ok':'error');await loadActivity()}
$('save').onclick=save;$('post').onclick=post;$('refresh').onclick=()=>load(guildId);$('clear').onclick=clearLeaderboard;$('logout').onclick=async()=>{await fetch('/logout',{method:'POST'});location.href='/login'};load();
</script></body></html>"""


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WebAdminCog(bot))
