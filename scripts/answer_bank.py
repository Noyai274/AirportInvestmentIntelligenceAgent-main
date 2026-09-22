"""
Owner: Claude. Runs the 22 question-bank tools against the real DB and writes docs/answers-<date>.md —
the actual numbers, one section per question, for a human to judge "is this right for a reason I can
name". No LLM involved. Run: python -m scripts.answer_bank
"""

import json
from datetime import date
from pathlib import Path

from agent import tools as T

OUT = Path(__file__).resolve().parent.parent / "docs" / f"answers-{date.today().isoformat()}.md"

QUESTIONS = [
    ("Q1  New England expansion candidates (exam)", lambda: T.rank_airports(region="New England")),
    ("Q2  Top 10 large hubs by CPI",                lambda: T.rank_airports(tier="Large", top_n=10)),
    ("Q3  Southern medium hubs by passenger growth", lambda: T.rank_airports(region="South", tier="Medium", sort_by="pax_growth_yoy")),
    ("Q4  Most growth vs 2019",                     lambda: T.rank_airports(sort_by="pax_vs_2019")),
    ("Q5  Texas airports with load factor >= 85%",  lambda: T.rank_airports(state="TX", filters={"load_factor_ttm": [">=", 85]})),
    ("Q6  Large hubs still below 2019",             lambda: T.rank_airports(tier="Large", filters={"recovered_2019": ["==", False]}, top_n=40)),
    ("Q7  Up-gauging airports",                     lambda: T.rank_airports(filters={"upgauging": ["==", True]}, sort_by="gauge_growth_yoy", top_n=15)),
    ("Q8  resolve 'LA'",                            lambda: T.resolve_airport("LA")),
    ("Q8  LAX vs SNA congestion (exam)",            lambda: T.compare_airports(["LAX", "SNA"])),
    ("Q9  JFK vs LGA vs EWR",                       lambda: T.compare_airports(["JFK", "LGA", "EWR"])),
    ("Q10 DEN vs PHX",                              lambda: T.compare_airports(["DEN", "PHX"])),
    ("Q11 SNA profile and peer group",              lambda: T.airport_profile("SNA")),
    ("Q12 SFO unmet demand (exam)",                 lambda: T.unmet_demand("SFO")),
    ("Q13 PDX decomposition",                       lambda: T.unmet_demand("PDX")),
    ("Q13 SEA decomposition",                       lambda: T.unmet_demand("SEA")),
    ("Q14 AUS decomposition",                       lambda: T.unmet_demand("AUS")),
    ("Q15 BOS passenger trend, 36 months",          lambda: T.trend("BOS", months=36)),
    ("Q16 ANC long haul (exam)",                    lambda: T.long_haul_share("ANC")),
    ("Q17 Highest international share of seat-miles", lambda: T.rank_airports(sort_by="intl_asm_share")),
    ("Q18 MIA international vs domestic growth",    lambda: T.long_haul_share("MIA")),
    ("Q19 Cargo-dominated airports",                lambda: T.rank_airports(filters={"cargo_dominated": ["==", True]}, include_ineligible=True, sort_by="freight_lbs_ttm")),
    ("Q20 Explain scoring + sensitivity",           lambda: T.explain_scoring(sensitivity=True)),
    ("Q21 BOS profile (provenance)",                lambda: T.airport_profile("BOS")),
]


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    parts = [f"# Answer bank — {date.today().isoformat()}\n",
             "Real tool outputs from data/airports.db. No LLM. Read each and ask: is this right for a reason I can name?\n"]
    for title, fn in QUESTIONS:
        try:
            e = fn()
            parts.append(f"\n## {title}\n\n*source:* `{e['source']}`  \n*method:* {e['method']}\n\n**caveats**\n")
            parts += [f"- {c}\n" for c in e["caveats"]]
            parts.append("\n```json\n" + json.dumps(e["result"], indent=1, default=str)[:6000] + "\n```\n")
        except Exception as exc:
            parts.append(f"\n## {title}\n\nERROR: {type(exc).__name__}: {exc}\n")
    OUT.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
