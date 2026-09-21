"""
Owner: Claude (RENAME_MAP and constants by Amit). Plan §3.4, §3.5.
The cache-through layer between the tools and BTS.

This is what makes "the agent uses public APIs" literally true (plan §0): tools call this, this
calls SODA only when needed. Public surface: get_monthly, get_monthly_all, refresh. Everything
prefixed with _ is a private helper.

Rule of the file: what comes from the source (BTS) is STORED; what we add (state) is JOINED at read.
"""

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ingest.soda import fetch

# Paths are anchored to this file, not the working directory, so the DB is found whether the
# caller is streamlit (project root), pytest (tests/), or a notebook.
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "airports.db"
STATES_PATH = ROOT / "data" / "airport_states.csv"
DATASET = "r495-tyji"

# BTS column name -> ours. Anything not listed here is dropped. This dict is the lineage record:
# every column in `monthly` traces to exactly one BTS field.
RENAME_MAP = {
    "origin_airport_code":         "code",
    "origin_airport_name":         "airport_name",
    "origin_city_name":            "city",
    "reporting_month":             "month",
    "total_departures":            "departures",
    "total_passengers":            "passengers",
    "total_seats":                 "seats",
    "total_load_factor":           "load_factor",
    "total_distance_flight_sm":    "avg_stage_sm",
    "total_distance_passenger":    "avg_pax_distance_sm",
    "domestic_departures":         "dom_departures",
    "domestic_passengers":         "dom_passengers",
    "domestic_seats":              "dom_seats",
    "domestic_distance_flight":    "dom_avg_stage_sm",
    "outbound_international":      "intl_departures",
    "outbound_international_1":    "intl_passengers",
    "outbound_international_seats": "intl_seats",
    "outbound_international_3":    "intl_avg_stage_sm",
    "total_freight_lbs":           "freight_lbs",
}

TEXT_COLS = ["code", "airport_name", "city", "month", "fetched_at"]
NUMERIC_COLS = [c for c in RENAME_MAP.values() if c not in TEXT_COLS]
COLUMNS = ["code", "airport_name", "city", "month"] + NUMERIC_COLS + ["fetched_at"]
# `state` is NOT stored: it is our lookup, not a BTS fact. It is joined at read time (see _with_state)
# so rows cached before airport_states.csv existed still get a state, and a corrected lookup
# applies everywhere without refetching.

# SQLite has no date type. `month` and `fetched_at` are ISO text (YYYY-MM-DD / YYYY-MM-DDTHH:MM:SS),
# which sorts and compares correctly as strings.
DDL = f"""
CREATE TABLE IF NOT EXISTS monthly (
  code TEXT NOT NULL, airport_name TEXT, city TEXT,
  month TEXT NOT NULL,
  {", ".join(f"{c} REAL" for c in NUMERIC_COLS)},
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (code, month)
);
"""


# ──────────────────────────────────────────────────────────────────────────────
# _connect() -> sqlite3.Connection
#
# Opens data/airports.db (creating the data/ folder and the file if missing), runs the CREATE TABLE
# IF NOT EXISTS statement — a no-op when the table already exists — and returns the connection.
# Every public function starts here, so nothing ever assumes the DB was set up in advance.
# Callers must close the connection (they do, in a `finally`).
# ──────────────────────────────────────────────────────────────────────────────
def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(DDL)
    return conn


# ──────────────────────────────────────────────────────────────────────────────
# _states() -> dict[str, str]
#
# Reads data/airport_states.csv (two columns: code,state — written by build_db.build_states_csv)
# and returns it as a dictionary {"BOS": "MA", "LAX": "CA", ...}. If the file does not exist yet,
# returns an empty dict, so the rest of the pipeline works before the lookup has been built.
# ──────────────────────────────────────────────────────────────────────────────
def _states() -> dict[str, str]:
    if not STATES_PATH.exists():
        return {}
    s = pd.read_csv(STATES_PATH, dtype=str)
    return dict(zip(s["code"], s["state"]))


