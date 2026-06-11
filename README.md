# World Cup Predictor League — Funnel Bot

Full funnel in one process: aiogram 3 bot + drip scheduler + postback HTTP server.

```
TG Ads → /start → team pick → first prediction (in-bot)
  → email capture (prize-gated) → result pushes every match
  → bridge triggers (streak / cold streak / matchday)
  → prelanding (?uid=tg_id) → registration postback
  → drip cascade for non-converters (+2h, +24h, +5d, +7d)
```

## Deploy (Railway)

1. New project → deploy from repo. Start command: `python main.py`
2. Set variables:

| Var | Example |
|---|---|
| `BOT_TOKEN` | from @BotFather |
| `ADMIN_IDS` | `123456789,987654321` |
| `PRELANDING_URL` | `https://yoursite.com/wc` (fallback if no tracker) |
| `TRACKER_URL` | `https://track.yourdomain.com/CAMPAIGN_ID` (Keitaro campaign) |
| `WEBAPP_URL` | `https://t.me/YourBot/app` |
| `POSTBACK_SECRET` | any long random string |
| `DB_PATH` | `/data/funnel.db` — **attach a Railway volume and point here**, or every redeploy wipes the database |
| `META_PIXEL_ID` | Meta Pixel id — enables server-side CAPI events (Lead / CompleteRegistration / Purchase) |
| `META_CAPI_TOKEN` | Meta Conversions API access token |
| `PRIVACY_URL` | privacy policy link — appends a GDPR consent line to the verification ask (EU traffic) |
| `ESPN_LEAGUE` | optional, default `fifa.world` — ESPN league slug, sync is keyless and free |
| `SYNC_LOOKAHEAD_DAYS` | optional, default `30` — how far ahead to pull fixtures |
| `NEWS_RSS_URL` | optional, defaults to BBC Football RSS |
| `DIGEST_HOUR_UTC` | optional, daily news digest hour UTC (default 9) |

3. Enable public networking → note the domain for postbacks.

`PORT` is injected by Railway automatically.

> SQLite file lives on the container disk. For production attach a Railway
> volume, or swap `db.py` to Postgres (every function is isolated — it's a
> 30-minute swap).

## Registration postback — Keitaro chain (recommended)

When `TRACKER_URL` is set, every bridge button opens
`TRACKER_URL?sub_id_2={tg_id}` — each user carries their Telegram id
through the whole chain:

1. **Bot → Keitaro**: click lands on the campaign with `sub_id_2={tg_id}`.
2. **Keitaro → affiliate network**: pass clickid in the offer URL as usual.
3. **Network → Keitaro**: registration postback by clickid (your current setup).
4. **Keitaro → bot**: campaign → *Postbacks* → add outgoing postback,
   trigger on lead/registration status:

```
https://<your-railway-domain>/postback?uid={sub_id_2}&event=reg&secret=<POSTBACK_SECRET>
```

On hit, the bot stops that user's cascade and sends the deposit-nudge message.
Bonus: Keitaro reports by `sub_id_2` let you join registration quality with
league behavior (streaks, accuracy) per user.

**Fallback (network strips clickid):** keep cascades — they hard-cap at 4
messages and return users to the league loop — and reconcile registrations
daily from the affiliate panel export.

## Direct postback (no tracker)

Wire the site/affiliate platform to call on successful registration:

```
GET https://<your-domain>/postback?uid={tg_id}&event=reg&secret=<POSTBACK_SECRET>
```

For first deposits add a second outgoing postback with `event=dep` — the bot
sends the bonus-activated message and fires a CAPI `Purchase` event.

## Ad attribution + Meta CAPI

- Every ad link carries its campaign: `t.me/YourBot?start=ig_wc_lal_001`.
  The payload is stored as the user's `source` (first touch wins); `/stats`
  shows a **By source: users / regs** breakdown per ad set.
- Payloads starting with `ig` switch the greeting to the Instagram-scented
  variant (`START_IG` in texts.py) — ad scent matches the first message.
