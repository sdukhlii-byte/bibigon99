# LOVABLE PROMPT — paste everything below into Lovable

Build a Telegram Mini App: **"Predictor League — World Cup Edition"**. Mobile-only web app (~380px viewport) that runs inside Telegram via the WebApp SDK. Dark esports-style sports product, English language.

## Purpose & audience

Football fans arriving from a Telegram bot. They predict World Cup match results for free, climb a leaderboard, chase a weekly 500 USDT prize pool — and the PLAY tab bridges the hottest users to a betting welcome offer. Vibe: fast, competitive, slightly cocky. Never corporate.

## Design system

- Background `#0B0F14` (near-black), cards `#121821` with 1px border `#1E2733`, radius 16px
- Accent: neon green `#39FF88` (CTAs, active tab, live indicators). Secondary accent: amber `#FFB341` for scores/odds-style numbers
- Red pulsing dot for LIVE states
- Typography: Space Grotesk for headings/numbers (bold, slightly condensed feel), Inter for body. White `#F2F5F7` primary text, `#8A97A5` secondary
- Buttons: pill, neon green fill with dark text, subtle glow on press
- Bottom tab bar, 4 tabs with emoji+label: 🔴 LIVE · 🎯 PICKS · 🏆 RANK · 💰 PLAY
- Micro-animations: score flips, confetti burst when a prediction is saved, leaderboard rows slide in
- Skeleton loaders for all data fetches. No layout shift.

## Telegram WebApp integration

- Load `https://telegram.org/js/telegram-web-app.js` in index.html
- On boot call `Telegram.WebApp.ready()` and `Telegram.WebApp.expand()`
- Get the user from `Telegram.WebApp.initDataUnsafe.user` (name for greeting, id for the PLAY link)
- Send raw `Telegram.WebApp.initData` string as header `X-Telegram-Init-Data` on EVERY API request (the backend verifies the signature)
- Use `Telegram.WebApp.HapticFeedback.impactOccurred('medium')` on pick taps
- If opened outside Telegram (no initData): show a friendly gate screen "Open this app from our Telegram bot to play" with a button linking to `https://t.me/BOT_USERNAME` (make BOT_USERNAME a config constant)

## Backend API (already live — do not mock differently)

Base URL: config constant `API_BASE` (e.g. `https://xxx.up.railway.app`). All JSON.

- `GET /api/me` → `{name, team, correct, total, streak, accuracy, registered}` — 404 means user hasn't started the bot (show the gate screen)
- `GET /api/matches` → array of `{id, t1, t2, kickoff (unix), result ("1"|"X"|"2"|null), score ("2:1"|null), status ("upcoming"|"live"|"settled"), my_pick ("1"|"X"|"2"|null)}`
- `POST /api/predict` body `{match_id, pick}` where pick ∈ "1"|"X"|"2" → `{ok, first_prediction}` — 400 means match closed
- `GET /api/leaderboard` → array of `{rank, name, team, correct, total, streak}`

Handle 401/404 gracefully with the gate screen; show toast on network errors with retry.

## Tab 1 — 🔴 LIVE

Header: "LIVE NOW" + red pulsing dot + match count chip. List of matches with status `live`: big card per match — team names left/right, amber score in the middle in a rounded badge, "LIVE" tag. Below: upcoming matches today as smaller rows with kickoff countdown ("in 2h 14m"). Pull-to-refresh. Empty state: "No live matches right now — next kickoff in {countdown}" with a ⚽ illustration.

## Tab 2 — 🎯 PICKS (default tab on open)

Top: personal strip — "Hey {name} 👋" + three stat chips: Accuracy {accuracy}%, Streak {streak}🔥, Picks {total}.

Prize banner (always visible, amber gradient border): "🏆 Weekly prize pool: **500 USDT** — top 10 split it. Resets every Monday."

Then the list of `upcoming` matches as prediction cards: team names, kickoff countdown, three big tap targets **[1] [Draw] [2]**. Tapping one: optimistic UI, POST /api/predict, selected button fills neon green, confetti micro-burst, haptic. If `my_pick` exists, show it pre-selected with label "Your call ✓" and allow changing until kickoff. Settled matches collapse into a compact "Recent results" section showing score + ✅/❌ against the user's pick.

If POST returns `first_prediction: true`, show a one-time bottom sheet: "Pick locked 🎯 You're officially in the league. Check the bot — one tap there makes you prize-eligible."

## Tab 3 — 🏆 RANK

Weekly leaderboard, top 20: rank medal (🥇🥈🥉 then numbers), name, small team tag, correct/total, streak 🔥 column. The current user's row is highlighted neon green and pinned to the bottom if outside top 20 ("You — #{rank}"). Header line: "Top 10 split 500 USDT on Monday." Subtle row stagger animation.

## Tab 4 — 💰 PLAY (the money screen — most polished)

Built for users who already proved themselves in the league. Structure top to bottom:

1. **Hook headline** (Space Grotesk, big): "TURN YOUR READS INTO REAL MONEY" with "REAL MONEY" in neon green.
2. Personal proof line (uses /api/me): "You've called {correct} of {total} matches — {accuracy}% accuracy. Playing for points only is charity."
3. **Bonus calculator card** (centerpiece): label "Your first deposit", slider 20–4,000 USDT (default 100), live computed line below in amber: "You play with **{amount × 2.25} USDT**". Caption: "125% welcome bonus, up to 5,000 USDT".
4. Value stack list with ✅ icons: "80 free spins on top, instantly" · "Cashback on your first bets — even a bad week pays part back" · "Every World Cup market, live odds" · "Crypto in, crypto out — withdrawals in minutes".
5. Objection accordion (3 items): "What if I lose?" → cashback safety net explanation; "How fast are withdrawals?" → minutes, crypto; "Minimum to start?" → 20 USDT, bonus works from the first deposit.
6. **Single CTA button** (sticky at bottom of this tab, glowing): "🚀 CLAIM ×2.25 & PLAY". On tap call `Telegram.WebApp.openLink(TRACKER_URL + '?sub_id_2=' + Telegram.WebApp.initDataUnsafe.user.id)`. `TRACKER_URL` is a config constant. Sub-caption under button: "Registration takes 30 seconds. Bonus locks to your account instantly."
7. If `/api/me` returns `registered: true`, replace the whole tab content with: "Bonus locked to your account ✅ Activate it with your first deposit — from 20 USDT" + the same CTA labeled "Open my account".

## General

- Single-page app, client-side tab switching, no router needed
- All config constants (API_BASE, TRACKER_URL, BOT_USERNAME) in one config file at the top level
- Countdown timers tick live (1s interval), format "2h 14m" / "live"
- Keep total bundle light; no heavy chart libs
- Every screen must look complete with zero data (empty states written above)
