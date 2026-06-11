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
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")   # legacy, no longer required
ESPN_LEAGUE = os.getenv("ESPN_LEAGUE", "fifa.world")   # ESPN soccer league slug
SYNC_LOOKAHEAD_DAYS = int(os.getenv("SYNC_LOOKAHEAD_DAYS", 30))
ANNOUNCE_GAP = int(os.getenv("ANNOUNCE_GAP_HOURS", 6)) * 3600  # min gap between announce pushes per user
SYNC_INTERVAL_MIN = int(os.getenv("SYNC_INTERVAL_MIN", 30))    # idle ESPN poll cadence
LIVE_SYNC_MIN = int(os.getenv("LIVE_SYNC_MIN", 3))             # cadence while a match is in play
VIP_PRICE_STARS = int(os.getenv("VIP_PRICE_STARS", 250))       # Telegram Stars / month
VIP_CHANNEL_INVITE = os.getenv("VIP_CHANNEL_INVITE", "")       # private VIP channel invite link
WC_END = os.getenv("WC_END", "2026-07-19")                     # real deadline = honest FOMO


def wc_days_left() -> int:
    import calendar
    end = calendar.timegm(time.strptime(WC_END, "%Y-%m-%d"))
    return max(0, (end - int(time.time())) // DAY)
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
    tag = "&sub_id_3=bot" if TRACKER_URL else ""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, url=f"{base}{sep}{param}={uid}{tag}")
    ]])


def kb_contact() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=T.BTN_VERIFY_CONTACT, request_contact=True)],
                  [KeyboardButton(text=T.BTN_VERIFY_SKIP)]],
        resize_keyboard=True, one_time_keyboard=True)


