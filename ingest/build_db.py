"""
Owner: Claude. Plan §3.5. Optional warm-up, not a required step.

`python -m ingest.build_db`
  - lists every airport code in r495-tyji (one $select distinct query)
  - loops cache.get_monthly(code) over all of them
  - builds data/airport_states.csv if missing
  - prints row counts for BOS / LAX / SNA / ANC / SFO and the data's last month as a sanity check

Output is data/airports.db, which gets committed. The agent works without ever running this; it
just makes the first demo question fast and lets graders run offline.
"""
