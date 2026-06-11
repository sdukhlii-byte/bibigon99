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
import logging
import os
import re
import time

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
        return False


# ----------------------------------------------------------------------
# FLOOR 1 — /start + team segmentation
# ----------------------------------------------------------------------
@r.message(CommandStart())
async def start(msg: Message):
    await db.upsert_user(msg.from_user.id, msg.from_user.first_name or "mate")
    await msg.answer(T.START, reply_markup=kb_teams())


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
        await cb.message.answer(T.PREDICTION_SAVED.format(pick=label),
                                reply_markup=kb_contact())
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
        await msg.answer(T.EMAIL_SAVED, reply_markup=kb_webapp())
    else:
        await msg.answer(T.EMAIL_INVALID)


# ----------------------------------------------------------------------
# FLOOR 3 — bridge triggers fire from settle / scheduler (below)
# ----------------------------------------------------------------------
async def fire_bridge(uid: int, text: str, label: str):
    user = await db.get_user(uid)
    if not user or user["registered"]:
        return
    if await safe_send(uid, text, reply_markup=kb_bridge(uid, label)):
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
        kickoff = int(time.mktime(time.strptime(dt, "%Y-%m-%d %H:%M")))
        mid = await db.add_match(t1, t2, kickoff)
        await msg.answer(f"Match #{mid} added: {t1} vs {t2} @ {dt} UTC")
    except Exception as e:
        await msg.answer(f"Format: /addmatch Team1;Team2;YYYY-MM-DD HH:MM\n({e})")


@r.message(Command("settle"))
async def settle(msg: Message):
    """/settle <match_id> <1|X|2> <score>   e.g. /settle 3 1 2:1"""
    if not admin(msg):
        return
    try:
        _, mid, result, score = msg.text.split()
        m = await db.get_match(int(mid))
        rows = await db.settle_match(int(mid), result, score)
        n_conv = await db.converted_count()
        for uid, ok in rows:
            u = await db.get_user(uid)
            if ok:
                await safe_send(uid, T.RESULT_WIN.format(
                    t1=m["t1"], t2=m["t2"], score=score,
                    points=10, streak=u["streak"]))
                if u["streak"] >= 3 and not u["registered"]:
                    await fire_bridge(uid, T.TRIGGER_STREAK.format(
                        name=u["name"], streak=u["streak"]), T.BRIDGE_BUTTON)
            else:
                await safe_send(uid, T.RESULT_LOSS.format(
                    t1=m["t1"], t2=m["t2"], score=score))
                if u["total"] >= 4 and u["correct"] / u["total"] <= 0.25 \
                        and not u["registered"]:
                    await fire_bridge(uid, T.TRIGGER_COLDSTREAK.format(
                        correct=u["correct"], total=u["total"]), T.BRIDGE_BUTTON)
        await msg.answer(f"Settled #{mid}: {len(rows)} predictions scored. "
                         f"Converted so far: {n_conv}")
    except Exception as e:
        await msg.answer(f"Format: /settle <id> <1|X|2> <score>\n({e})")


@r.message(Command("stats"))
async def stats(msg: Message):
    if not admin(msg):
        return
    s = await db.stats()
    await msg.answer("\n".join(f"<b>{k}</b>: {v}" for k, v in s.items()))


@r.message(Command("broadcast"))
async def broadcast(msg: Message):
    if not admin(msg):
        return
    text = msg.text.split(" ", 1)[1]
    users = await db.all_users()
    sent = 0
    for u in users:
        sent += await safe_send(u["tg_id"], text)
        await asyncio.sleep(0.05)            # ~20 msg/s, under TG limits
    await msg.answer(f"Broadcast: {sent}/{len(users)} delivered")


# ----------------------------------------------------------------------
# SCHEDULER — announcements, matchday bridges, drip cascade
# ----------------------------------------------------------------------
CASCADE = [           # (delay since bridge_clicked_at, step index, text fn)
    (2 * HOUR, 1, lambda u, n: T.CASCADE_2H),
    (24 * HOUR, 2, lambda u, n: T.CASCADE_24H.format(
        name=u["name"], n_converted=max(n, 12),
        accuracy=int(100 * u["correct"] / u["total"]) if u["total"] else 60)),
    (5 * DAY, 3, lambda u, n: T.CASCADE_5D.format(name=u["name"])),
    (7 * DAY, 4, lambda u, n: T.CASCADE_7D.format(team=u["team"] or "Your team")),
]


