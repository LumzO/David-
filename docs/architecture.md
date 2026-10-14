# David

An autonomous AI agent for market research and analysis, built on the Anthropic
Claude API.

David is a personal intelligence agent that pulls live market data from four
external sources and directly from a MetaTrader 5 broker terminal, reasons
through a structured trading methodology, and produces analysis with defined risk
parameters — or refuses to, which turns out to be the harder and more useful
behaviour.

> **Scope note.** This is a personal research project, not a product and not
> financial advice. The trading edge is unproven — see
> [Honest limitations](#honest-limitations).

---

## What it actually is

The model is Claude. Everything around it is the engineering:

- **Tool orchestration** — 27 tools, autonomously chained; the agent decides
  which it needs and loops until it can answer
- **Two-tier knowledge injection** — a lean always-on core plus a detailed
  domain framework loaded on demand, by section
- **Model routing** — cheap model by default, escalating to the frontier model
  when the work warrants it
- **Persistent memory and journalling** — across sessions, with structured
  decision logging for later measurement
- **Multimodal I/O** — image analysis, chart rendering, speech synthesis in
  three registers, local speech recognition
- **Self-modification** — the agent can read, write and execute code in its own
  project, behind guardrails

---

## Architecture

```
                    ┌─────────────────────┐
   Terminal ──────► │                     │
                    │    david_core.py    │  agentic loop
   Web UI  ───────► │  run_david(input)   │  model routing
   (FastAPI/WS)     │                     │  history hygiene
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
        ┌──────────┐    ┌────────────┐   ┌────────────┐
        │  values  │    │   TOOLS    │   │  Claude    │
        │  system  │    │  registry  │   │    API     │
        │  prompt  │    │ (27 tools) │   │            │
        └──────────┘    └─────┬──────┘   └────────────┘
                              │
   ┌──────────┬───────────┬───┴────┬───────────┬──────────┐
   ▼          ▼           ▼        ▼           ▼          ▼
 market    analysis   knowledge  memory     media      self
 data                                                  coding
```

**Two front doors, one brain.** `david_core.py` holds the agent; `main.py` is a
terminal client and `server.py` is a FastAPI/WebSocket backend. Both import the
same loop, so behaviour can't drift between interfaces.

---

## Design decisions worth explaining

### Knowledge loaded on demand, not stuffed into context

The domain framework is roughly twelve substantial sections. Putting all of it
in the system prompt would mean paying for the entire body of knowledge on every
message — including "hello."

Instead: a lean summary lives always-on and shapes how the agent thinks; the full
framework sits behind a tool and is retrieved by section only when a real
analysis needs it. Retrieval-augmented design applied to the system prompt rather
than a document store.

Side effect worth noting: prompt caching has a minimum token threshold. The
system prompt had been sitting just below it, so caching was silently inactive.
Adding the lean summary crossed the threshold and caching engaged on its own —
visible immediately in the token diagnostics printed on every call.

### Routing decided by behaviour, not classification

Rather than trying to classify request complexity up front, the loop escalates
the model when the agent *reaches for* an expensive tool — the domain framework,
broker candle data, the chart renderer. Manual trigger words also force it.

The routing decision is made by what the agent chose to do, which is more
reliable than guessing what the user meant.

### Designed to refuse

The agent requires multiple independent confirmations before endorsing a trade,
and must state plainly what it cannot verify. Asked directly for entry and stop
levels on a setup its own checklist had failed, it declined — on the grounds that
producing a plan on a no-trade read would be dishonest.

This has proven load-bearing. Asked to analyse an instrument, it refused on the
basis that the prices were implausible. It was right: the broker exposes two
similarly-named instruments and the wrong one was being resolved. Chasing that
down surfaced two further defects — pip size was being inferred from the
instrument *name* rather than the broker's digit precision (a 100× error in
spread calculation), and the wide-spread threshold was calibrated for currency
majors only, so it would have rejected every legitimate metals trade.

One well-designed refusal surfaced three genuine bugs.

### Self-modification behind guardrails

The coding tools are the most powerful capability here and the guardrails *are*
the design:

| Risk | Mitigation |
|---|---|
| Path traversal | Every path resolves through one check confirming it stays inside the project |
| Credential exposure | `.env` blocked outright, read or write |
| Destructive overwrite | Automatic `.bak` before any write |
| Breaking the agent itself | Core files return a diff and require explicit approval |
| Runaway execution | Separate process, timeout, Python only — no shell |

---

## Notable bugs and what they taught

**History cleanup went blind on images.** After a multi-tool turn the
conversation history is full of tool-use blocks; a cleanup routine walks back to
the original user message and replaces everything after it. That routine looked
for user messages whose content was a *string*. An image message's content is a
*list* — so it walked straight past the image, found an older text message, and
deleted the image with everything after it.
*Lesson: when you extend an agent's inputs, audit every assumption existing code
makes about message shape.*

**The model claiming actions it never took.** It would say "I'll remember that"
without calling the memory tool — producing the sentence a helpful assistant
would produce, rather than performing the action. Not a code bug; an instruction
design problem. The same class recurred with voice register switching, where the
tool was called going *into* a register but never coming back out, because
"return to default" was implicitly treated as the absence of an action.

**Debugging behaviour that was never broken.** Hours spent tightening prompts
because no tool call appeared in the trace — until it turned out that adding a
callback hook had accidentally *replaced* the diagnostic print rather than
sitting alongside it. The agent had been correct throughout.
*Lesson: verify observability before debugging behaviour.*

**Confidently confirming a wrong premise.** The agent invented an absolute file
path that didn't exist, and when that path was fed back to it, agreed it was
correct and rationalised why. Fixed by instructing it never to assume paths and
giving it a directory-listing tool. Give an agent a way to verify and it stops
guessing.

---

## Stack

| Layer | Technology |
|---|---|
| Reasoning | Anthropic Claude API (tool use, vision, prompt caching) |
| Backend | Python, FastAPI, WebSocket |
| Market data | MetaTrader 5 Python API, Twelve Data, Finnhub, Forex Factory |
| Charting | matplotlib (rendering), Pillow (annotation) |
| Speech | Edge TTS (synthesis), Whisper (local recognition) |
| Frontend | Vanilla HTML/CSS/JS |
| Persistence | JSON |

---

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env        # add your API keys
```

```bash
python main.py                              # terminal
python -m uvicorn server:app --port 8000    # web UI at localhost:8000
```

Broker features require the MetaTrader 5 terminal running and logged in on the
same machine (Windows only — the MT5 Python bridge has no macOS or Linux build).

---

## Honest limitations

**No evaluation harness.** Testing has been structured but manual — each tool in
isolation before integration, then end-to-end against live data with known
expected outcomes. That is not automated evaluation. A fixed scenario suite with
expected tool-call sequences, running on every change, is the first thing worth
building next.

**The trading edge is unproven.** The agent reasons through the methodology
correctly and declines weak setups consistently — ten consecutive no-trade
verdicts on live data at time of writing. But that is behaviour, not
performance. There is no meaningful track record yet. The journalling and broker
trade-history integration exist specifically so that a real win rate, R:R and
drawdown can be measured before any conclusion is drawn.

**Single user, single machine.** Memory is a JSON file with keyword matching. At
scale this wants a database with embedding-based retrieval, structured logging of
every tool call for audit, and cleaner separation between agent core and
interfaces.

**Domain knowledge is not published.** The trading framework is encoded from
third-party course material for personal use and is excluded from this
repository.

---

## Licence

Personal project. Not licensed for redistribution or commercial use.
