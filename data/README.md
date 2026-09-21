<!--
Owner: Claude (both files are produced by ingest/). Plan §3.3, §3.4, §3.5.

airports.db
  SQLite. One table, `monthly`: one row per (airport code, month), Jan 2019 → latest, schema in
  plan §3.4. Column names are the sane renames from cache.py, not BTS's originals. Each row carries
  fetched_at. COMMITTED to the repo so graders run without network; README states its date.
  Produced by `python -m ingest.build_db` (warm-up) or lazily by cache.get_monthly().

airport_states.csv
  Static lookup: IATA code → US state, for the region questions (New England = CT, MA, ME, NH,
  RI, VT). Built once from a public airport list during ingest; committed. r495-tyji has no state
  field, which is the only reason this file exists.
-->
