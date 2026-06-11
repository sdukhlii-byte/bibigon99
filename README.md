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

| Command | What it does |
|---|---|
| `/addmatch Brazil;Argentina;2026-06-15 18:00` | adds match (UTC). Within 24h of kickoff the scheduler announces it to everyone with personalized text for fans of the two teams |
| `/settle 3 1 2:1` | settles match #3 as home win, score 2:1. Scores all predictions, sends win/loss pushes, fires streak/cold-streak bridge triggers automatically |
| `/stats` | funnel snapshot: users → team picked → predictors → emails → bridge clicks → registered |
| `/broadcast <text>` | send to everyone (rate-limited) |

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