def kb_webapp(label: str | None = None) -> InlineKeyboardMarkup:
    """One button, many jobs: the label sells the OUTCOME of opening the
    hub in this exact context, never the feature ("Open Live Hub")."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label or T.BTN_OPEN_APP, url=WEBAPP_URL)
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
    referrer = None
    if payload and payload.startswith("ref_"):
        try:
            rid = int(payload[4:])
            if rid != msg.from_user.id:
                referrer = rid
        except ValueError:
            pass
        payload = "referral"     # keep /stats clean of per-user payloads
    await db.upsert_user(msg.from_user.id, msg.from_user.first_name or "mate",
                         source=payload)
    if referrer and not existing:      # first touch only, no self-invites
        await db.set_user(msg.from_user.id, referrer_id=referrer)

    # returning player with a team: don't reset them to the quiz
    if existing and existing["team"]:
        await msg.answer(T.WELCOME_BACK.format(team=existing["team"]),
                         reply_markup=kb_webapp(T.BTN_HUB_SCORES))
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


async def edit_or_send(cb: CallbackQuery, text: str, kb=None):
    """The greeting is a photo now: photos take edit_caption, text messages
    take edit_text. Any edit failure falls back to sending a new message."""
    try:
        if cb.message.photo:
            await cb.message.edit_caption(caption=text, reply_markup=kb)
        else:
            await cb.message.edit_text(text, reply_markup=kb)
    except Exception as e:
        log.warning("edit failed (%s), sending new message", e)
        await cb.message.answer(text, reply_markup=kb)


@r.callback_query(F.data.startswith("team:"))
async def team_chosen(cb: CallbackQuery):
    team = cb.data.split(":", 1)[1]
    await db.set_user(cb.from_user.id, team=team)
    await cb.answer(f"{team} 🏆")

    upcoming = await db.matches_where(
        "result IS NULL AND kickoff > ?", (int(time.time()),))
    if upcoming:
        m = upcoming[0]
        await edit_or_send(cb, T.TEAM_SAVED.format(team=team))
        await cb.message.answer(
            T.NEW_MATCH.format(t1=m["t1"], t2=m["t2"],
                               hours=max(1, (m["kickoff"] - int(time.time())) // HOUR)),
            reply_markup=kb_pick(m))
    else:
        await edit_or_send(cb, T.TEAM_SAVED_NO_MATCH.format(team=team),
                           kb=kb_webapp(T.BTN_HUB_SCHEDULE))


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

    if first:
        rid, gave = await db.credit_referral(cb.from_user.id)
        if rid:
            await safe_send(rid, T.REF_JOINED.format(
                name=cb.from_user.first_name or "Your friend",
                points="+1 league point" if gave else "no points (weekly cap)"))
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
            reply_markup=kb_webapp(T.BTN_HUB_TRACK))


@r.message(F.contact)
async def got_contact(msg: Message):
    if msg.contact.user_id != msg.from_user.id:   # forwarded someone else's card
        return
    await db.set_user(msg.from_user.id,
                      phone=msg.contact.phone_number, awaiting_email=0)
    await capi_lead_once(msg.from_user.id)
    await msg.answer(T.CONTACT_SAVED, reply_markup=ReplyKeyboardRemove())
    await msg.answer(T.HUB_AFTER_VERIFY, reply_markup=kb_webapp(T.BTN_HUB_TRACK))


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
        await msg.answer(T.EMAIL_SAVED, reply_markup=kb_webapp(T.BTN_HUB_TRACK))
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
            kb = kb_bridge(uid, T.BTN_CASH_READ) if not u["registered"] else None
            await safe_send(uid, T.RESULT_WIN.format(
                t1=m["t1"], t2=m["t2"], score=score,
                points=10, streak=u["streak"]), reply_markup=kb)
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


@r.message(Command("vip"))
async def vip_cmd(msg: Message):
    if not VIP_CHANNEL_INVITE:
        await msg.answer("VIP launches soon — stay tuned 👀")
        return
    u = await db.get_user(msg.from_user.id)
    if u and u["vip"]:
        await msg.answer(T.VIP_ALREADY.format(link=VIP_CHANNEL_INVITE),
                         disable_web_page_preview=True)
        return
    from aiogram.types import LabeledPrice
    await bot.send_invoice(
        msg.chat.id, title="VIP Predictor Pass — 30 days",
        description=T.VIP_INVOICE_DESC,
        payload="vip_month", currency="XTR", provider_token="",
        prices=[LabeledPrice(label="VIP Pass (30 days)",
                             amount=VIP_PRICE_STARS)])


@r.pre_checkout_query()
async def pre_checkout(q):
    await q.answer(ok=True)


@r.message(F.successful_payment)
async def vip_paid(msg: Message):
    await db.set_user(msg.from_user.id, vip=1)
    await msg.answer(T.VIP_WELCOME.format(link=VIP_CHANNEL_INVITE),
                     disable_web_page_preview=True)
    log.info("VIP purchase uid=%s", msg.from_user.id)


@r.message(Command("invite"))
async def invite_cmd(msg: Message):
    from urllib.parse import quote
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{msg.from_user.id}"
    share = ("https://t.me/share/url?url=" + quote(link) + "&text="
             + quote(T.INVITE_SHARE_TEXT))
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=T.BTN_INVITE_SHARE, url=share)]])
    await msg.answer(T.INVITE.format(link=link), reply_markup=kb,
                     disable_web_page_preview=True)


@r.message(Command("export"))
async def export_cmd(msg: Message):
    """Admin: dump all users as CSV — feeds ESP imports, Meta custom
    audiences and SMS retargeting. Contains personal data: handle per GDPR."""
    if not admin(msg):
        return
    import csv
    import io
    from aiogram.types import BufferedInputFile
    rows = await db.all_users()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["tg_id", "name", "team", "phone", "email", "source",
                "registered", "correct", "total", "week_correct",
                "week_total", "streak", "blocked", "created_at"])
    for u in rows:
        w.writerow([u["tg_id"], u["name"], u["team"], u["phone"], u["email"],
                    u["source"], u["registered"], u["correct"], u["total"],
                    u["week_correct"], u["week_total"], u["streak"],
                    u["blocked"],
                    time.strftime("%Y-%m-%d %H:%M",
                                  time.gmtime(u["created_at"] or 0))])
    data = buf.getvalue().encode("utf-8-sig")     # BOM so Excel opens UTF-8
    fname = f"leads_{time.strftime('%Y%m%d_%H%M')}.csv"
    await msg.answer_document(
        BufferedInputFile(data, filename=fname),
        caption=f"📦 {len(rows)} users · phones: "
                f"{sum(1 for u in rows if u['phone'])} · emails: "
                f"{sum(1 for u in rows if u['email'])}")


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


async def sync_fixtures() -> dict:
    """Pull World Cup fixtures/results from ESPN's free scoreboard API and
    auto-settle finished matches. No API key, no paid seasons.
    Returns a summary dict for logging / the /sync admin command."""
    start = time.strftime("%Y%m%d", time.gmtime(int(time.time()) - 2 * DAY))
    end = time.strftime("%Y%m%d",
                        time.gmtime(int(time.time()) + SYNC_LOOKAHEAD_DAYS * DAY))
    url = (f"https://site.api.espn.com/apis/site/v2/sports/soccer/"
           f"{ESPN_LEAGUE}/scoreboard")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params={"dates": f"{start}-{end}",
                                          "limit": 350},
                             timeout=20) as resp:
                if resp.status != 200:
                    return {"error": f"ESPN HTTP {resp.status}"}
                data = await resp.json()
    except Exception as e:
        log.warning("fixtures sync failed: %s", e)
        return {"error": str(e)}

    from datetime import datetime
    upserted = settled = 0
    for ev in data.get("events", []):
        try:
            comp = ev["competitions"][0]
            home = next(c for c in comp["competitors"]
                        if c.get("homeAway") == "home")
            away = next(c for c in comp["competitors"]
                        if c.get("homeAway") == "away")
            t1 = home["team"].get("displayName") or "TBD"
            t2 = away["team"].get("displayName") or "TBD"
            kickoff = int(datetime.fromisoformat(
                ev["date"].replace("Z", "+00:00")).timestamp())
            mid = await db.upsert_match_ext(str(ev["id"]), t1, t2, kickoff)
            upserted += 1
            odds = (comp.get("odds") or [{}])[0].get("details")
            if odds:                                # real ESPN line, when given
                await db.set_match_odds(mid, str(odds)[:32])
            state = ev.get("status", {}).get("type", {}).get("state", "pre")
            if state == "in":      # in-play: stream the live score to the hub
                hs, as_ = int(home.get("score", 0)), int(away.get("score", 0))
                new_score = f"{hs}:{as_}"
                prev = (await db.get_match(mid))["score"] or "0:0"
                await db.set_match_live(mid, new_score, live=1)
                if new_score != prev:   # goal: dopamine ping to pickers, once
                    local = await db.get_match(mid)
                    for p in await db.picks_for_match(mid):
                        if p["alerted"]:
                            continue
                        if p["pick"] == "1":
                            st = "ahead ✅" if hs > as_ else \
                                 ("level ⚖️" if hs == as_ else "behind 😬")
                        elif p["pick"] == "2":
                            st = "ahead ✅" if as_ > hs else \
                                 ("level ⚖️" if hs == as_ else "behind 😬")
                        else:
                            st = "ahead ✅" if hs == as_ else "behind 😬"
                        await safe_send(p["user_id"], T.GOAL_ALERT.format(
                            t1=local["t1"], t2=local["t2"], score=new_score,
                            status=st), reply_markup=kb_webapp(T.BTN_HUB_TRACK))
                        await db.mark_alerted(p["user_id"], mid)
                        await asyncio.sleep(0.05)
            if ev.get("status", {}).get("type", {}).get("completed"):
                local = await db.get_match(mid)
                if local and not local["result"]:
                    hs, as_ = int(home.get("score", 0)), int(away.get("score", 0))
                    if home.get("winner"):
                        res = "1"
                    elif away.get("winner"):
                        res = "2"
                    else:
                        res = "1" if hs > as_ else ("2" if as_ > hs else "X")
                    await db.set_match_live(mid, f"{hs}:{as_}", live=0)
                    await do_settle(mid, res, f"{hs}:{as_}")
                    settled += 1
                    log.info("auto-settled match %s (%s:%s)", mid, hs, as_)
        except Exception:
            log.exception("bad ESPN event row")
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
    (7 * DAY, 4, lambda u, n: T.CASCADE_7D.format(team=u["team"] or "Your team")
        + T.CASCADE_VIP_PS),
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

            # 0c) FOMO receipt: day 6 of the week, unregistered players with
            # 2+ correct calls get THEIR OWN numbers back as missed upside
            if now - week_start >= 6 * DAY and \
                    await db.meta_get("fomo_week", "") != str(week_start):
                for u in await db.all_users(
                        "registered=0 AND blocked=0 AND week_correct >= 2"):
                    await safe_send(u["tg_id"], T.FOMO_RECEIPT.format(
                        name=u["name"], n=u["week_correct"]),
                        reply_markup=kb_bridge(u["tg_id"], T.BTN_CASH_READ))
                    await asyncio.sleep(0.05)
                await db.meta_set("fomo_week", str(week_start))

            # 0b) prize proximity: midweek, tell ranks 11-20 how close the
            # money is — nothing retains like an almost-won prize
            if now - week_start >= 4 * DAY and \
                    await db.meta_get("proximity_week", "") != str(week_start):
                top = await db.weekly_top(20)
                if len(top) > 10:
                    threshold = top[9]["week_correct"]
                    for i, r in enumerate(top[10:], start=11):
                        gap = max(1, threshold - r["week_correct"] + 1)
                        await safe_send(r["tg_id"], T.PROXIMITY.format(
                            rank=i, gap=gap,
                            calls="call" if gap == 1 else "calls"))
                        await asyncio.sleep(0.05)
                await db.meta_set("proximity_week", str(week_start))

            # 0a) fixtures auto-sync (also auto-settles + win/loss pushes).
            # While a match is in play the hub promises LIVE scores — poll
            # ESPN every LIVE_SYNC_MIN instead of the idle cadence.
            in_play = await db.any_live() or await db.matches_where(
                "result IS NULL AND kickoff <= ? AND kickoff > ?",
                (now, now - 3 * HOUR))
            interval = (LIVE_SYNC_MIN if in_play else SYNC_INTERVAL_MIN) * 60
            if now - int(await db.meta_get("last_sync", "0")) >= interval:
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

            # 1) announce new matches (kickoff within 24h, not announced).
            # Pacing: soonest kickoff goes first, and each user gets at most
            # one announce push per ANNOUNCE_GAP — on 4-matches-a-day group
            # stage days the rest stay reachable via /schedule, the daily
            # digest and the mini app instead of flooding the chat.
            for m in await db.matches_where(
                    "announced=0 AND result IS NULL AND kickoff BETWEEN ? AND ? "
                    "ORDER BY kickoff", (now, now + DAY)):
                hours = max(1, (m["kickoff"] - now) // HOUR)
                for u in await db.all_users(
                        "team IS NOT NULL AND blocked=0 "
                        "AND last_announce_push < ?", (now - ANNOUNCE_GAP,)):
                    if u["team"] in (m["t1"], m["t2"]):
                        txt = T.NEW_MATCH_TEAM.format(
                            team=u["team"], t1=m["t1"], t2=m["t2"], hours=hours)
                    else:
                        txt = T.NEW_MATCH.format(t1=m["t1"], t2=m["t2"], hours=hours)
                    if m["odds"]:
                        txt += T.ODDS_LINE.format(odds=m["odds"])
                    if u["streak"] >= 2:
                        txt += T.STREAK_LINE.format(streak=u["streak"])
                    await safe_send_photo(
                        u["tg_id"], f"new_match_{m['id'] % 2 + 1}.png",
                        txt, reply_markup=kb_pick(m))
                    await db.set_user(u["tg_id"], last_announce_push=now)
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

            # 4b) deposit cascade: registered but no FTD — warmest pool in
            # the whole funnel, every touch here is worth $150 of payout
            for delay, step, key in ((20 * HOUR, 1, "DEP_CASCADE_20H"),
                                     (68 * HOUR, 2, "DEP_CASCADE_68H")):
                for u in await db.all_users(
                        "registered=1 AND deposited=0 AND blocked=0 "
                        "AND dep_cascade_step < ? AND registered_at > 0 "
                        "AND registered_at < ?", (step, now - delay)):
                    body = getattr(T, key).format(name=u["name"],
                                                  team=u["team"] or "Your team")
                    if step == 1 and wc_days_left() > 0:
                        body += T.WC_COUNTDOWN.format(days=wc_days_left())
                    await safe_send_photo(
                        u["tg_id"], "dep_win.png" if step == 2 else None,
                        body, reply_markup=kb_bridge(u["tg_id"], T.BRIDGE_BUTTON))
                    await db.set_user(u["tg_id"], dep_cascade_step=step)
                    await asyncio.sleep(0.05)

            # 5a) one-time Live Hub nudge: picks in chat, never opened the app
            for u in await db.all_users(
                    "last_pick_at > 0 AND last_webapp_at = 0 "
                    "AND webapp_nudged = 0 AND blocked = 0 "
                    "AND last_pick_at < ?", (now - 6 * HOUR,)):
                await safe_send(u["tg_id"],
                                T.WEBAPP_NUDGE.format(name=u["name"]),
                                reply_markup=kb_webapp(T.BTN_HUB_RANK))
                await db.set_user(u["tg_id"], webapp_nudged=1)

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
    await db.set_user(uid, last_webapp_at=int(time.time()))   # hub visit beacon
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
                  ("live" if (m["is_live"] or m["kickoff"] <= now)
                   else "upcoming"),
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
        rid, gave = await db.credit_referral(uid)
        if rid:
            await safe_send(rid, T.REF_JOINED.format(
                name=(await db.get_user(uid))["name"],
                points="+1 league point" if gave else "no points (weekly cap)"))
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
        await db.set_user(uid, registered=1, registered_at=int(time.time()))
        # truth-checked urgency: only claim a kickoff if one actually exists
        team, congrats = user["team"], None
        if team:
            nxt = await db.matches_where(
                "result IS NULL AND kickoff > ? AND (t1=? OR t2=?) "
                "ORDER BY kickoff LIMIT 1", (int(time.time()), team, team))
            if nxt:
                hours = max(1, (nxt[0]["kickoff"] - int(time.time())) // HOUR)
                congrats = T.REG_CONGRATS_MATCH.format(team=team, hours=hours)
        if not congrats:
            congrats = T.REG_CONGRATS_GENERIC
        await safe_send_photo(uid, "streak_bonus.png", congrats)
        await send_capi("CompleteRegistration", uid, user["source"])
        log.info("registration postback uid=%s source=%s", uid, user["source"])
    elif event in ("dep", "ftd", "deposit"):
        await db.set_user(uid, registered=1, deposited=1)   # dep implies reg
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
