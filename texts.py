# ============================================================
# ALL FUNNEL COPY — WORLD CUP, ENGLISH, BROAD GEOs
# Edit texts here, never inside main.py
# Placeholders: {name} {team} {streak} {rank} {accuracy} {t1} {t2} {hours}
# ============================================================

TEAMS = ["Brazil", "Argentina", "France", "England",
         "Spain", "Germany", "Portugal", "Other / Neutral"]

# ---------- FLOOR 1: /start + segmentation ----------

START = (
    "Yo! 👊 One question and I'll set everything up for you.\n\n"
    "<b>Who takes the World Cup this year?</b> 🏆"
)

# ad scent: shown when /start payload begins with "ig" (Instagram/Meta traffic)
START_IG = (
    "Instagram crew — you found it 👊\n\n"
    "This week's <b>500 USDT prize pool</b> is live, and you're in before "
    "most people even know it exists.\n\n"
    "One question and I'll set everything up:\n"
    "<b>Who takes the World Cup this year?</b> 🏆"
)

WELCOME_BACK = (
    "Back in the game 👊 Still riding with <b>{team}</b>.\n\n"
    "Your league card: /mystats · next matches: /schedule · live hub below 👇"
)

TEAM_SAVED = (
    "Locked in ✍️ <b>{team}</b> to go all the way.\n\n"
    "Here's what you just unlocked:\n"
    "⚡ Live scores for every match — faster than the spoilers in your group chat\n"
    "🎯 <b>Predictor League</b>: call every result, climb the leaderboard\n"
    "🏆 Top 10 every week split a <b>500 USDT prize pool</b>\n\n"
    "Your first match is below. Make your call 👇"
)

TEAM_SAVED_NO_MATCH = (
    "Locked in ✍️ <b>{team}</b> to go all the way.\n\n"
    "⚡ Live scores for every match\n"
    "🎯 <b>Predictor League</b> — call results, climb the board\n"
    "🏆 Top 10 each week split a <b>500 USDT prize pool</b>\n\n"
    "Next match opens for predictions soon — I'll ping you the second it does. "
    "Meanwhile, check the live hub 👇"
)

REMIND_NO_TEAM = (
    "{name}, the World Cup won't wait for you 👀\n"
    "One tap — who's lifting the trophy?"
)

# ---------- FLOOR 2: predictions + email ----------

NEW_MATCH = (
    "🔥 <b>Prediction window open:</b>\n\n"
    "<b>{t1} vs {t2}</b> — kicks off in {hours}h.\n\n"
    "What's your call?"
)

NEW_MATCH_TEAM = (
    "⚡ <b>{team} play in {hours}h.</b>\n\n"
    "<b>{t1} vs {t2}</b> — you've backed them since day one. "
    "Now put your name on it. What's your call?"
)

PREDICTION_SAVED = (
    "Pick locked: <b>{pick}</b> 🎯 You're in the game.\n\n"
    "One last thing — the weekly <b>500 USDT prize pool</b> only pays out to "
    "verified players. One tap below and you're prize-eligible "
    "(it also makes sure your result reaches you even off Telegram):"
)

BTN_VERIFY_CONTACT = "✅ Verify for prizes"
BTN_VERIFY_SKIP = "Skip for now"

# appended to the verification ask when PRIVACY_URL is set (GDPR, EU traffic)
PRIVACY_FOOTNOTE = (
    "\n\n<i>By verifying you agree to receive league updates. "
    "Privacy policy: {url} — unsubscribe anytime with /skip.</i>"
)

CONTACT_SAVED = (
    "Verified ✅ You're prize-eligible.\n\n"
    "I'll ping you the moment the final whistle blows. Good luck — "
    "let's see if that read of yours is real."
)

ASK_EMAIL_FALLBACK = (
    "No problem. Drop an email instead and your points still count toward "
    "the prize pool — or /skip and play just for the leaderboard:"
)

REHOOK = (
    "{name}, your picks went quiet 👀 Scared of the leaderboard?\n\n"
    "The league moved on without you — and the prize pool resets weekly, "
    "so this week is anyone's game. Next match is waiting for your call."
)

# ---------- content layer: schedule / news / stats ----------