- With `META_PIXEL_ID` + `META_CAPI_TOKEN` set, the bot sends server events:
  `Lead` on phone/email verification, `CompleteRegistration` on reg postback,
  `Purchase` on dep postback. Matching via hashed `external_id`; `event_id`
  dedupes against the browser pixel on the prelanding.

## Weekly league

Copy promises a weekly 500 USDT pool — the code now delivers a weekly window:
`week_correct/week_total` drive the leaderboard and `/mystats` rank; every
Monday 00:00 UTC the scheduler broadcasts the Top 10 podium and resets the
weekly counters (all-time stats stay). **Actually pay the winners and post
proof — that broadcast is your strongest social-proof asset.**

`uid` arrives at the prelanding as a query param (`?uid=...`) — pass it through
the reg form as sub_id / clickid. On postback the bot:
stops the cascade, sends the congrats + deposit-nudge message.

## Daily ops (admin commands in the bot)

**Matches run themselves**: fixtures sync from ESPN's free scoreboard API
(no key needed) every 30 min, announcements go out within 24h of kickoff,
finished matches auto-settle — results, streaks, and bridge triggers fire
without you. Admin commands remain as manual override:

| Command | What it does |
|---|---|
| `/addmatch Brazil;Argentina;2026-06-15 18:00` | manually add a match (UTC) — manual fallback |
| `/settle 3 1 2:1` | manually settle match #3 (auto-settle covers API matches) | |
| `/stats` | funnel snapshot: users → team picked → predictors → emails → phones → bridge clicks → registered |
| `/broadcast <text>` | send to everyone (rate-limited) |

User commands (in the bot menu): `/schedule` — next 10 matches with pick
status and instant pick buttons; `/news` — top football headlines on demand;
`/mystats` — personal league card with rank; `/verify` — retry prize
verification. A daily news digest goes to all users at `DIGEST_HOUR_UTC`
(content touches that keep the selling pushes below 1-in-4).

## Funnel logic baked in

- **Verification after FIRST prediction** (peak engagement): one-tap
  `request_contact` button collects the user's **phone number** (Telegram-verified);
  skippers get an email fallback, `/skip` allowed, `/verify` to retry. Phones
  unlock SMS/WhatsApp retargeting and Meta custom audiences.
- **Bridge triggers**: streak ≥3 (hot), accuracy ≤25% after 4+ picks (cashback frame), team kicks off in <3h (capped at 1 per 48h).
- **Cascade** runs only for users who saw a bridge and didn't register; stops instantly on postback. Last step returns the user to the league loop instead of saying goodbye.
- **Re-hook**: predictors silent for 3+ days get one playful nudge back into
  the league ("your picks went quiet — scared of the leaderboard?"); flag
  resets on their next pick.
- All copy lives in `texts.py` — edit without touching logic.

## What to watch (kill zombies)

`/stats` ratios to keep: start→team 80%+, team→prediction 40%+,
prediction→email 25%+, bridge→prelanding clicks 15%+, prelanding→reg 20%+.
Fix the single worst ratio each week, nothing else.

## Media (funnel-stage creatives)

`media/` ships with one creative per funnel moment — the bot attaches it
automatically; if a file is missing it silently falls back to text:

| File | Fires on | Why this one |
|---|---|---|
| `start.png` | /start greeting | hero shot, **no money imagery** — safe ad scent for Meta/TG ads |
| `new_match_1.png` / `new_match_2.png` | match announcements | rotate by match id — kills banner blindness |
| `matchday.png` | matchday bridge (<3h) | rain + intensity = urgency frame |
| `streak_bonus.png` | streak bridge + reg congrats | chest/coins appear only AFTER engagement, never in ads |
| `rehook.png` | quiet-predictor re-hook | phone glow = "the bot is calling you" |
| `podium.png` | weekly Top-10 broadcast | trophy + prizes = the proof moment |
| `dep_win.png` | deposit postback | gold payoff at the deepest stage |

**Rule: creatives with coins/treasure (streak_bonus, dep_win, podium) stay
INSIDE the bot.** In Meta/Telegram ad creatives use only start/new_match/
matchday/rehook style imagery — a free prediction league, not gambling.
Results, cascade and emails stay text-only on purpose: nurture should feel
personal, not like a banner feed.
