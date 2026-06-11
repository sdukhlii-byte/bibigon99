"""
World Cup Predictor League — full funnel bot.
Stack: aiogram 3.x + aiosqlite + aiohttp postback server, single process.

ENV:
  BOT_TOKEN        - Telegram bot token
  ADMIN_IDS        - comma-separated tg ids, e.g. "123,456"
  PRELANDING_URL   - https://your-site.com/wc  (uid is appended as ?uid=)
  WEBAPP_URL       - https://t.me/YourBot/app or mini-app https URL
  POSTBACK_SECRET  - shared secret for /postback
  PORT             - http port for postback server (Railway sets it)
"""
import asyncio
import hashlib
import logging
import os
import re
import time

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)

import db
import texts as T

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("funnel")

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
PRELANDING_URL = os.getenv("PRELANDING_URL", "https://example.com")
TRACKER_URL = os.getenv("TRACKER_URL", "")   # Keitaro campaign URL; if set, bridge goes through it
WEBAPP_URL = os.getenv("WEBAPP_URL", PRELANDING_URL)
POSTBACK_SECRET = os.getenv("POSTBACK_SECRET", "change-me")
META_PIXEL_ID = os.getenv("META_PIXEL_ID", "")        # Meta CAPI: server-side events
META_CAPI_TOKEN = os.getenv("META_CAPI_TOKEN", "")
PRIVACY_URL = os.getenv("PRIVACY_URL", "")            # GDPR notice link (EU traffic)
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")   # api-sports.io (API-Football v3) key
FOOTBALL_LEAGUE_ID = os.getenv("FOOTBALL_LEAGUE_ID", "1")   # 1 = FIFA World Cup
FOOTBALL_SEASON = os.getenv("FOOTBALL_SEASON", "2026")
NEWS_RSS_URL = os.getenv("NEWS_RSS_URL",
                         "https://feeds.bbci.co.uk/sport/football/rss.xml")
DIGEST_HOUR_UTC = int(os.getenv("DIGEST_HOUR_UTC", 9))

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")
HOUR, DAY = 3600, 86400

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
r = Router()
dp.include_router(r)


# ----------------------------------------------------------------------
# keyboards
# ----------------------------------------------------------------------
def kb_teams() -> InlineKeyboardMarkup:
    rows, row = [], []
    for t in T.TEAMS:
        row.append(InlineKeyboardButton(text=t, callback_data=f"team:{t}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_pick(m) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=T.BTN_PICK_1.format(t1=m["t1"]),
                             callback_data=f"pick:{m['id']}:1"),
        InlineKeyboardButton(text=T.BTN_PICK_X,
                             callback_data=f"pick:{m['id']}:X"),
        InlineKeyboardButton(text=T.BTN_PICK_2.format(t2=m["t2"]),
                             callback_data=f"pick:{m['id']}:2"),
    ]])


def kb_bridge(uid: int, label: str) -> InlineKeyboardMarkup:
    """Keitaro chain: TRACKER_URL?sub_id_2={tg_id} -> partner clickid ->
    network postback -> Keitaro outgoing postback hits /postback?uid={sub_id_2}.
    Falls back to direct prelanding ?uid= if no tracker is set."""
    base = TRACKER_URL or PRELANDING_URL
    sep = "&" if "?" in base else "?"
    param = "sub_id_2" if TRACKER_URL else "uid"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, url=f"{base}{sep}{param}={uid}")
    ]])


def kb_contact() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=T.BTN_VERIFY_CONTACT, request_contact=True)],
                  [KeyboardButton(text=T.BTN_VERIFY_SKIP)]],
        resize_keyboard=True, one_time_keyboard=True)


def kb_webapp() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=T.BTN_OPEN_APP, url=WEBAPP_URL)
    ]])


async def safe_send(uid: int, text: str, **kw):
    try:
        await bot.send_message(uid, text, **kw)
        return True
    except Exception as e:           # blocked / deactivated — never crash loop
        log.warning("send to %s failed: %s", uid, e)
        if "blocked" in str(e).lower() or "deactivated" in str(e).lower():
            await db.set_user(uid, blocked=1)   # drop from future pushes
        return False


