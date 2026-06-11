"""SQLite data layer. Swap to Postgres later by replacing aiosqlite calls."""
import os
import time
import aiosqlite

# Point DB_PATH at a mounted Railway volume (e.g. /data/funnel.db) —
# otherwise every redeploy wipes users, picks and ad attribution.
DB_PATH = os.getenv("DB_PATH", "funnel.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id        INTEGER PRIMARY KEY,
    name         TEXT,
    team         TEXT,
    email        TEXT,
    phone        TEXT,
    awaiting_email INTEGER DEFAULT 0,
    last_pick_at INTEGER DEFAULT 0,          -- ts of latest prediction
    rehooked     INTEGER DEFAULT 0,          -- quiet-user re-hook sent
    streak       INTEGER DEFAULT 0,
    correct      INTEGER DEFAULT 0,
    total        INTEGER DEFAULT 0,
    registered   INTEGER DEFAULT 0,          -- postback flips this
    bridge_clicked_at INTEGER,               -- ts when user tapped a bridge CTA
    cascade_step INTEGER DEFAULT 0,          -- 0..4 (2h,24h,5d,7d done)
    last_matchday_push INTEGER DEFAULT 0,    -- ts, frequency cap
    created_at   INTEGER,
    reminded_team INTEGER DEFAULT 0,
    source       TEXT,                       -- /start deep-link payload (ad attribution)
    blocked      INTEGER DEFAULT 0,          -- user blocked the bot; skip in pushes
    week_correct INTEGER DEFAULT 0,          -- weekly league (prize pool window)
    week_total   INTEGER DEFAULT 0,
    capi_lead_sent INTEGER DEFAULT 0         -- Meta CAPI Lead dedup
);
CREATE TABLE IF NOT EXISTS matches (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ext_id    TEXT UNIQUE,                   -- football-data.org match id
    t1        TEXT, t2 TEXT,
    kickoff   INTEGER,                       -- unix ts UTC
    result    TEXT,                          -- '1' | 'X' | '2' | NULL
    score     TEXT,                          -- e.g. '2:1'
    announced INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
CREATE TABLE IF NOT EXISTS predictions (
    user_id   INTEGER,
    match_id  INTEGER,
    pick      TEXT,
    correct   INTEGER,                       -- NULL until settled
    PRIMARY KEY (user_id, match_id)
);
"""


async def init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        # safe migrations for DBs created before these columns existed
        for col, ddl in [("phone", "TEXT"), ("last_pick_at", "INTEGER DEFAULT 0"),
                         ("rehooked", "INTEGER DEFAULT 0"),
                         ("source", "TEXT"), ("blocked", "INTEGER DEFAULT 0"),
                         ("week_correct", "INTEGER DEFAULT 0"),
                         ("week_total", "INTEGER DEFAULT 0"),
                         ("capi_lead_sent", "INTEGER DEFAULT 0")]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
            except Exception:
                pass
        try:
            await db.execute("ALTER TABLE matches ADD COLUMN ext_id TEXT")
        except Exception:
            pass
        await db.commit()


async def meta_get(k: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT v FROM meta WHERE k=?", (k,))
        row = await cur.fetchone()
        return row[0] if row else default


async def meta_set(k: str, v: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO meta (k,v) VALUES (?,?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
        await db.commit()


async def upsert_match_ext(ext_id: str, t1: str, t2: str, kickoff: int) -> int:
    """Insert fixture from the football API or refresh its kickoff time."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM matches WHERE ext_id=?", (ext_id,))
        row = await cur.fetchone()
        if row:
            await db.execute("UPDATE matches SET kickoff=? WHERE id=?",
                             (kickoff, row[0]))
            await db.commit()
            return row[0]
        cur = await db.execute(
            "INSERT INTO matches (ext_id,t1,t2,kickoff) VALUES (?,?,?,?)",
            (ext_id, t1, t2, kickoff))
        await db.commit()
        return cur.lastrowid


async def user_rank(uid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT week_correct FROM users WHERE tg_id=?", (uid,))
        row = await cur.fetchone()
        if not row:
            return None
        cur = await db.execute(
            "SELECT COUNT(*)+1 FROM users WHERE week_total>0 AND week_correct>?",
            (row[0],))
        (rank,) = await cur.fetchone()
        return rank


async def upsert_user(tg_id: int, name: str, source: str | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (tg_id, name, created_at, source) VALUES (?,?,?,?) "
            "ON CONFLICT(tg_id) DO UPDATE SET name=excluded.name, blocked=0, "
            "source=COALESCE(users.source, excluded.source)",   # first touch wins
            (tg_id, name, int(time.time()), source),
        )
        await db.commit()


async def set_user(tg_id: int, **fields):
    keys = ", ".join(f"{k}=?" for k in fields)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {keys} WHERE tg_id=?",
                         (*fields.values(), tg_id))
        await db.commit()


