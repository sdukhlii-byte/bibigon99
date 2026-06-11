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
| `FOOTBALL_API_KEY` | api-sports.io key — enables fixture auto-sync (free: 100 req/day, sync uses ~48) |
| `FOOTBALL_LEAGUE_ID` | optional, default `1` (FIFA World Cup) |
| `FOOTBALL_SEASON` | optional, default `2026` |
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

`uid` arrives at the prelanding as a query param (`?uid=...`) — pass it through
the reg form as sub_id / clickid. On postback the bot:
stops the cascade, sends the congrats + deposit-nudge message.

## Daily ops (admin commands in the bot)

With `FOOTBALL_API_KEY` set, **matches run themselves**: fixtures sync from
API-Sports every 30 min, announcements go out within 24h of kickoff,
finished matches auto-settle — results, streaks, and bridge triggers fire
without you. Admin commands remain as manual override:

| Command | What it does |
|---|---|
| `/addmatch Brazil;Argentina;2026-06-15 18:00` | manually add a match (UTC) — only needed without the API key |
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