async def scheduler():
    while True:
        try:
            now = int(time.time())

            # 1) announce new matches (kickoff within 24h, not announced)
            for m in await db.matches_where(
                    "announced=0 AND result IS NULL AND kickoff BETWEEN ? AND ?",
                    (now, now + DAY)):
                hours = max(1, (m["kickoff"] - now) // HOUR)
                for u in await db.all_users("team IS NOT NULL"):
                    if u["team"] in (m["t1"], m["t2"]):
                        txt = T.NEW_MATCH_TEAM.format(
                            team=u["team"], t1=m["t1"], t2=m["t2"], hours=hours)
                    else:
                        txt = T.NEW_MATCH.format(t1=m["t1"], t2=m["t2"], hours=hours)
                    await safe_send(u["tg_id"], txt, reply_markup=kb_pick(m))
                    await asyncio.sleep(0.05)
                await db.mark_announced(m["id"])

            # 2) matchday bridge: user's team kicks off in <3h, cap 1 per 48h
            for m in await db.matches_where(
                    "result IS NULL AND kickoff BETWEEN ? AND ?",
                    (now, now + 3 * HOUR)):
                hours = max(1, (m["kickoff"] - now) // HOUR)
                for u in await db.all_users(
                        "registered=0 AND team IN (?,?) AND last_matchday_push < ?",
                        (m["t1"], m["t2"], now - 2 * DAY)):
                    await fire_bridge(u["tg_id"], T.TRIGGER_MATCHDAY.format(
                        team=u["team"], hours=hours), T.MATCHDAY_BUTTON)
                    await db.set_user(u["tg_id"], last_matchday_push=now)

            # 3) drip cascade for bridge-shown, unregistered users
            n_conv = await db.converted_count()
            for u in await db.all_users(
                    "registered=0 AND bridge_clicked_at IS NOT NULL"):
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
                    "last_pick_at > 0 AND last_pick_at < ? AND rehooked=0",
                    (now - 3 * DAY,)):
                kb = kb_pick(upcoming[0]) if upcoming else kb_webapp()
                await safe_send(u["tg_id"],
                                T.REHOOK.format(name=u["name"]), reply_markup=kb)
                await db.set_user(u["tg_id"], rehooked=1)

            # 5) 4h reminder for users without a team
            for u in await db.all_users(
                    "team IS NULL AND reminded_team=0 AND created_at < ?",
                    (now - 4 * HOUR,)):
                await safe_send(u["tg_id"],
                                T.REMIND_NO_TEAM.format(name=u["name"]),
                                reply_markup=kb_teams())
                await db.set_user(u["tg_id"], reminded_team=1)

        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(300)             # every 5 min


# ----------------------------------------------------------------------
# POSTBACK SERVER — affiliate / site calls this on registration
# GET /postback?uid=<tg_id>&event=reg&secret=<POSTBACK_SECRET>
# ----------------------------------------------------------------------
async def postback(request: web.Request):
    if request.query.get("secret") != POSTBACK_SECRET:
        return web.Response(status=403, text="forbidden")
    uid = int(request.query.get("uid", 0))
    event = request.query.get("event", "reg")
    user = await db.get_user(uid)
    if not user:
        return web.Response(status=404, text="unknown uid")
    if event == "reg" and not user["registered"]:
        await db.set_user(uid, registered=1)
        await safe_send(uid, T.REG_CONGRATS.format(team=user["team"] or "Your team"))
        log.info("registration postback uid=%s", uid)
    return web.Response(text="ok")


async def health(_):
    return web.Response(text="ok")


async def run_web():
    app = web.Application()
    app.router.add_get("/postback", postback)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
    await site.start()
    log.info("postback server on :%s", os.getenv("PORT", 8080))


async def main():
    await db.init()
    await run_web()
    asyncio.create_task(scheduler())
    log.info("bot polling started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