# ──────────────────────────────────────────────────────────────────────────────
# _with_state(df) -> DataFrame
#
# Takes a frame WITHOUT a state column and returns a copy WITH one: for each row, look up its
# `code` in the _states() dictionary. Codes not in the lookup (tiny fields without an IATA code)
# get NaN — no error. This runs at read time on every public return path, which is how rows
# cached before the CSV existed still come back with a state.
# ──────────────────────────────────────────────────────────────────────────────
def _with_state(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["state"] = df["code"].map(_states())
    return df


# ──────────────────────────────────────────────────────────────────────────────
# _to_frame(rows) -> DataFrame
#
# Turns BTS's raw answer (a list of dicts, every value a string) into our clean, typed table:
#   1. DataFrame from the rows.
#   2. Keep only the columns named in RENAME_MAP and rename them (outbound_international_3 ->
#      intl_avg_stage_sm). Everything unmapped is dropped here.
#   3. Convert every numeric column from text to numbers. errors="coerce" turns a blank or
#      malformed value into NaN instead of raising. This is the step that makes `passengers > seats`
#      a numeric comparison rather than an alphabetical one.
#   4. Trim `month` from "2026-04-01T00:00:00.000" to "2026-04-01".
#   5. Stamp `fetched_at` = now, ISO text — the column the freshness rule reads later.
#   6. Return the columns in COLUMNS order so the frame lines up with the table.
# An empty input returns an empty frame with the right columns, so callers never special-case it.
# ──────────────────────────────────────────────────────────────────────────────
def _to_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows)
    df = df[[c for c in RENAME_MAP if c in df.columns]].rename(columns=RENAME_MAP)
    for c in NUMERIC_COLS:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df["month"] = df["month"].str.slice(0, 10)
    df["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    return df[COLUMNS]


# ──────────────────────────────────────────────────────────────────────────────
# _upsert(conn, df) -> None
#
# Writes the frame into the `monthly` table with INSERT OR REPLACE, one row at a time via
# executemany. Because the primary key is (code, month), writing BOS April 2026 a second time
# overwrites the first — a refetch picks up BTS revisions instead of creating a duplicate month.
# The astype/where line converts pandas NaN into Python None first, because SQLite understands
# NULL but not NaN. Commits at the end; an empty frame is a no-op.
# ──────────────────────────────────────────────────────────────────────────────
def _upsert(conn: sqlite3.Connection, df: pd.DataFrame) -> None:
    if df.empty:
        return
    placeholders = ", ".join("?" for _ in COLUMNS)
    sql = f"INSERT OR REPLACE INTO monthly ({', '.join(COLUMNS)}) VALUES ({placeholders})"
    records = df.astype(object).where(df.notna(), None).itertuples(index=False, name=None)
    conn.executemany(sql, records)
    conn.commit()


# ──────────────────────────────────────────────────────────────────────────────
# _read(conn, since, code) -> DataFrame
#
# SELECT from `monthly`: months on or after `since`, and one airport if `code` is given or all
# airports if it is None. Values go in as `?` placeholders, never string-formatted — that is the
# safe way to put values into SQL. Sorted by code then month. Returns an empty frame when nothing
# matches (which is what triggers a live fetch upstream).
# ──────────────────────────────────────────────────────────────────────────────
def _read(conn: sqlite3.Connection, since: str, code: str | None) -> pd.DataFrame:
    sql = "SELECT * FROM monthly WHERE month >= ?"
    params: list = [since]
    if code is not None:
        sql += " AND code = ?"
        params.append(code)
    return pd.read_sql(sql + " ORDER BY code, month", conn, params=params)


# ──────────────────────────────────────────────────────────────────────────────
# _is_fresh(df, max_age_days) -> bool
#
# The yes/no at the heart of the cache: "can I use these cached rows, or must I go to BTS?"
# Fresh means: rows exist AND the OLDEST fetched_at among them is within max_age_days of now.
# Oldest rather than newest so the all-airports cache is never a patchwork of ages.
# max_age_days < 0 is defined as "never fresh" — that single rule is how refresh() forces a live
# call without any extra code.
# ──────────────────────────────────────────────────────────────────────────────
def _is_fresh(df: pd.DataFrame, max_age_days: int) -> bool:
    if df.empty or max_age_days < 0:
        return False
    oldest = datetime.fromisoformat(df["fetched_at"].min())
    return datetime.now() - oldest <= timedelta(days=max_age_days)


# ──────────────────────────────────────────────────────────────────────────────
# _validate_code(code) -> str
#
# The guard at the boundary. Strips whitespace, uppercases, and requires exactly three letters or
# digits (BTS uses codes like "01A" for tiny fields, hence digits). Anything else raises ValueError.
# It matters because later `code` arrives from the LLM's tool call and is interpolated into a SoQL
# string; validating once here is what keeps a malformed argument from becoming a malformed query.
# ──────────────────────────────────────────────────────────────────────────────
def _validate_code(code: str) -> str:
    code = code.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3}", code):
        raise ValueError(f"not an airport code: {code!r}")
    return code


