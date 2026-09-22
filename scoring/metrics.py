"""
Owner: Amit (drafted by Claude). Plan §4.1 — THE CONTRACT between the data layer and the tools.

build(monthly) turns the cached monthly rows (one per airport-month, from ingest.cache) into one row
per airport with the metrics listed in plan §4.1. Pure pandas: no network, no SQLite, no LLM.

Column names produced here are what agent/tools.py builds against. Once fixed, do not rename them.

Also here, used once each for DESIGN.md: floor_check() and noise_check().
"""

import numpy as np
import pandas as pd

from scoring import config

# Sums over a window. "weighted" columns are Σ(count × average) — the only way to aggregate an
# average correctly across months is to turn it back into a total first.
_SUM_COLS = ["passengers", "seats", "departures",
             "dom_departures", "dom_passengers", "dom_seats",
             "intl_departures", "intl_passengers", "intl_seats", "freight_lbs"]
_WEIGHTED = {                       # new column      = Σ( count column  × average column )
    "asm":            ("seats",           "avg_stage_sm"),          # available seat-miles
    "rpm":            ("passengers",      "avg_pax_distance_sm"),   # revenue passenger-miles
    "dep_miles":      ("departures",      "avg_stage_sm"),
    "dom_dep_miles":  ("dom_departures",  "dom_avg_stage_sm"),
    "intl_dep_miles": ("intl_departures", "intl_avg_stage_sm"),
    "intl_asm":       ("intl_seats",      "intl_avg_stage_sm"),
}


# _anchor(monthly, anchor) -> pd.Timestamp
def _anchor(monthly: pd.DataFrame, anchor: str) -> pd.Timestamp:
    if anchor == "latest":
        return monthly["month"].max()
    return pd.Timestamp(anchor)


