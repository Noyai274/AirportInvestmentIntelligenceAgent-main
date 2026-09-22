"""
Owner: Amit (drafted by Claude). Plan §4.2. The Capacity Pressure Index.

CPI measures how hard current demand presses on an airport's existing capacity, and whether that
pressure is rising. Four ratio components, each a within-hub-tier z-score, weighted by config.WEIGHTS:

    CPI = w_growth · z(growth)
        + w_load_factor · z(load_factor_ttm)
        + w_lf_trend · z(lf_trend)
        + w_demand_minus_supply · z(growth − seat_growth_yoy)

Size is deliberately NOT a component (see config.WEIGHTS): it segments (tiers) and filters (floor).

`growth` is pax_growth_yoy by default (baseline="yoy") or pax_vs_2019 (baseline="2019"); nothing
else changes between the two, so the rankings are directly comparable.

No I/O, no LLM, no network. Takes the metrics frame from scoring.metrics.build, returns frames/dicts.
"""

import numpy as np
import pandas as pd

from scoring import config

COMPONENTS = ["growth", "load_factor", "lf_trend", "demand_minus_supply"]


# _components(metrics, baseline) -> DataFrame with the four raw component columns
def _components(metrics: pd.DataFrame, baseline: str) -> pd.DataFrame:
    if baseline not in ("yoy", "2019"):
        raise ValueError("baseline must be 'yoy' or '2019'")
    growth = metrics["pax_growth_yoy"] if baseline == "yoy" else metrics["pax_vs_2019"]
    return pd.DataFrame({
        "growth": growth,
        "load_factor": metrics["load_factor_ttm"],
        "lf_trend": metrics["lf_trend"],
        "demand_minus_supply": growth - metrics["seat_growth_yoy"],
    }, index=metrics.index)


# _eligible(metrics, baseline) -> boolean Series
def _eligible(metrics: pd.DataFrame, baseline: str) -> pd.Series:
    ok = metrics["pax_ttm"] >= config.RANKING_FLOOR_PAX_TTM
    if config.REQUIRE_COMPLETE_WINDOWS:
        ok &= (metrics["months_ttm"] == 12) & (metrics["months_prior"] == 12)
    if baseline == "2019":
        ok &= metrics["months_2019"] == 12
    return ok


# _zscore_within_tier(values, tiers) -> Series
def _zscore_within_tier(values: pd.Series, tiers: pd.Series) -> pd.Series:
    g = values.groupby(tiers)
    mean, std = g.transform("mean"), g.transform(lambda s: s.std(ddof=0))
    z = (values - mean) / std.where(std > 1e-9)      # tiny std = "all the same" → z = 0, not noise
    z = z.fillna(0.0)
    if config.Z_CLIP is not None:
        z = z.clip(-config.Z_CLIP, config.Z_CLIP)
    return z


# score(metrics, weights=config.WEIGHTS, baseline="yoy") -> DataFrame
def score(metrics: pd.DataFrame, weights: dict | None = None, baseline: str = "yoy") -> pd.DataFrame:
    weights = weights or config.WEIGHTS
    if set(weights) != set(COMPONENTS):
        raise ValueError(f"weights must have exactly these keys: {COMPONENTS}")
    if abs(sum(weights.values()) - 1.0) > 1e-6:
        raise ValueError(f"weights must sum to 1, got {sum(weights.values()):.4f}")

    out = metrics.copy()
    raw = _components(metrics, baseline)
    elig = _eligible(metrics, baseline) & raw.notna().all(axis=1)
    out["eligible"] = elig
    out["baseline"] = baseline

    for c in COMPONENTS:
        out[f"raw_{c}"] = raw[c]
        z = pd.Series(np.nan, index=out.index)
        z[elig] = _zscore_within_tier(raw.loc[elig, c], out.loc[elig, "hub_tier"])
        out[f"z_{c}"] = z

    # not a CPI component — a within-tier z for the up-gauging narrative in congestion()
    zg = pd.Series(np.nan, index=out.index)
    zg[elig] = _zscore_within_tier(metrics.loc[elig, "gauge_growth_yoy"], out.loc[elig, "hub_tier"])
    out["z_gauge_growth"] = zg

    out["cpi"] = sum(weights[c] * out[f"z_{c}"] for c in COMPONENTS)
    out["rank_in_tier"] = (out[elig].groupby("hub_tier")["cpi"]
                              .rank(ascending=False, method="min")
                              .reindex(out.index).astype("Int64"))
    out["rank_overall"] = (out.loc[elig, "cpi"].rank(ascending=False, method="min")
                              .reindex(out.index).astype("Int64"))
    return out


