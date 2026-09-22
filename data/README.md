<!--
Owner: Claude (both files are produced by ingest/). Plan §3.3, §3.4, §3.5.

airports.db
  SQLite. One table, `monthly`: one row per (airport code, month), Jan 2019 → latest, schema in
  plan §3.4. Column names are the sane renames from cache.py, not BTS's originals. Each row carries
  fetched_at. COMMITTED to the repo so graders run without network; README states its date.
  Produced by `python -m ingest.build_db` (warm-up) or lazily by cache.get_monthly().

airport_states.csv
  Static lookup: IATA code → US state, for the region questions (New England = CT, MA, ME, NH,
  RI, VT). Built by ingest/build_db.py:build_states_csv() from OurAirports
  (https://davidmegginson.github.io/ourairports-data/airports.csv, public domain): US rows with an
  IATA code, state = iso_region minus the "US-" prefix. Committed. r495-tyji has no state field,
  which is the only reason this file exists. NOT stored in airports.db — cache.py joins it at read time.
-->
