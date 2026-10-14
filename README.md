# David-
David

David is a trading assistant I built for myself. It runs on my own laptop, connects to my broker, and helps me place forex trades.

But most of the code isn't about finding good trades. It's about making the bad version impossible.

I started building it after I lost £229 on a single gold trade. My account was £500, so one trade took nearly half of it. I didn't lose that money because my analysis was wrong. I lost it because I picked the trade size by feel instead of working it out. The trade was 20 times bigger than it should have been.

So this project is mostly a set of brakes.

What it does
Reads live prices and my real account balance from MetaTrader 5
Works out the correct trade size from that balance and how far away my stop is
Stages a trade and then waits for me to confirm it before anything is sent to the broker
Keeps a journal of every trade and calculates whether I'm actually making money
Runs a backtester over two years of real price data, to check whether a trading rule is real or whether I just imagined it
What it refuses to do

This is the more interesting half, and it's the part I'd want someone to look at.

It won't trade gold

Gold is where I lost the £229. It's on a blocked list, and David can't take it off.

The only way to unblock it is for me to open a config file and edit it by hand. I deliberately didn't give David a tool that can write to that setting. An assistant that can remove its own safety rail doesn't really have one.

It won't move a stop loss further away

A "stop loss" is the price where you admit you were wrong and get out. Moving it further away converts a small planned loss into a big unplanned one. It's the single most common way beginners blow up an account, and it always feels reasonable at the time.

The function that moves stops can only move them in the profitable direction. There's no override, no force=True, no flag. The moment you add one, you'll use it on exactly the trade where you shouldn't.

It can't confirm its own trades

This one took me two tries, and the first try was fake security.

Attempt 1. I gave the "send trade" function a parameter called confirm, which had to be set to "YES". I thought that meant a human had to approve.

It didn't. The AI is the thing filling in the parameters. It can just write "YES" itself. I hadn't built a lock — I'd written down a rule and hoped it would be followed.

Attempt 2, the real one. Now when David stages a trade, the code prints a random 4-digit number to the terminal window — the screen the program writes to. That number is never included in the text David gets back. So David genuinely doesn't know it. It has to stop and ask me to read it off the screen.

The difference matters:

Attempt 1 was a rule the AI was asked to follow.
Attempt 2 is a fact the AI cannot reach.

Only the second kind actually holds. If a safety check can be satisfied by the AI deciding to satisfy it, it isn't a safety check.

It won't read its own secrets

The tool that reads documents has a blocked list of filenames — .env, credentials.json, secrets.json. Even though it has permission to read files in the project folder, it refuses those. Same reasoning as above: the restriction is in the code that does the reading, not an instruction asking it to be careful.

The bug that was in there for two years

This is my favourite thing in the project, because it's the one that proved I'd been fooling myself.

Some background

My broker's server clock runs 3 hours ahead of UTC. The price data it sends has timestamps that look like normal UTC timestamps, but they're actually server time wearing a disguise.

My backtester needed New York time, because that's when the trading sessions I care about happen. So it subtracted a flat 4 hours.

Two things were wrong with that.

The number was wrong. It should have been 7 or 8 hours, not 4.
A fixed number can never be right. Europe and the US change their clocks on different dates. For about one week every autumn, the gap between them is 6 hours instead of 7. Any single hardcoded offset is wrong at least twice a year.
What it broke

Trading sessions were measured over the wrong hours, so one of my filters had been testing the wrong range of prices the whole time.

Worse, daily price bars got filed under the wrong day. Every Friday setup was filed under Saturday — a day the backtester never looks at. They didn't error. They just quietly vanished.

How I found it

A results table was missing a row for Friday. That's it. One missing row.

What happened after I fixed it

Fridays turned out to be 21% of all the profit. They'd been invisible for two years.

Then the uncomfortable part. Almost everything else I believed stopped being true.

A quick note on "R": R is one unit of risk. If I risk £3 and make £6, that's +2R. Measuring in R instead of pounds means I can compare trades of different sizes and on different currency pairs fairly.