SCHEDULE_HEADER = "📅 <b>Upcoming World Cup matches</b>\n"
SCHEDULE_ROW = "{when} — <b>{t1} vs {t2}</b>{picked}"
SCHEDULE_EMPTY = "No upcoming matches in the calendar yet — check back soon ⚽"
SCHEDULE_FOOTER = "\nTap /news for today's headlines · your stats: /mystats"

NEWS_HEADER = "⚽ <b>Football today</b>\n"
NEWS_FOOTER = (
    "\nWhile everyone's reading, the sharp ones are calling results — "
    "next match: /schedule 🎯"
)
NEWS_EMPTY = "Couldn't reach the newsroom right now — try again in a minute."

MYSTATS = (
    "📊 <b>Your league card</b>\n\n"
    "Team: <b>{team}</b>\n"
    "This week: <b>{week_correct}/{week_total}</b> correct (prize window)\n"
    "All-time: <b>{correct}/{total}</b> ({accuracy}%)\n"
    "Current streak: <b>{streak}</b> 🔥\n"
    "League rank: <b>#{rank}</b>\n\n"
    "Top 10 split the weekly 500 USDT pool. Keep climbing."
)
MYSTATS_EMPTY = "No picks yet — your card is empty. Fix that: /schedule"

EMAIL_SAVED = (
    "Done ✅ You're prize-eligible.\n\n"
    "I'll ping you the moment the final whistle blows. Good luck — "
    "let's see if that read of yours is real."
)

EMAIL_INVALID = "That doesn't look like an email 😅 Try again, or hit /skip."
EMAIL_SKIPPED = (
    "Fair enough — your points still count for the leaderboard, "
    "but prizes only go to verified players. /verify anytime."
)

RESULT_WIN = (
    "✅ <b>{t1} {score} {t2}</b> — you called it! +{points} pts.\n"
    "Streak: <b>{streak}</b> 🔥 Next match is coming — keep it alive."
)

RESULT_LOSS = (
    "❌ <b>{t1} {score} {t2}</b> — not this time. Happens to the best analysts.\n"
    "The week is long. Win it back on the next one."
)

# ---------- FLOOR 3: bridge triggers ----------

TRIGGER_STREAK = (
    "{name}, <b>{streak} out of {streak}</b> 🔥 That's not luck anymore — that's a read.\n\n"
    "Honest question: you just called {streak} results in a row <i>for free</i>. "
    "What would that be worth if those picks weren't for points?\n\n"
    "League players get a welcome package: your first deposit plays as <b>×2.25</b> "
    "(drop 100 USDT — play with 225, up to 5,000) + <b>80 free spins</b> + "
    "<b>cashback on your first bets</b>.\n\n"
    "Your read + double the bankroll. Run your own numbers 👇"
)

TRIGGER_COLDSTREAK = (
    "Rough week — {correct} out of {total} 😬 And here's the interesting part:\n\n"
    "Players with the welcome package get money back even on a week like this — "
    "<b>cashback runs on your first bets, win or lose</b>. Miss a pick, part of it "
    "comes back. In the free league, a miss pays you nothing.\n\n"
    "See how the safety net works 👇"
)

TRIGGER_MATCHDAY = (
    "⚡ <b>{team} kick off in {hours}h.</b>\n\n"
    "You've backed them since day one — I remember your answer. "
    "With the welcome package, your first bet on {team} plays <b>double</b>. "
    "Claim it before kickoff and the bonus lands instantly.\n\n"
    "After the whistle, this moment's gone."
)

BRIDGE_BUTTON = "📊 Check my numbers"
MATCHDAY_BUTTON = "⚡ Claim before kickoff"

# ---------- FLOOR 5: cascade (clicked bridge, no registration) ----------

CASCADE_2H = (
    "Your ×2.25 calculation is still sitting there 👀\n\n"
    "One thing worth repeating: <b>cashback kicks in from your very first bet</b> — "
    "even if a pick doesn't land, part of it comes back. This isn't coin-flip "
    "gambling, it's playing with a safety net."
)

CASCADE_24H = (
    "Quick stat from your league, {name}:\n\n"
    "<b>{n_converted} players</b> have already moved from free picks to the real thing. "
    "You're sitting at <b>{accuracy}% accuracy</b> — better than most of them.\n\n"
    "They're getting paid for what you're doing for free. That math should bother you."
)

