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

log = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_COOKIE_NAME = "nothing_admin_token"


def _web_enabled() -> bool:
    value = os.getenv("ADMIN_WEB_ENABLED")
    if value is not None:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(os.getenv("ADMIN_WEB_TOKEN"))


def _web_host() -> str:
    host = os.getenv("ADMIN_WEB_HOST")
    if host:
        return host
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PUBLIC_DOMAIN"):
        return "0.0.0.0"
    return "127.0.0.1"


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


def _channel_payload(channel: discord.abc.GuildChannel) -> dict[str, int | str]:
    return {"id": channel.id, "name": channel.name}


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
                web.post("/api/config", self._api_config),
                web.post("/api/post-leaderboard", self._api_post_leaderboard),
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
        guild = self._guild(guild_id) if guild_id else (self.bot.guilds[0] if self.bot.guilds else None)
        guilds = [{"id": item.id, "name": item.name} for item in self.bot.guilds]
        if guild is None:
            return web.json_response({"guilds": guilds, "selected": None})

        cfg = await self.store.get(guild.id)
        return web.json_response(
            {
                "guilds": guilds,
                "selected": {
                    "id": guild.id,
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

    def _guild(self, guild_id: Any) -> discord.Guild | None:
        try:
            target_id = int(guild_id)
        except (TypeError, ValueError):
            return None
        return self.bot.get_guild(target_id)

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
:root{--bg:#f5f6f8;--panel:#fff;--line:#d9dde5;--text:#202225;--muted:#68707c;--brand:#5865f2;--ok:#168a45;--warn:#b7791f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,Arial,sans-serif}
header{height:64px;display:flex;align-items:center;justify-content:space-between;padding:0 24px;background:#fff;border-bottom:1px solid var(--line)}
h1{font-size:20px;margin:0}.shell{display:grid;grid-template-columns:280px 1fr;min-height:calc(100vh - 64px)}
aside{border-right:1px solid var(--line);background:#fff;padding:18px}.content{padding:24px;max-width:1180px}
.section{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:18px;margin-bottom:16px}.section h2{font-size:16px;margin:0 0 14px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.field label{display:block;font-size:12px;font-weight:700;color:var(--muted);margin-bottom:6px}
select,input{width:100%;height:40px;border:1px solid #c8ced8;border-radius:6px;background:#fff;padding:0 10px;font-size:14px}.toggle{display:flex;align-items:center;gap:10px}
.status{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line);border-radius:999px;padding:6px 10px;font-weight:700}.dot{width:9px;height:9px;border-radius:50%;background:var(--warn)}.dot.on{background:var(--ok)}
button{height:38px;border:0;border-radius:6px;background:var(--brand);color:#fff;font-weight:700;padding:0 14px;cursor:pointer}button.secondary{background:#eef0f5;color:#202225}
.actions{display:flex;gap:10px;flex-wrap:wrap}.muted{color:var(--muted);line-height:1.5}.list{display:grid;gap:8px}.guild{width:100%;text-align:left;background:#f1f3f7;color:#202225}.guild.active{background:var(--brand);color:#fff}
@media(max-width:800px){.shell{grid-template-columns:1fr}aside{border-right:0;border-bottom:1px solid var(--line)}.grid{grid-template-columns:1fr}.content{padding:16px}}
</style></head><body><header><h1>Nothing Bot Admin</h1><button class="secondary" id="logout">로그아웃</button></header><div class="shell"><aside><div class="section"><h2>서버</h2><div id="guilds" class="list"></div></div><p class="muted">웹 대시보드는 ADMIN_WEB_TOKEN으로 보호됩니다. 토큰은 서버 환경변수에서 관리하세요.</p></aside><main class="content"><div class="section"><h2>운영 상태</h2><div id="status" class="muted">불러오는 중</div></div><div class="section"><h2>TTS 설정</h2><div class="grid"><div class="field"><label>TTS 입력 채널</label><select id="tts_channel"></select></div><div class="field"><label>음성 출력 채널</label><select id="voice_channel"></select></div></div></div><div class="section"><h2>일일 리더보드</h2><div class="grid"><div class="field"><label>발송 채널</label><select id="leaderboard_channel"></select></div><div class="field"><label>발송 시각 KST</label><input id="post_time" placeholder="23:59" maxlength="5"></div></div><div style="height:14px"></div><label class="toggle"><input id="daily_enabled" type="checkbox" style="width:auto;height:auto"> 매일 자동 발송 사용</label></div><div class="section"><h2>작업</h2><div class="actions"><button id="save">설정 저장</button><button class="secondary" id="post">리더보드 즉시 발송</button><button class="secondary" id="refresh">새로고침</button></div><p id="message" class="muted"></p></div></main></div><script>
let state=null;let guildId=null;
const $=id=>document.getElementById(id);
function option(value,name){const o=document.createElement('option');o.value=value;o.textContent=name;return o}
function fillSelect(el,items,current,empty){el.innerHTML='';el.appendChild(option('0',empty));for(const item of items){el.appendChild(option(String(item.id),'# '+item.name))}el.value=String(current||0)}
function render(){const g=state.selected;if(!g){$('status').textContent='봇이 접속한 서버가 없습니다.';return}guildId=g.id;const cfg=g.config||{};$('guilds').innerHTML='';for(const item of state.guilds){const b=document.createElement('button');b.className='guild'+(item.id===g.id?' active':'');b.textContent=item.name;b.onclick=()=>load(item.id);$('guilds').appendChild(b)}fillSelect($('tts_channel'),g.text_channels,cfg.tts_channel_id,'미설정');fillSelect($('leaderboard_channel'),g.text_channels,cfg.leaderboard_channel_id,'미설정');fillSelect($('voice_channel'),g.voice_channels,cfg.voice_channel_id,'미설정');$('post_time').value=cfg.leaderboard_post_time||'23:59';$('daily_enabled').checked=!!cfg.leaderboard_daily_enabled;const on=cfg.leaderboard_daily_enabled?'on':'';$('status').innerHTML=`<span class="status"><span class="dot ${on}"></span>일일 리더보드 ${cfg.leaderboard_daily_enabled?'켜짐':'꺼짐'}</span><p>TTS 입력: ${cfg.tts_channel_id?'#'+channelName(g.text_channels,cfg.tts_channel_id):'미설정'} · 음성 출력: ${cfg.voice_channel_id?channelName(g.voice_channels,cfg.voice_channel_id):'미설정'}</p>`}
function channelName(items,id){const found=items.find(x=>String(x.id)===String(id));return found?found.name:id}
async function load(id){const q=id?`?guild_id=${id}`:'';const r=await fetch('/api/state'+q);if(r.status===401){location.href='/login';return}state=await r.json();render()}
async function save(){const body={guild_id:guildId,tts_channel_id:$('tts_channel').value,voice_channel_id:$('voice_channel').value,leaderboard_channel_id:$('leaderboard_channel').value,leaderboard_post_time:$('post_time').value,leaderboard_daily_enabled:$('daily_enabled').checked};const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await r.json();$('message').textContent=r.ok?'설정을 저장했습니다.':(data.error||'저장 실패');await load(guildId)}
async function post(){const r=await fetch('/api/post-leaderboard',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({guild_id:guildId})});const data=await r.json();$('message').textContent=r.ok?'리더보드를 발송했습니다.':(data.error||'발송 실패')}
$('save').onclick=save;$('post').onclick=post;$('refresh').onclick=()=>load(guildId);$('logout').onclick=async()=>{await fetch('/logout',{method:'POST'});location.href='/login'};load();
</script></body></html>"""


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WebAdminCog(bot))