# ----------------------------------------------------------------------
# MEDIA — funnel-stage creatives (media/*.png). Missing file -> text only.
# ----------------------------------------------------------------------
from aiogram.types import FSInputFile

MEDIA_DIR = os.getenv("MEDIA_DIR", "media")


def media(name: str):
    path = os.path.join(MEDIA_DIR, name)
    return path if os.path.exists(path) else None


async def safe_send_photo(uid: int, img: str | None, caption: str, **kw):
    """Photo + caption; falls back to plain text if the image is missing,
    the caption exceeds Telegram's 1024-char photo limit, or sending fails."""
    path = media(img) if img else None
    if not path or len(caption) > 1024:
        return await safe_send(uid, caption, **kw)
    try:
        await bot.send_photo(uid, FSInputFile(path), caption=caption, **kw)
        return True
    except Exception as e:
        log.warning("photo to %s failed (%s), falling back to text", uid, e)
        if "blocked" in str(e).lower() or "deactivated" in str(e).lower():
            await db.set_user(uid, blocked=1)
            return False
        return await safe_send(uid, caption, **kw)


# ----------------------------------------------------------------------
# META CAPI — server-side conversions (Lead / CompleteRegistration / Purchase)
# ----------------------------------------------------------------------
async def send_capi(event: str, uid: int, source: str | None = None):
    """Fire a server event to Meta. external_id = sha256(tg_id) for matching;
    event_id dedupes against the browser pixel on the prelanding."""
    if not (META_PIXEL_ID and META_CAPI_TOKEN):
        return
    payload = {"data": [{
        "event_name": event,
        "event_time": int(time.time()),
        "event_id": f"{event.lower()}_{uid}",
        "action_source": "chat",
        "user_data": {
            "external_id": hashlib.sha256(str(uid).encode()).hexdigest()},
        "custom_data": {"source": source or "organic"},
    }]}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                    f"https://graph.facebook.com/v19.0/{META_PIXEL_ID}/events",
                    params={"access_token": META_CAPI_TOKEN},
                    json=payload, timeout=15) as resp:
                if resp.status != 200:
                    log.warning("CAPI %s for %s -> %s: %s",
                                event, uid, resp.status, await resp.text())
    except Exception as e:
        log.warning("CAPI %s for %s failed: %s", event, uid, e)


async def capi_lead_once(uid: int):
    """Lead = first verified contact (phone or email). Fires exactly once."""
    u = await db.get_user(uid)
    if u and not u["capi_lead_sent"]:
        await db.set_user(uid, capi_lead_sent=1)
        await send_capi("Lead", uid, u["source"])


# ----------------------------------------------------------------------
# FLOOR 1 — /start + team segmentation
# ----------------------------------------------------------------------
@r.message(CommandStart())
async def start(msg: Message):
    # deep-link payload = ad attribution: t.me/bot?start=ig_wc_lal_001
    parts = (msg.text or "").split(maxsplit=1)
    payload = parts[1].strip()[:64] if len(parts) > 1 else None

    existing = await db.get_user(msg.from_user.id)
    await db.upsert_user(msg.from_user.id, msg.from_user.first_name or "mate",
                         source=payload)

    # returning player with a team: don't reset them to the quiz
    if existing and existing["team"]:
        await msg.answer(T.WELCOME_BACK.format(team=existing["team"]),
                         reply_markup=kb_webapp())
        return

    # ad scent: greeting matches the traffic source (ig_* = Instagram/Meta)
    greet = T.START_IG if payload and payload.startswith("ig") else T.START
    if media("start.png"):
        try:
            await msg.answer_photo(FSInputFile(media("start.png")),
                                   caption=greet, reply_markup=kb_teams())
            return
        except Exception as e:
            log.warning("start photo failed: %s", e)
    await msg.answer(greet, reply_markup=kb_teams())