async def get_user(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
        return await cur.fetchone()


async def all_users(where: str = "1=1", params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM users WHERE {where}", params)
        return await cur.fetchall()


async def add_match(t1: str, t2: str, kickoff: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO matches (t1,t2,kickoff) VALUES (?,?,?)",
            (t1, t2, kickoff))
        await db.commit()
        return cur.lastrowid


async def get_match(mid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM matches WHERE id=?", (mid,))
        return await cur.fetchone()


async def matches_where(where: str, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM matches WHERE {where}", params)
        return await cur.fetchall()


async def mark_announced(mid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE matches SET announced=1 WHERE id=?", (mid,))
        await db.commit()


async def save_prediction(uid: int, mid: int, pick: str) -> bool:
    """Returns True if it was the user's FIRST ever prediction."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM predictions WHERE user_id=?", (uid,))
        (count,) = await cur.fetchone()
        await db.execute(
            "INSERT OR REPLACE INTO predictions (user_id,match_id,pick) "
            "VALUES (?,?,?)", (uid, mid, pick))
        await db.execute(
            "UPDATE users SET last_pick_at=?, rehooked=0 WHERE tg_id=?",
            (int(time.time()), uid))
        await db.commit()
        return count == 0


async def settle_match(mid: int, result: str, score: str):
    """Mark result, score predictions, update user stats. Returns affected rows."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("UPDATE matches SET result=?, score=? WHERE id=?",
                         (result, score, mid))
        cur = await db.execute(
            "SELECT * FROM predictions WHERE match_id=?", (mid,))
        preds = await cur.fetchall()
        out = []
        for p in preds:
            ok = 1 if p["pick"] == result else 0
            await db.execute(
                "UPDATE predictions SET correct=? WHERE user_id=? AND match_id=?",
                (ok, p["user_id"], mid))
            if ok:
                await db.execute(
                    "UPDATE users SET streak=streak+1, correct=correct+1, "
                    "total=total+1, week_correct=week_correct+1, "
                    "week_total=week_total+1 WHERE tg_id=?", (p["user_id"],))
            else:
                await db.execute(
                    "UPDATE users SET streak=0, total=total+1, "
                    "week_total=week_total+1 WHERE tg_id=?",
                    (p["user_id"],))
            out.append((p["user_id"], ok))
        await db.commit()
        return out


async def converted_count() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users WHERE registered=1")
        (n,) = await cur.fetchone()
        return n


async def leaderboard(limit: int = 20):
    """Weekly league — matches the 'Top 10 every week' prize promise."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT name, team, week_correct AS correct, week_total AS total, "
            "streak FROM users WHERE week_total > 0 "
            "ORDER BY week_correct DESC, week_total ASC LIMIT ?", (limit,))
        return await cur.fetchall()


async def weekly_top(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT tg_id, name, week_correct, week_total FROM users "
            "WHERE week_total > 0 ORDER BY week_correct DESC, week_total ASC "
            "LIMIT ?", (limit,))
        return await cur.fetchall()


async def weekly_reset():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET week_correct=0, week_total=0")
        await db.commit()


async def user_picks(uid: int):
    """match_id -> pick for one user."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT match_id, pick FROM predictions WHERE user_id=?", (uid,))
        return {mid: pick for mid, pick in await cur.fetchall()}


async def stats():
    async with aiosqlite.connect(DB_PATH) as db:
        q = {
            "users": "SELECT COUNT(*) FROM users",
            "picked_team": "SELECT COUNT(*) FROM users WHERE team IS NOT NULL",
            "predictors": "SELECT COUNT(DISTINCT user_id) FROM predictions",
            "emails": "SELECT COUNT(*) FROM users WHERE email IS NOT NULL",
            "phones": "SELECT COUNT(*) FROM users WHERE phone IS NOT NULL",
            "bridge_clicks": "SELECT COUNT(*) FROM users WHERE bridge_clicked_at IS NOT NULL",
            "registered": "SELECT COUNT(*) FROM users WHERE registered=1",
        }
        out = {}
        for k, sql in q.items():
            cur = await db.execute(sql)
            (out[k],) = await cur.fetchone()
        return out


async def stats_by_source():
    """source -> (users, registered). Ad-set level read straight in the bot."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COALESCE(source,'organic'), COUNT(*), SUM(registered) "
            "FROM users GROUP BY 1 ORDER BY 2 DESC")
        return await cur.fetchall()
