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

# One-row bookkeeping table. `full_fetch_at` is written only when a WHOLE-TABLE fetch completes, so
# get_monthly_all can tell a complete snapshot from a cache that merely holds a few airports fetched
# individually. Age alone cannot tell those apart (one airport fetched today looks "fresh").
META_DDL = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);"


# _connect() -> sqlite3.Connection
def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(DDL)
    conn.execute(META_DDL)
    return conn


# _states() -> dict[str, str]
def _states() -> dict[str, str]:
    if not STATES_PATH.exists():
        return {}
    s = pd.read_csv(STATES_PATH, dtype=str)
    return dict(zip(s["code"], s["state"]))


# _with_state(df) -> DataFrame
def _with_state(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["state"] = df["code"].map(_states())
    return df


# _to_frame(rows) -> DataFrame
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


# _upsert(conn, df) -> None
def _upsert(conn: sqlite3.Connection, df: pd.DataFrame) -> None:
    if df.empty:
        return
    placeholders = ", ".join("?" for _ in COLUMNS)
    sql = f"INSERT OR REPLACE INTO monthly ({', '.join(COLUMNS)}) VALUES ({placeholders})"
    records = df.astype(object).where(df.notna(), None).itertuples(index=False, name=None)
    conn.executemany(sql, records)
    conn.commit()


# _read(conn, since, code) -> DataFrame
def _read(conn: sqlite3.Connection, since: str, code: str | None) -> pd.DataFrame:
    sql = "SELECT * FROM monthly WHERE month >= ?"
    params: list = [since]
    if code is not None:
        sql += " AND code = ?"
        params.append(code)
    return pd.read_sql(sql + " ORDER BY code, month", conn, params=params)


# _is_fresh(df, max_age_days) -> bool
def _is_fresh(df: pd.DataFrame, max_age_days: int) -> bool:
    if df.empty or max_age_days < 0:
        return False
    oldest = datetime.fromisoformat(df["fetched_at"].min())
    return datetime.now() - oldest <= timedelta(days=max_age_days)


# _validate_code(code) -> str
def _validate_code(code: str) -> str:
    code = code.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3}", code):
        raise ValueError(f"not an airport code: {code!r}")
    return code


# _full_fetch_at(conn) -> datetime | None
def _full_fetch_at(conn: sqlite3.Connection):
    row = conn.execute("SELECT value FROM meta WHERE key = 'full_fetch_at'").fetchone()
    return datetime.fromisoformat(row[0]) if row else None


# _mark_full_fetch(conn) -> None
def _mark_full_fetch(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('full_fetch_at', ?)",
                 (datetime.now().isoformat(timespec="seconds"),))
    conn.commit()


# _fetch_live(conn, since, code) -> DataFrame
def _fetch_live(conn: sqlite3.Connection, since: str, code: str | None) -> pd.DataFrame:
    where = f"reporting_month >= '{since}'"
    if code is not None:
        where += f" AND origin_airport_code='{code}'"
    rows = fetch(DATASET, **{"$where": where, "$select": ",".join(RENAME_MAP)})
    df = _to_frame(rows)
    _upsert(conn, df)
    if code is None:
        _mark_full_fetch(conn)
    return df


# get_monthly(code, since="2019-01-01", max_age_days=30) -> (DataFrame, source)
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


# get_monthly_all(since="2019-01-01", max_age_days=30) -> (DataFrame, source)
def get_monthly_all(since: str = "2019-01-01", max_age_days: int = 30) -> tuple[pd.DataFrame, str]:
    conn = _connect()
    try:
        full = _full_fetch_at(conn)
        if (full is not None and max_age_days >= 0
                and datetime.now() - full <= timedelta(days=max_age_days)):
            df = _read(conn, since, None)
            return _with_state(df), f"cache, fetched {full.date().isoformat()}"
        return _with_state(_fetch_live(conn, since, None)), "data.bts.gov live"
    finally:
        conn.close()


# snapshot_stamp() -> str
def snapshot_stamp() -> str:
    """Cheap identity of the current cache contents: the full-fetch timestamp plus the row count.
    Callers (agent.tools) key their in-memory scored tables on this instead of re-reading 80k rows
    on every tool call."""
    conn = _connect()
    try:
        full = _full_fetch_at(conn)
        n = conn.execute("SELECT COUNT(*) FROM monthly").fetchone()[0]
        return f"{full.isoformat(timespec='seconds') if full else 'none'}|{n}"
    finally:
        conn.close()


# refresh(code) -> (DataFrame, source)
def refresh(code: str) -> tuple[pd.DataFrame, str]:
    return get_monthly(code, max_age_days=-1)
