# Airport Investment Intelligence Agent

An AI agent that answers an investment analyst's questions about US airport capacity — which airports have
demand pressing on their existing capacity, and why. Built for the Deloitte Digital Forward Deployed Engineer
take-home (September 2026).

**The one design idea:** the language model never computes a number. Every figure comes from a deterministic
Python function over BTS T-100 data, and every function returns *how* it computed the result and what it
cannot show. The model picks the function, narrates, and states the assumptions. See `DESIGN.md`.

## Run it

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                              # then put your ANTHROPIC_API_KEY in .env
streamlit run app.py
```

The repository ships a warmed data cache (`data/airports.db`, 1,319 airports, Jan 2019 – Apr 2026, fetched
2026-09-21), so the app runs with no network except the Anthropic API. To see the agent pull from BTS live,
delete `data/airports.db` and run `python -m ingest.build_db` — or ask the agent to refresh one airport.

Optional: `SODA_APP_TOKEN` in `.env` avoids Socrata throttling on a full refresh. `ANTHROPIC_MODEL` overrides
the model (default `claude-sonnet-5`).

## Try these

- Which airports in New England are strong candidates for terminal expansion?
- Compare LA and Santa Ana airport congestion levels.
- What is the percentage of long haul flights out of Anchorage airport?
- What is the unmet flight demand in SFO airport and why?
- Which large hubs are still below their 2019 traffic?
- How is the score computed, and how sensitive is the ranking to the weights?

Under every answer, an expander shows the tool calls the model made, their arguments, the data source
(`cache, fetched <date>` or `data.bts.gov live`), the computation method, and the caveats.

## Data

BTS **T-100 Segment Summary by Origin Airport** (dataset `r495-tyji`) via the Socrata SODA API on
`data.bts.gov`: one row per airport per month — departures, passengers, seats, load factor, stage lengths,
domestic/international split, freight. Airport → state comes from the public OurAirports list
(`data/airport_states.csv`). Nothing else.

What the data does not contain, and the agent says so: delays, gates, runway counts, suppressed demand.
Departure counts include all-cargo flights. "Seats" are aircraft seats, not terminal capacity.

## Layout

```
ingest/     soda.py (SODA client) · cache.py (SQLite cache-through) · build_db.py (warm-up)
scoring/    config.py (every tunable: KPIs, weights, tiers, thresholds) · metrics.py (19 KPIs per airport) · kpi.py (Capacity Pressure Index)
agent/      tools.py (8 tools the model can call) · agent.py (tool-use loop) · prompts.py (system prompt)
app.py      Streamlit chat
tests/      33 tests, all offline against the committed cache:  pytest -v
scripts/    answer_bank.py — runs 22 analyst questions and writes docs/answers-<date>.md
docs/       the real answers, for review
```

## Tests

```bash
pytest -v                       # 33 tests: data assumptions, scoring, one per analyst question, the agent loop (stubbed)
python -m scripts.answer_bank   # real numbers for 22 questions → docs/answers-<date>.md
```

## Housekeeping

`node_modules/`, `package.json` and `package-lock.json`, if present, are leftovers of an accidental
`npm install` and are not part of the project (they are gitignored).
