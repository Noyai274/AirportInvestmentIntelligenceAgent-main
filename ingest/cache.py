"""
Owner: Claude. Plan §3.4, §3.5. The cache-through layer between the tools and BTS.

This is what makes "the agent uses public APIs" literally true (plan §0): tools call this, this
calls SODA only when needed.

Exposes:
  get_monthly(code, since="2019-01-01", max_age_days=30) -> (pandas.DataFrame, source: str)
    - if SQLite has rows for `code` and the newest fetched_at is within max_age_days:
        return them, source = "cache, fetched <date>"
    - else: soda.fetch(r495-tyji, origin_airport_code=code, reporting_month >= since),
        rename columns (RENAME_MAP below), upsert into `monthly`, return, source = "data.bts.gov live"
  get_monthly_all(since=..., max_age_days=...) -> (DataFrame, source)
    - same logic on the whole table; used by ranking tools
  refresh(code) -> forces a live fetch regardless of age (backs airport_profile(refresh=True))

Also owns:
  RENAME_MAP — BTS's column names (total_departures, outbound_international_3, ...) → ours
    (departures, intl_avg_stage_sm, ...). Documented inline so lineage is traceable.
  the `monthly` table DDL from plan §3.4 (created if missing).
  the airport → state join from data/airport_states.csv.

Ingest-time check (plan §3.1): on the ANC row, intl_seats / intl_departures should be far below a
passenger aircraft's seat count — confirms freighters are in the departure count.
"""