# decompose(scored, code, weights=config.WEIGHTS) -> dict
def decompose(scored: pd.DataFrame, code: str, weights: dict | None = None) -> dict:
    weights = weights or config.WEIGHTS
    row = scored.loc[code]
    parts = []
    for c in COMPONENTS:
        z = row[f"z_{c}"]
        parts.append({"component": c, "raw": _clean(row[f"raw_{c}"]), "z": _clean(z),
                      "weight": weights[c], "contribution": _clean(weights[c] * z) if pd.notna(z) else None})
    parts.sort(key=lambda p: -(p["contribution"] or 0))
    return {
        "code": code, "hub_tier": row["hub_tier"], "eligible": bool(row["eligible"]),
        "cpi": _clean(row["cpi"]), "rank_in_tier": _clean(row["rank_in_tier"]),
        "tier_size": int((scored["hub_tier"] == row["hub_tier"]).sum()),
        "baseline": row["baseline"], "ttm_end": row["ttm_end"],
        "pax_vs_2019": _clean(row["pax_vs_2019"]), "recovered_2019": bool(row["recovered_2019"]),
        "components": parts,
    }


# congestion(scored, code) -> dict
def congestion(scored: pd.DataFrame, code: str) -> dict:
    row = scored.loc[code]
    return {
        "code": code, "hub_tier": row["hub_tier"], "ttm_end": row["ttm_end"],
        "load_factor_pct": _clean(row["load_factor_ttm"]), "z_load_factor": _clean(row["z_load_factor"]),
        "pax_growth_yoy": _clean(row["pax_growth_yoy"]), "seat_growth_yoy": _clean(row["seat_growth_yoy"]),
        "demand_minus_supply": _clean(row["raw_demand_minus_supply"]),
        "z_demand_minus_supply": _clean(row["z_demand_minus_supply"]),
        "dep_growth_yoy": _clean(row["dep_growth_yoy"]),
        "pax_per_departure": _clean(row["pax_per_departure"]),
        "gauge_growth_yoy": _clean(row["gauge_growth_yoy"]),
        "z_gauge_growth": _clean(row.get("z_gauge_growth")),
        "upgauging": _upgauging(row),
    }


# _upgauging(row) -> dict
def _upgauging(row: pd.Series) -> dict:
    """Departures flat AND passengers up ⇒ airlines are adding seats per flight, not flights —
    the signature of a per-movement (slot/gate) constraint. Returns the flag and one sentence."""
    dep, pax = row["dep_growth_yoy"], row["pax_growth_yoy"]
    if pd.isna(dep) or pd.isna(pax):
        return {"flag": False, "note": "insufficient data for the up-gauging check"}
    flag = bool(row["upgauging"])
    if flag:
        note = (f"departures {dep:+.1%} while passengers {pax:+.1%}: airlines are up-gauging "
                f"(seats per flight {row['gauge_growth_yoy']:+.1%}), a pattern consistent with a "
                f"per-movement constraint such as slots or gates — not proof of one")
    else:
        note = "no up-gauging signature: departures and passengers are moving together"
    return {"flag": flag, "note": note}


# sensitivity(metrics, codes, weight_sets=config.SENSITIVITY_SETS, baseline="yoy") -> DataFrame
def sensitivity(metrics: pd.DataFrame, codes: list[str], weight_sets: dict | None = None,
                baseline: str = "yoy") -> pd.DataFrame:
    weight_sets = weight_sets or config.SENSITIVITY_SETS
    table = {name: score(metrics, w, baseline).loc[codes, "rank_in_tier"] for name, w in weight_sets.items()}
    out = pd.DataFrame(table)
    out.insert(0, "hub_tier", metrics.loc[codes, "hub_tier"])
    return out


# definition() -> dict
def definition() -> dict:
    return {
        "kpi": "Capacity Pressure Index (CPI)",
        "measures": "how hard current demand presses on an airport's existing capacity, and whether that pressure is rising",
        "formula": "CPI = Σ weight_k · z_k, z computed within hub tier, clipped to ±%s" % config.Z_CLIP,
        "components": {
            "growth": "passenger growth, trailing 12 months vs prior 12 (or vs 2019 under baseline='2019')",
            "load_factor": "passengers / seats over the trailing 12 months, percent (BTS's basis)",
            "lf_trend": "load factor now minus load factor a year ago, percentage points",
            "demand_minus_supply": "passenger growth minus seat growth — demand outrunning supply",
        },
        "not_a_component": "size — passengers set the hub tier and the ranking floor and are reported with every answer, but never add to the score",
        "weights": config.WEIGHTS,
        "sensitivity_sets": config.SENSITIVITY_SETS,
        "hub_tiers": {name: f">= {cut:.2%} of US passengers" for name, cut in config.HUB_TIERS} | {config.NON_HUB: "below"},
        "ranking_floor_pax_ttm": config.RANKING_FLOOR_PAX_TTM,
        "recovery_threshold": config.RECOVERY_THRESHOLD,
        "load_factor_basis": config.LOAD_FACTOR_BASIS,
        "long_haul_definition": config.LONG_HAUL_DEFINITION,
        "baselines": ["yoy (default)", "2019 (on request — swaps the growth term for pax_vs_2019)"],
    }


# _clean(x) -> float | int | None
def _clean(x):
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if x is None or (isinstance(x, float) and np.isnan(x)) or pd.isna(x):
        return None
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return round(float(x), 4)
    return x