@r.callback_query(F.data.startswith("team:"))
async def team_chosen(cb: CallbackQuery):
    team = cb.data.split(":", 1)[1]
    await db.set_user(cb.from_user.id, team=team)
    await cb.answer(f"{team} 🏆")

    upcoming = await db.matches_where(
        "result IS NULL AND kickoff > ?", (int(time.time()),))
    if upcoming:
        m = upcoming[0]
        await cb.message.edit_text(T.TEAM_SAVED.format(team=team))
        await cb.message.answer(
            T.NEW_MATCH.format(t1=m["t1"], t2=m["t2"],
                               hours=max(1, (m["kickoff"] - int(time.time())) // HOUR)),
            reply_markup=kb_pick(m))
    else:
        await cb.message.edit_text(T.TEAM_SAVED_NO_MATCH.format(team=team),
                                   reply_markup=kb_webapp())


# ----------------------------------------------------------------------
# FLOOR 2 — predictions + email capture
# ----------------------------------------------------------------------
@r.callback_query(F.data.startswith("pick:"))
async def pick(cb: CallbackQuery):
    _, mid, choice = cb.data.split(":")
    first = await db.save_prediction(cb.from_user.id, int(mid), choice)
    m = await db.get_match(int(mid))
    label = {"1": m["t1"], "X": "Draw", "2": m["t2"]}[choice]
    await cb.answer(f"Locked: {label} 🎯")

    user = await db.get_user(cb.from_user.id)
    if first and not user["phone"] and not user["email"]:
        ask = T.PREDICTION_SAVED.format(pick=label)
        if PRIVACY_URL:
            ask += T.PRIVACY_FOOTNOTE.format(url=PRIVACY_URL)
        await cb.message.answer(ask, reply_markup=kb_contact(),
                                disable_web_page_preview=True)
    else:
        await cb.message.answer(
            f"Pick locked: <b>{label}</b> 🎯 I'll ping you at the final whistle.",
            reply_markup=kb_webapp())


@r.message(F.contact)
async def got_contact(msg: Message):
    if msg.contact.user_id != msg.from_user.id:   # forwarded someone else's card
        return
    await db.set_user(msg.from_user.id,
                      phone=msg.contact.phone_number, awaiting_email=0)
    await capi_lead_once(msg.from_user.id)
    await msg.answer(T.CONTACT_SAVED, reply_markup=ReplyKeyboardRemove())
    await msg.answer("Live hub 👇", reply_markup=kb_webapp())


@r.message(F.text == T.BTN_VERIFY_SKIP)
async def contact_skipped(msg: Message):
    await db.set_user(msg.from_user.id, awaiting_email=1)
    await msg.answer(T.ASK_EMAIL_FALLBACK, reply_markup=ReplyKeyboardRemove())


@r.message(Command("skip"))
async def skip_email(msg: Message):
    await db.set_user(msg.from_user.id, awaiting_email=0)
    await msg.answer(T.EMAIL_SKIPPED)


@r.message(Command("verify"))
async def verify(msg: Message):
    await db.set_user(msg.from_user.id, awaiting_email=1)
    await msg.answer("Drop your email below 👇")


@r.message(F.text, ~F.text.startswith("/"))
async def maybe_email(msg: Message):
    user = await db.get_user(msg.from_user.id)
    if not user or not user["awaiting_email"]:
        return
    if EMAIL_RE.match(msg.text.strip()):
        await db.set_user(msg.from_user.id,
                          email=msg.text.strip().lower(), awaiting_email=0)
        await capi_lead_once(msg.from_user.id)
        await msg.answer(T.EMAIL_SAVED, reply_markup=kb_webapp())
    else:
        await msg.answer(T.EMAIL_INVALID)


# ----------------------------------------------------------------------
# FLOOR 3 — bridge triggers fire from settle / scheduler (below)
# ----------------------------------------------------------------------
async def fire_bridge(uid: int, text: str, label: str, img: str | None = None):
    user = await db.get_user(uid)
    if not user or user["registered"]:
        return
    if await safe_send_photo(uid, img, text, reply_markup=kb_bridge(uid, label)):
        # bridge shown -> cascade clock starts on click; we approximate with show time
        if not user["bridge_clicked_at"]:
            await db.set_user(uid, bridge_clicked_at=int(time.time()))


# ----------------------------------------------------------------------
# ADMIN
# ----------------------------------------------------------------------
def admin(msg: Message) -> bool:
    return msg.from_user.id in ADMIN_IDS


@r.message(Command("addmatch"))
async def addmatch(msg: Message):
    """/addmatch Brazil;Argentina;2026-06-15 18:00   (UTC)"""
    if not admin(msg):
        return
    try:
        _, payload = msg.text.split(" ", 1)
        t1, t2, dt = [x.strip() for x in payload.split(";")]
        import calendar
        kickoff = calendar.timegm(time.strptime(dt, "%Y-%m-%d %H:%M"))
        mid = await db.add_match(t1, t2, kickoff)
        await msg.answer(f"Match #{mid} added: {t1} vs {t2} @ {dt} UTC")
    except Exception as e:
        await msg.answer(f"Format: /addmatch Team1;Team2;YYYY-MM-DD HH:MM\n({e})")


async def do_settle(mid: int, result: str, score: str) -> int:
    """Score predictions, push results, fire bridge triggers. Returns count."""
    m = await db.get_match(mid)
    rows = await db.settle_match(mid, result, score)
    for uid, ok in rows:
        u = await db.get_user(uid)
        if ok:
            await safe_send(uid, T.RESULT_WIN.format(
                t1=m["t1"], t2=m["t2"], score=score,
                points=10, streak=u["streak"]))
            if u["streak"] >= 3 and not u["registered"]:
                await fire_bridge(uid, T.TRIGGER_STREAK.format(
                    name=u["name"], streak=u["streak"]), T.BRIDGE_BUTTON,
                    img="streak_bonus.png")
        else:
            await safe_send(uid, T.RESULT_LOSS.format(
                t1=m["t1"], t2=m["t2"], score=score))
            if u["total"] >= 4 and u["correct"] / u["total"] <= 0.25 \
                    and not u["registered"]:
                await fire_bridge(uid, T.TRIGGER_COLDSTREAK.format(
                    correct=u["correct"], total=u["total"]), T.BRIDGE_BUTTON)
        await asyncio.sleep(0.05)
    return len(rows)


@r.message(Command("settle"))
async def settle(msg: Message):
    """/settle <match_id> <1|X|2> <score>   e.g. /settle 3 1 2:1"""
    if not admin(msg):
        return
    try:
        _, mid, result, score = msg.text.split()
        n = await do_settle(int(mid), result, score)
        await msg.answer(f"Settled #{mid}: {n} predictions scored. "
                         f"Converted so far: {await db.converted_count()}")
    except Exception as e:
        await msg.answer(f"Format: /settle <id> <1|X|2> <score>\n({e})")


@r.message(Command("stats"))
async def stats(msg: Message):
    if not admin(msg):
        return
    s = await db.stats()
    lines = [f"<b>{k}</b>: {v}" for k, v in s.items()]
    by_src = await db.stats_by_source()
    if by_src:
        lines.append("\n<b>By source</b> (users / regs):")
        lines += [f"• {src}: {n} / {regs or 0}" for src, n, regs in by_src]
    await msg.answer("\n".join(lines))


@r.message(Command("broadcast"))
async def broadcast(msg: Message):
    if not admin(msg):
        return
    parts = msg.text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("Usage: /broadcast <text>")
        return
    text = parts[1]
    users = await db.all_users("blocked=0")
    sent = 0
    for u in users:
        sent += await safe_send(u["tg_id"], text)
        await asyncio.sleep(0.05)            # ~20 msg/s, under TG limits
    await msg.answer(f"Broadcast: {sent}/{len(users)} delivered")


# ----------------------------------------------------------------------
# CONTENT LAYER — schedule, news, personal stats
# ----------------------------------------------------------------------

import xml.etree.ElementTree as ET

import aiohttp


@r.message(Command("schedule"))
async def schedule_cmd(msg: Message):
    now = int(time.time())
    rows = await db.matches_where(
        "result IS NULL AND kickoff > ? ORDER BY kickoff LIMIT 10", (now,))
    if not rows:
        await msg.answer(T.SCHEDULE_EMPTY)
        return
    picks = await db.user_picks(msg.from_user.id)
    lines = [T.SCHEDULE_HEADER]
    for m in rows:
        when = time.strftime("%d %b %H:%M", time.gmtime(m["kickoff"]))
        picked = "  ✅" if m["id"] in picks else ""
        lines.append(T.SCHEDULE_ROW.format(
            when=when, t1=m["t1"], t2=m["t2"], picked=picked))
    lines.append(T.SCHEDULE_FOOTER)
    await msg.answer("\n".join(lines), reply_markup=kb_pick(rows[0]))


async def fetch_news(n: int = 3):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(NEWS_RSS_URL, timeout=15) as resp:
                text = await resp.text()
        items = ET.fromstring(text).findall(".//item")[:n]
        return [(i.findtext("title", ""), i.findtext("link", ""))
                for i in items]
    except Exception as e:
        log.warning("news fetch failed: %s", e)
        return []


def format_news(items) -> str:
    body = "\n".join(f"• <a href=\"{link}\">{title}</a>"
                     for title, link in items)
    return T.NEWS_HEADER + body + T.NEWS_FOOTER


@r.message(Command("news"))
async def news_cmd(msg: Message):
    items = await fetch_news(5)
    await msg.answer(format_news(items) if items else T.NEWS_EMPTY,
                     disable_web_page_preview=True)


@r.message(Command("mystats"))
async def mystats_cmd(msg: Message):
    u = await db.get_user(msg.from_user.id)
    if not u or not u["total"]:
        await msg.answer(T.MYSTATS_EMPTY)
        return
    await msg.answer(T.MYSTATS.format(
        team=u["team"] or "—", total=u["total"], correct=u["correct"],
        week_correct=u["week_correct"], week_total=u["week_total"],
        accuracy=round(100 * u["correct"] / u["total"]),
        streak=u["streak"], rank=await db.user_rank(msg.from_user.id)))


FINISHED_STATUSES = {"FT", "AET", "PEN"}   # api-sports fixture status codes


async def sync_fixtures() -> dict:
    """Pull World Cup fixtures/results from API-Sports v3 and auto-settle.
    Returns a summary dict for logging / the /sync admin command."""
    if not FOOTBALL_API_KEY:
        return {"error": "FOOTBALL_API_KEY is not set"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                    "https://v3.football.api-sports.io/fixtures",
                    params={"league": FOOTBALL_LEAGUE_ID,
                            "season": FOOTBALL_SEASON},
                    headers={"x-apisports-key": FOOTBALL_API_KEY},
                    timeout=20) as resp:
                data = await resp.json()
    except Exception as e:
        log.warning("fixtures sync failed: %s", e)
        return {"error": str(e)}
    if data.get("errors"):
        log.warning("api-sports errors: %s", data["errors"])
        return {"error": str(data["errors"])}

    from datetime import datetime
    upserted = settled = 0
    for row in data.get("response", []):
        try:
            fx, teams, goals = row["fixture"], row["teams"], row["goals"]
            t1 = teams["home"]["name"] or "TBD"
            t2 = teams["away"]["name"] or "TBD"
            kickoff = int(datetime.fromisoformat(fx["date"]).timestamp())
            mid = await db.upsert_match_ext(str(fx["id"]), t1, t2, kickoff)
            upserted += 1
            if fx["status"]["short"] in FINISHED_STATUSES:
                local = await db.get_match(mid)
                if local and not local["result"] \
                        and goals.get("home") is not None:
                    if teams["home"].get("winner"):
                        res = "1"
                    elif teams["away"].get("winner"):
                        res = "2"
                    else:
                        res = "X"
                    await do_settle(mid, res,
                                    f"{goals['home']}:{goals['away']}")
                    settled += 1
                    log.info("auto-settled match %s", mid)
        except Exception:
            log.exception("bad fixture row")
    return {"fixtures": upserted, "settled": settled}


@r.message(Command("sync"))
async def sync_cmd(msg: Message):
    """Admin: force a fixtures sync and see what the API answered."""
    if not admin(msg):
        return
    res = await sync_fixtures()
    await db.meta_set("last_sync", str(int(time.time())))
    if "error" in res:
        await msg.answer(f"⚠️ Sync error: {res['error']}")
    else:
        await msg.answer(f"✅ Synced {res['fixtures']} fixtures, "
                         f"auto-settled {res['settled']}.")


# ----------------------------------------------------------------------
# SCHEDULER — announcements, matchday bridges, drip cascade
# ----------------------------------------------------------------------
CASCADE = [           # (delay since bridge_clicked_at, step index, text fn)
    (2 * HOUR, 1, lambda u, n: T.CASCADE_2H),
    (24 * HOUR, 2, lambda u, n: (
        T.CASCADE_24H.format(
            name=u["name"], n_converted=n,
            accuracy=int(100 * u["correct"] / u["total"]) if u["total"] else 60)
        if n >= 5 else            # real social proof only — never invent numbers
        T.CASCADE_24H_EARLY.format(
            name=u["name"],
            accuracy=int(100 * u["correct"] / u["total"]) if u["total"] else 60))),
    (5 * DAY, 3, lambda u, n: T.CASCADE_5D.format(name=u["name"])),
    (7 * DAY, 4, lambda u, n: T.CASCADE_7D.format(team=u["team"] or "Your team")),
]


async def scheduler():
    while True:
        try:
            now = int(time.time())

            # 0) weekly league reset (Mon 00:00 UTC): announce podium, zero week
            week_start = int(await db.meta_get("week_start", "0"))
            if not week_start:
                # align to last Monday 00:00 UTC
                g = time.gmtime(now)
                week_start = now - g.tm_wday * DAY - g.tm_hour * HOUR \
                    - g.tm_min * 60 - g.tm_sec
                await db.meta_set("week_start", str(week_start))
            elif now - week_start >= 7 * DAY:
                top = await db.weekly_top(10)
                if top:
                    podium = "\n".join(
                        f"{i+1}. {r['name']} — {r['week_correct']}/{r['week_total']}"
                        for i, r in enumerate(top))
                    text = T.WEEKLY_PODIUM.format(podium=podium)
                    for u in await db.all_users("team IS NOT NULL AND blocked=0"):
                        await safe_send_photo(u["tg_id"], "podium.png", text)
                        await asyncio.sleep(0.05)
                await db.weekly_reset()
                await db.meta_set("week_start", str(week_start + 7 * DAY))

            # 0a) fixtures auto-sync every 30 min (also auto-settles)
            if now - int(await db.meta_get("last_sync", "0")) >= 1800:
                await sync_fixtures()
                await db.meta_set("last_sync", str(now))

            # 0b) daily news digest at DIGEST_HOUR_UTC
            today = time.strftime("%Y-%m-%d", time.gmtime(now))
            if time.gmtime(now).tm_hour == DIGEST_HOUR_UTC and \
                    await db.meta_get("digest_date") != today:
                items = await fetch_news(3)
                if items:
                    text = format_news(items)
                    for u in await db.all_users("team IS NOT NULL AND blocked=0"):
                        await safe_send(u["tg_id"], text,
                                        disable_web_page_preview=True)
                        await asyncio.sleep(0.05)
                await db.meta_set("digest_date", today)

            # 1) announce new matches (kickoff within 24h, not announced)
            for m in await db.matches_where(
                    "announced=0 AND result IS NULL AND kickoff BETWEEN ? AND ?",
                    (now, now + DAY)):
                hours = max(1, (m["kickoff"] - now) // HOUR)
                for u in await db.all_users("team IS NOT NULL AND blocked=0"):
                    if u["team"] in (m["t1"], m["t2"]):
                        txt = T.NEW_MATCH_TEAM.format(
                            team=u["team"], t1=m["t1"], t2=m["t2"], hours=hours)
                    else:
                        txt = T.NEW_MATCH.format(t1=m["t1"], t2=m["t2"], hours=hours)
                    await safe_send_photo(
                        u["tg_id"], f"new_match_{m['id'] % 2 + 1}.png",
                        txt, reply_markup=kb_pick(m))
                    await asyncio.sleep(0.05)
                await db.mark_announced(m["id"])

            # 2) matchday bridge: user's team kicks off in <3h, cap 1 per 48h
            for m in await db.matches_where(
                    "result IS NULL AND kickoff BETWEEN ? AND ?",
                    (now, now + 3 * HOUR)):
                hours = max(1, (m["kickoff"] - now) // HOUR)
                for u in await db.all_users(
                        "registered=0 AND blocked=0 AND team IN (?,?) AND last_matchday_push < ?",
                        (m["t1"], m["t2"], now - 2 * DAY)):
                    await fire_bridge(u["tg_id"], T.TRIGGER_MATCHDAY.format(
                        team=u["team"], hours=hours), T.MATCHDAY_BUTTON,
                        img="matchday.png")
                    await db.set_user(u["tg_id"], last_matchday_push=now)

            # 3) drip cascade for bridge-shown, unregistered users
            n_conv = await db.converted_count()
            for u in await db.all_users(
                    "registered=0 AND blocked=0 AND bridge_clicked_at IS NOT NULL"):
                for delay, step, fn in CASCADE:
                    if u["cascade_step"] < step and \
                            now - u["bridge_clicked_at"] >= delay:
                        kb = None if step == 4 else kb_bridge(
                            u["tg_id"], T.BRIDGE_BUTTON)
                        await safe_send(u["tg_id"], fn(u, n_conv), reply_markup=kb)
                        await db.set_user(u["tg_id"], cascade_step=step)
                        break    # one step per tick

            # 4) re-hook quiet predictors: 3+ days since last pick
            upcoming = await db.matches_where(
                "result IS NULL AND kickoff > ?", (now,))
            for u in await db.all_users(
                    "last_pick_at > 0 AND last_pick_at < ? AND rehooked=0 AND blocked=0",
                    (now - 3 * DAY,)):
                kb = kb_pick(upcoming[0]) if upcoming else kb_webapp()
                await safe_send_photo(u["tg_id"], "rehook.png",
                                      T.REHOOK.format(name=u["name"]),
                                      reply_markup=kb)
                await db.set_user(u["tg_id"], rehooked=1)

            # 5) 4h reminder for users without a team
            for u in await db.all_users(
                    "team IS NULL AND reminded_team=0 AND blocked=0 AND created_at < ?",
                    (now - 4 * HOUR,)):
                await safe_send(u["tg_id"],
                                T.REMIND_NO_TEAM.format(name=u["name"]),
                                reply_markup=kb_teams())
                await db.set_user(u["tg_id"], reminded_team=1)

        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(300)             # every 5 min


# ----------------------------------------------------------------------
# MINI APP REST API — consumed by the Lovable web app
# Auth: X-Telegram-Init-Data header, verified per Telegram WebApp spec
# ----------------------------------------------------------------------
import hashlib
import hmac
import json
from urllib.parse import parse_qsl


def verify_init_data(init_data: str):
    """Returns tg user id if signature is valid and fresh, else None."""
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = data.pop("hash")
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(),
                          hashlib.sha256).digest()
        calc = hmac.new(secret, check_string.encode(),
                        hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received_hash):
            return None
        # replay protection: initData older than 24h is rejected
        if int(time.time()) - int(data.get("auth_date", 0)) > DAY:
            return None
        return json.loads(data["user"])["id"]
    except Exception:
        return None


@web.middleware
async def cors(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = \
        "Content-Type, X-Telegram-Init-Data"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


def api_uid(request) -> int | None:
    return verify_init_data(request.headers.get("X-Telegram-Init-Data", ""))


async def api_me(request):
    uid = api_uid(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    u = await db.get_user(uid)
    if not u:
        return web.json_response({"error": "start the bot first"}, status=404)
    return web.json_response({
        "name": u["name"], "team": u["team"],
        "correct": u["correct"], "total": u["total"], "streak": u["streak"],
        "week_correct": u["week_correct"], "week_total": u["week_total"],
        "accuracy": round(100 * u["correct"] / u["total"]) if u["total"] else None,
        "registered": bool(u["registered"]),
    })


async def api_matches(request):
    uid = api_uid(request)
    picks = await db.user_picks(uid) if uid else {}
    now = int(time.time())
    rows = await db.matches_where(
        "kickoff > ? OR result IS NOT NULL", (now - 2 * DAY,))
    return web.json_response([{
        "id": m["id"], "t1": m["t1"], "t2": m["t2"],
        "kickoff": m["kickoff"], "result": m["result"], "score": m["score"],
        "status": "settled" if m["result"] else
                  ("live" if m["kickoff"] <= now else "upcoming"),
        "my_pick": picks.get(m["id"]),
    } for m in rows])


async def api_predict(request):
    uid = api_uid(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await request.json()
    mid, pick = int(body.get("match_id", 0)), body.get("pick")
    m = await db.get_match(mid)
    if not m or m["result"] or m["kickoff"] <= int(time.time()) \
            or pick not in ("1", "X", "2"):
        return web.json_response({"error": "match closed"}, status=400)
    first = await db.save_prediction(uid, mid, pick)
    # the funnel must not fork: a first pick made in the mini app gets the
    # same verification ask as an in-bot pick — otherwise the whole web-app
    # path silently skips phone/email capture
    if first:
        u = await db.get_user(uid)
        if u and not u["phone"] and not u["email"]:
            label = {"1": m["t1"], "X": "Draw", "2": m["t2"]}[pick]
            ask = T.PREDICTION_SAVED.format(pick=label)
            if PRIVACY_URL:
                ask += T.PRIVACY_FOOTNOTE.format(url=PRIVACY_URL)
            await safe_send(uid, ask, reply_markup=kb_contact(),
                            disable_web_page_preview=True)
    return web.json_response({"ok": True, "first_prediction": first})


async def api_leaderboard(request):
    rows = await db.leaderboard(20)
    return web.json_response([{
        "rank": i + 1, "name": r["name"], "team": r["team"],
        "correct": r["correct"], "total": r["total"], "streak": r["streak"],
    } for i, r in enumerate(rows)])


# ----------------------------------------------------------------------
# POSTBACK SERVER — affiliate / site calls this on registration
# GET /postback?uid=<tg_id>&event=reg&secret=<POSTBACK_SECRET>
# ----------------------------------------------------------------------
async def postback(request: web.Request):
    if request.query.get("secret") != POSTBACK_SECRET:
        return web.Response(status=403, text="forbidden")
    try:
        uid = int(request.query.get("uid", ""))
    except ValueError:
        return web.Response(status=400, text="bad uid")
    event = request.query.get("event", "reg")
    user = await db.get_user(uid)
    if not user:
        return web.Response(status=404, text="unknown uid")
    if event == "reg" and not user["registered"]:
        await db.set_user(uid, registered=1)
        await safe_send_photo(uid, "streak_bonus.png",
                              T.REG_CONGRATS.format(team=user["team"] or "Your team"))
        await send_capi("CompleteRegistration", uid, user["source"])
        log.info("registration postback uid=%s source=%s", uid, user["source"])
    elif event in ("dep", "ftd", "deposit"):
        await db.set_user(uid, registered=1)            # dep implies reg
        await safe_send_photo(uid, "dep_win.png", T.DEP_CONGRATS)
        await send_capi("Purchase", uid, user["source"])
        log.info("deposit postback uid=%s source=%s", uid, user["source"])
    return web.Response(text="ok")


async def health(_):
    return web.Response(text="ok")


async def run_web():
    app = web.Application(middlewares=[cors])
    app.router.add_get("/postback", postback)
    app.router.add_get("/health", health)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/matches", api_matches)
    app.router.add_post("/api/predict", api_predict)
    app.router.add_get("/api/leaderboard", api_leaderboard)
    app.router.add_route("OPTIONS", "/{tail:.*}", lambda r: web.Response())
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
    await site.start()
    log.info("postback server on :%s", os.getenv("PORT", 8080))


async def main():
    await db.init()
    await run_web()
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="schedule", description="📅 Match calendar"),
        BotCommand(command="news", description="⚽ Football headlines"),
        BotCommand(command="mystats", description="📊 My league card"),
        BotCommand(command="verify", description="✅ Verify for prizes"),
    ])
    asyncio.create_task(scheduler())
    log.info("bot polling started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
