"""
Owner: Claude. Plan §3.3, §3.5.

The optional warm-up. `python -m ingest.build_db` does three things:
  1. build_states_csv()  — data/airport_states.csv (code,state) from the OurAirports public list.
  2. warm_up()           — pulls every airport's rows since 2019 into data/airports.db in ONE
                           paginated SODA query (not a loop of 1,000+ requests).
  3. sanity_report()     — prints what a human wants to eyeball: row counts for five known airports,
                           the latest month in the data, and the ANC freighter check from plan §3.1.

Both output files are committed to the repo. The agent works without ever running this file; it
makes the first demo question fast and lets graders run offline.
"""

from pathlib import Path

import pandas as pd

from ingest.cache import STATES_PATH, get_monthly_all

# OurAirports: public-domain, community-maintained list of every airport in the world, one CSV at a
# fixed URL. We use three columns: iata_code, iso_country, iso_region ("US-MA" -> "MA").
OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"

# When one IATA code appears on several rows (a closed field, a heliport sharing the code), keep
# the most airport-like one. Lower number wins.
_TYPE_PRIORITY = {"large_airport": 0, "medium_airport": 1, "small_airport": 2,
                  "seaplane_base": 3, "heliport": 4, "balloonport": 5, "closed": 9}

CHECK_AIRPORTS = ["BOS", "LAX", "SNA", "ANC", "SFO"]


# build_states_csv(force=False) -> Path
def build_states_csv(force: bool = False) -> Path:
    if STATES_PATH.exists() and not force:
        return STATES_PATH

    df = pd.read_csv(OURAIRPORTS_URL, dtype=str,
                     usecols=["iata_code", "iso_country", "iso_region", "type"])
    df = df[(df["iso_country"] == "US") & df["iata_code"].notna()]
    df["state"] = df["iso_region"].str.slice(3)
    df = df[df["state"].str.fullmatch(r"[A-Z]{2}")]
    df["_prio"] = df["type"].map(_TYPE_PRIORITY).fillna(8)
    out = (df.sort_values(["iata_code", "_prio"])
             .drop_duplicates("iata_code")
             .rename(columns={"iata_code": "code"})
             [["code", "state"]]
             .sort_values("code"))

    STATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(STATES_PATH, index=False)
    return STATES_PATH


# warm_up(force=False) -> (DataFrame, source)
def warm_up(force: bool = False) -> tuple[pd.DataFrame, str]:
    return get_monthly_all(max_age_days=-1 if force else 30)


# sanity_report(df) -> None
def sanity_report(df: pd.DataFrame) -> None:
    print(f"rows: {len(df):,}   airports: {df['code'].nunique():,}   latest month: {df['month'].max()}")
    for code in CHECK_AIRPORTS:
        sub = df[df["code"] == code]
        state = sub["state"].iloc[0] if len(sub) else "?"
        print(f"  {code}: {len(sub):3d} rows  state={state}")

    last12 = df[df["month"] > df["month"].max()[:4] + "-00"]          # rows in the latest year only
    def seats_per_intl_dep(code: str) -> float:
        s = last12[last12["code"] == code]
        return s["intl_seats"].sum() / max(s["intl_departures"].sum(), 1)
    print(f"  freighter check — intl seats per intl departure: "
          f"ANC={seats_per_intl_dep('ANC'):.0f}  BOS={seats_per_intl_dep('BOS'):.0f}")


if __name__ == "__main__":
    path = build_states_csv()
    n = sum(1 for _ in open(path, encoding="utf-8")) - 1
    print(f"{path.name}: {n} airports")
    df, source = warm_up()
    print(f"airports.db: {source}")
    sanity_report(df)