# ──────────────────────────────────────────────────────────────────────────────
# _fetch_live(conn, since, code) -> DataFrame
#
# The cache-miss path, packaged as one step so both public functions share it:
#   build the SoQL $where (reporting_month >= since, plus the airport filter if any) and a $select
#   listing only the BTS fields we keep (a much smaller payload than the full row) → soda.fetch →
#   _to_frame → _upsert → return the clean frame.
# ──────────────────────────────────────────────────────────────────────────────
def _fetch_live(conn: sqlite3.Connection, since: str, code: str | None) -> pd.DataFrame:
    where = f"reporting_month >= '{since}'"
    if code is not None:
        where += f" AND origin_airport_code='{code}'"
    rows = fetch(DATASET, **{"$where": where, "$select": ",".join(RENAME_MAP)})
    df = _to_frame(rows)
    _upsert(conn, df)
    return df


# ──────────────────────────────────────────────────────────────────────────────
# get_monthly(code, since="2019-01-01", max_age_days=30) -> (DataFrame, source)
#
# PUBLIC. One airport's monthly rows. The sentence the whole file exists for:
#   validate the code → open the DB → read what is cached → if fresh, return it tagged
#   "cache, fetched <date>" → otherwise fetch live, store, return tagged "data.bts.gov live".
# Both return paths go through _with_state so the caller always gets a `state` column.
# The connection is closed in `finally` so it is released even if something raises halfway.
# `source` is the second return value; the UI shows it under every answer.
# ──────────────────────────────────────────────────────────────────────────────
def get_monthly(code: str, since: str = "2019-01-01", max_age_days: int = 30) -> tuple[pd.DataFrame, str]:
    code = _validate_code(code)
    conn = _connect()
    try:
        df = _read(conn, since, code)
        if _is_fresh(df, max_age_days):
            return _with_state(df), f"cache, fetched {df['fetched_at'].max()[:10]}"
        return _with_state(_fetch_live(conn, since, code)), "data.bts.gov live"
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# get_monthly_all(since="2019-01-01", max_age_days=30) -> (DataFrame, source)
#
# PUBLIC. The same decision for every airport at once — what the ranking tools need to compute
# z-scores within hub tiers. If any row is stale, the whole table is refetched in one paginated
# SODA query (a few 50k-row pages) rather than airport by airport, so the cache is never a mix
# of ages and BTS sees a handful of requests instead of a thousand.
# ──────────────────────────────────────────────────────────────────────────────
def get_monthly_all(since: str = "2019-01-01", max_age_days: int = 30) -> tuple[pd.DataFrame, str]:
    conn = _connect()
    try:
        df = _read(conn, since, None)
        if _is_fresh(df, max_age_days):
            return _with_state(df), f"cache, fetched {df['fetched_at'].max()[:10]}"
        return _with_state(_fetch_live(conn, since, None)), "data.bts.gov live"
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# refresh(code) -> (DataFrame, source)
#
# PUBLIC. Force a live fetch for one airport, bypassing the cache: max_age_days=-1 is never fresh,
# so get_monthly always takes the live path. Backs airport_profile(refresh=True) — the demo moment
# "pull the latest for SFO" that proves the API path is real.
# ──────────────────────────────────────────────────────────────────────────────
def refresh(code: str) -> tuple[pd.DataFrame, str]:
    return get_monthly(code, max_age_days=-1)