# _window_sums(monthly, start, end) -> DataFrame indexed by code
def _window_sums(monthly: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    w = monthly[(monthly["month"] > start) & (monthly["month"] <= end)].copy()
    for new, (count_col, avg_col) in _WEIGHTED.items():
        w[new] = w[count_col] * w[avg_col]
    g = w.groupby("code")
    out = g[_SUM_COLS + list(_WEIGHTED)].sum(min_count=1)
    out["months"] = g["month"].nunique()
    return out


# _hub_tier(pax_ttm) -> Series of tier labels
def _hub_tier(pax_ttm: pd.Series) -> pd.Series:
    share = pax_ttm / pax_ttm.sum()
    tier = pd.Series(config.NON_HUB, index=pax_ttm.index, dtype="object")
    for name, cutoff in reversed(config.HUB_TIERS):     # small → large, so the largest label wins
        tier[share >= cutoff] = name
    return tier


# _ratio(numerator, denominator) -> Series
def _ratio(num: pd.Series, den: pd.Series) -> pd.Series:
    return num / den.where(den > 0)


# build(monthly, anchor=config.TTM_ANCHOR) -> DataFrame indexed by airport code
def build(monthly: pd.DataFrame, anchor: str = config.TTM_ANCHOR) -> pd.DataFrame:
    m = monthly.copy()
    m["month"] = pd.to_datetime(m["month"])
    end = _anchor(m, anchor)
    ttm_start = end - pd.DateOffset(months=12)
    prior_start = end - pd.DateOffset(months=24)
    y = config.BASELINE_YEAR

    ttm = _window_sums(m, ttm_start, end)
    prior = _window_sums(m, prior_start, ttm_start)
    base = _window_sums(m, pd.Timestamp(f"{y-1}-12-01"), pd.Timestamp(f"{y}-12-01"))

    # identity: first non-null name/city/state per airport
    ident = (m.sort_values("month")
              .groupby("code")[["airport_name", "city", "state"]]
              .last())

    out = pd.DataFrame(index=ttm.index)
    out[["airport_name", "city", "state"]] = ident.reindex(out.index)
    out["ttm_end"] = end.strftime("%Y-%m")
    out["months_ttm"] = ttm["months"]
    out["months_prior"] = prior["months"].reindex(out.index).fillna(0).astype(int)
    out["months_2019"] = base["months"].reindex(out.index).fillna(0).astype(int)

    # size
    out["pax_ttm"] = ttm["passengers"]
    out["seats_ttm"] = ttm["seats"]
    out["dep_ttm"] = ttm["departures"]
    out["freight_lbs_ttm"] = ttm["freight_lbs"]
    out["hub_tier"] = _hub_tier(out["pax_ttm"].fillna(0))

    # growth
    p, b = prior.reindex(out.index), base.reindex(out.index)
    out["pax_growth_yoy"] = _ratio(ttm["passengers"], p["passengers"]) - 1
    out["seat_growth_yoy"] = _ratio(ttm["seats"], p["seats"]) - 1
    out["dep_growth_yoy"] = _ratio(ttm["departures"], p["departures"]) - 1
    base_pax = b["passengers"].where(b["passengers"] >= config.MIN_BASELINE_PAX)   # tiny 2019 base → NaN
    out["pax_2019"] = b["passengers"]
    out["intl_pax_growth_yoy"] = _ratio(ttm["intl_passengers"], p["intl_passengers"]) - 1
    out["dom_pax_growth_yoy"] = _ratio(ttm["dom_passengers"], p["dom_passengers"]) - 1
    out["pax_vs_2019"] = _ratio(ttm["passengers"], base_pax) - 1
    out["recovered_2019"] = (out["pax_vs_2019"] >= config.RECOVERY_THRESHOLD) | out["pax_vs_2019"].isna()

    # utilisation — load factor in PERCENT, seat-based, to match BTS's published number
    out["load_factor_ttm"] = 100 * _ratio(ttm["passengers"], ttm["seats"])
    lf_prior = 100 * _ratio(p["passengers"], p["seats"])
    out["lf_trend"] = out["load_factor_ttm"] - lf_prior
    out["pax_per_departure"] = _ratio(ttm["passengers"], ttm["departures"])
    gauge_prior = _ratio(p["passengers"], p["departures"])
    out["gauge_growth_yoy"] = _ratio(out["pax_per_departure"], gauge_prior) - 1   # up-gauging: bigger planes, same flights
    out["upgauging"] = ((out["dep_growth_yoy"] <= config.UPGAUGING_MAX_DEP_GROWTH)
                        & (out["pax_growth_yoy"] >= config.UPGAUGING_MIN_PAX_GROWTH)).fillna(False)
    g = out["pax_growth_yoy"]
    out["direction"] = np.select([g >= config.DIRECTION_BAND, g <= -config.DIRECTION_BAND], ["expanding", "contracting"], "flat")
    out.loc[g.isna(), "direction"] = None

    # industry units
    out["asm_ttm"] = ttm["asm"]
    out["rpm_ttm"] = ttm["rpm"]

    # long-haul proxies (plan §4.2)
    out["intl_dep_share"] = _ratio(ttm["intl_departures"], ttm["departures"])
    out["intl_asm_share"] = _ratio(ttm["intl_asm"], ttm["asm"])
    out["avg_stage_sm"] = _ratio(ttm["dep_miles"], ttm["departures"])
    out["dom_avg_stage_sm"] = _ratio(ttm["dom_dep_miles"], ttm["dom_departures"])
    out["intl_avg_stage_sm"] = _ratio(ttm["intl_dep_miles"], ttm["intl_departures"])
    out["seats_per_intl_dep"] = _ratio(ttm["intl_seats"], ttm["intl_departures"])
    out["cargo_dominated"] = ((out["seats_per_intl_dep"] < config.CARGO_SEATS_PER_DEP_THRESHOLD)
                              & (ttm["intl_departures"] >= config.CARGO_MIN_INTL_DEPARTURES)).fillna(False)

    out.index.name = "code"
    return out.sort_index()


# floor_check(monthly) -> DataFrame
def floor_check(monthly: pd.DataFrame) -> pd.DataFrame:
    mt = build(monthly)
    mt = mt[(mt["months_ttm"] == 12) & (mt["months_prior"] == 12) & (mt["pax_ttm"] > 0)]
    edges = [0, 10e3, 30e3, 100e3, 300e3, 1e6, 3e6, 10e6, 1e9]
    labels = ["<10k", "10–30k", "30–100k", "100–300k", "300k–1M", "1–3M", "3–10M", ">10M"]
    bucket = pd.cut(mt["pax_ttm"], bins=edges, labels=labels)
    g = mt.groupby(bucket, observed=True)["pax_growth_yoy"]
    return pd.DataFrame({"airports": g.size(), "growth_std": g.std(), "growth_median": g.median()})


# noise_check(monthly) -> dict
def noise_check(monthly: pd.DataFrame) -> dict:
    mt = build(monthly)
    large = mt[(mt["hub_tier"] == "Large") & (mt["months_prior"] == 12)]
    return {
        "large_hubs": int(len(large)),
        "median_abs_yoy_change": float(large["pax_growth_yoy"].abs().median()),
        "threshold_in_use": config.RECOVERY_THRESHOLD,
    }