Before and after the fix:

What I believed	Before	After
"Divergence setups are better"	+0.247R	+0.001R
"Deep pullbacks are better"	+0.278R	+0.117R
"Deeper liquidity sweeps are better"	strong	+0.006R

All three were measured against a broken clock. They were never real.

What did survive was the four basic structural rules the strategy is built on. Every extra condition I'd layered on top turned out to be noise. I'd been adding complexity on top of a bug.

My own notes file had an entry saying "Monday is a trap day — do not trade." The corrected data says Monday is the best day of the week (+0.400R). If I'd kept trusting that note, David would have refused the best setups I get.

Fixing this meant deleting findings I was proud of. That's what measuring is for.

How it's built

It's an agent loop, and it's simpler than it sounds:

I send a message.
The AI gets a list of tools it's allowed to use, with descriptions.
It picks one and says what arguments it wants.
My Python code runs the tool — not the AI.
The result goes back, and it carries on until it has an answer.

The key point is step 4. The AI never touches my broker. It asks for things, and my code decides whether to do them. Every refusal in this README lives in that layer, which is why none of them can be talked around.

A few other decisions worth mentioning:

Tool descriptions come from Python docstrings. That means the docstring isn't a comment — it's the thing that tells the AI what the tool does and when to use it. A vague docstring causes a real bug. They're written as carefully as the code.

One shared function for anything time-related. The timezone bug appeared in five different files, because each one converted time its own way. There's now one module that every other file imports. When I found the same class of bug a second time (reading pip size from the currency pair's name, which is 100x wrong for gold), I fixed it the same way — one function, everyone calls it.

That's the general lesson: writing a rule in the AI's instructions didn't stop it producing code with the bug. A shared function did. Instructions ask; code enforces.

Risk is capped in code. 1% of the account per trade by default, 2% maximum. The absolute ceiling can't be raised past 5% even by me, and every change to the setting is logged with a reason.

What it can't do

Being straight about this, because a README that only lists wins isn't worth reading.

It can't watch the market. It only sees prices at the moment I ask. It can't notice something happening while I'm away, and it's specifically instructed not to claim it can — early versions offered to "monitor" trades, which was a plausible-sounding lie.
It doesn't know about correlation. It checks each position's risk on its own. But GBP/USD and EUR/USD move together about 85% of the time, so two "safe" 1.1% trades can really be one 2.2% trade. The individual checks all pass. This is a known hole and it's on the list.
The live results aren't in yet. The backtest says +0.267R per trade over 464 setups. That's a backtest, not reality. Real trading started recently and needs about 65 trades before the number means anything. I'm not going to claim it works until it has.
My account is small enough that the maths pinches. The smallest trade size my broker allows is 0.01 lots. On a ~£300 account that's about 1.1% of risk on a 45-pip stop — so for anything wider, 1% isn't reachable and the only real choice is to skip the trade.

The strategy rules themselves aren't in this repo. They're derived from paid material that isn't mine to publish.

Running it

You'll need Python 3.10+, a MetaTrader 5 terminal (Windows only — that's MT5's own limitation), and an Anthropic API key.

bash
pip install -r requirements.txt

cp .env.example .env                      # then put your real key in it
cp risk_config.example.json risk_config.json

python main.py

The backtester runs on its own:

bash
python backtest.py --exit tp1

There are no real credentials, no account data and no trade history in this repo.

Repo map
main.py              entry point and the agent loop
david_core.py        the agent loop, tool dispatch, model routing
tools/
  market.py          shared time + pip-size functions (the fix for both bug classes)
  sizing.py          position sizing, risk limits, the gold refusal
  execute.py         two-step staging and the confirmation code
  manage.py          view positions, move stops in the safe direction only
  journal.py         trade log and expectancy maths
  memory_audit.py    finds stale notes that contradict current rules
  docreader.py       reads project files, refuses to read secrets
backtest.py          the backtester
docs/                write-ups, including the timezone postmortem