# used while real converted count is still small — specificity without invention
CASCADE_24H_EARLY = (
    "Quick stat from your league, {name}:\n\n"
    "You're sitting at <b>{accuracy}% accuracy</b> — calling results for free "
    "that others get paid for.\n\n"
    "The welcome package turns the exact same reads into real numbers: "
    "×2.25 on the first deposit + cashback either way. That math should interest you."
)

CASCADE_5D = (
    "Okay {name}, no hype, just numbers.\n\n"
    "Minimum entry: <b>20 USDT</b>. That's a pizza.\n"
    "With the bonus it becomes <b>45 in play + 80 free spins + cashback</b>.\n\n"
    "It's a small test, not a life decision. Worst case, the safety net catches part of it."
)

CASCADE_7D = (
    "Closing the topic — I won't bring the bonus up again. It stays attached to your "
    "league account; grab it whenever you're ready.\n\n"
    "Meanwhile the league doesn't stop — <b>{team}</b> need your calls. "
    "Next match: /schedule 🎯"
)

# ---------- FLOOR 6: registered, no deposit ----------

REG_CONGRATS = (
    "Account created, bonus locked to it ✅\n\n"
    "It activates with your first deposit — from <b>20 USDT</b>. The stack: "
    "<b>×2.25 on your bankroll + 80 free spins + cashback safety net</b>.\n\n"
    "{team} play soon — get it done before kickoff."
)

# used when the user's team has a REAL upcoming fixture in the calendar
REG_CONGRATS_MATCH = (
    "Account created, bonus locked to it ✅\n\n"
    "It activates with your first deposit — from <b>20 USDT</b>. The stack: "
    "<b>×2.25 on your bankroll + 80 free spins + cashback safety net</b>.\n\n"
    "<b>{team} kick off in {hours}h</b> — get it done before the whistle."
)

# used when there is no scheduled fixture for the team (eliminated / Other)
REG_CONGRATS_GENERIC = (
    "Account created, bonus locked to it ✅\n\n"
    "It activates with your first deposit — from <b>20 USDT</b>. The stack: "
    "<b>×2.25 on your bankroll + 80 free spins + cashback safety net</b>.\n\n"
    "Matches run every day — next one: /schedule. Lock it in before the odds move."
)

DEP_CONGRATS = (
    "Bonus activated 🔥 Your bankroll just played <b>×2.25</b>, free spins "
    "are loading, cashback is armed.\n\n"
    "Your league picks now have real weight behind them. Same reads — "
    "different stakes. Good luck out there 🎯"
)

WEBAPP_NUDGE = (
    "{name}, you're playing on hard mode 😅\n\n"
    "Your picks work in chat, but the <b>Live Hub</b> is where the league "
    "actually lives: live scores as they happen, your full card, the "
    "leaderboard you're climbing — all in one screen.\n\n"
    "Took 30 seconds to set up. Takes 1 tap to open 👇"
)

# ---------- weekly league reset ----------

WEEKLY_PODIUM = (
    "🏆 <b>Week closed — here's your Top 10:</b>\n\n"
    "{podium}\n\n"
    "Winners: check your DMs about the prize pool split.\n"
    "Scores are reset — <b>this week is anyone's game</b>. First match: /schedule 🎯"
)

# ---------- buttons ----------

BTN_OPEN_APP = "🚀 Open Live Hub"          # generic fallback
BTN_HUB_TRACK = "📺 Track your pick LIVE"   # right after a pick / verify
BTN_HUB_SCORES = "⚡ Live scores now"       # returning user
BTN_HUB_RANK = "📊 See where you rank"      # hub-nudge for chat-only players
BTN_HUB_SCHEDULE = "📅 See all matches"     # no match open yet

HUB_AFTER_VERIFY = (
    "Your pick is in play — watch the score move in real time, "
    "and check who you need to beat for the weekly pool 👇"
)
BTN_PICK_1 = "1️⃣ {t1}"
BTN_PICK_X = "🤝 Draw"
BTN_PICK_2 = "2️⃣ {t2}"
