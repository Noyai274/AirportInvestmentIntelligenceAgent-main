"""
Owner: Claude. Plan §6. Streamlit chat UI — one page.

    streamlit run app.py

Sidebar: data as-of, CPI weights and tier cut-offs (from config), saved conversations, running cost.
Under every assistant answer an expander shows the tool calls: name, arguments, source line (cache vs
live), method, caveats, raw result — plus the tokens and estimated cost of that answer. That expander is
the demo of "not only LLM output" and "uses public APIs". No scoring logic here.

Conversations are saved as JSON in data/chats/ (git-ignored) after every turn and can be reopened from
the sidebar. Everything the model saw (history) and everything the user saw (display) is stored.
"""

import json
import time
from pathlib import Path

import streamlit as st

from agent import tools as T
from agent.agent import MODEL, run_turn
from scoring import config

st.set_page_config(page_title="Airport Investment Intelligence Agent", page_icon="✈", layout="wide")

SAMPLE_QUESTIONS = [
    "Which airports in New England are strong candidates for terminal expansion?",
    "Compare LA and Santa Ana airport congestion levels.",
    "What is the percentage of long haul flights out of Anchorage airport?",
    "What is the unmet flight demand in SFO airport and why?",
]
CHAT_DIR = Path(__file__).parent / "data" / "chats"
CHAT_DIR.mkdir(parents=True, exist_ok=True)
EMPTY_USAGE = {"api_calls": 0, "input_tokens": 0, "output_tokens": 0,
               "cache_write_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.0}


# ── conversation persistence ──────────────────────────────────────────────────
def _save_chat() -> None:
    if not st.session_state.display:
        return
    cid = st.session_state.setdefault("chat_id", time.strftime("%Y%m%d-%H%M%S"))
    doc = {"id": cid, "title": st.session_state.display[0]["text"][:70], "model": MODEL,
           "history": st.session_state.history, "display": st.session_state.display,
           "usage_total": st.session_state.usage_total}
    (CHAT_DIR / f"{cid}.json").write_text(json.dumps(doc, default=str, indent=1), encoding="utf-8")


def _load_chat(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    st.session_state.update(chat_id=doc["id"], history=doc["history"], display=doc["display"],
                            usage_total=doc.get("usage_total", dict(EMPTY_USAGE)))


def _fmt_usage(u: dict) -> str:
    cost = f"≈ ${u['cost_usd']:.4f}" if u.get("cost_usd") is not None else "cost n/a"
    return (f"{cost} · {u['input_tokens']:,} in / {u['output_tokens']:,} out · "
            f"{u['cache_read_tokens']:,} cached · {u['api_calls']} API call(s)")


# ── state ─────────────────────────────────────────────────────────────────────
st.session_state.setdefault("history", [])       # API-shaped messages (what the model sees)
st.session_state.setdefault("display", [])       # [{role, text, trace, usage}] (what the user sees)
st.session_state.setdefault("usage_total", dict(EMPTY_USAGE))

# ── sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("Airport Investment Intelligence Agent")
    try:
        info = T.explain_scoring()["result"]["data"]
        st.caption(f"Data: BTS T-100 via SODA · latest month **{info['ttm_end']}** · "
                   f"{info['airports_in_data']:,} airports, {info['airports_scored']} scored")
    except Exception as exc:                          # DB missing → say what to run
        st.error(f"No data yet: {exc}. Run `python -m ingest.build_db`.")
    st.markdown("**Capacity Pressure Index — weights (the definition)**")
    st.table({k: f"{v:.2f}" for k, v in config.WEIGHTS.items()})
    st.markdown(f"Hub tiers (share of US pax): " + " · ".join(f"{n} ≥ {c:.2%}" for n, c in config.HUB_TIERS))
    st.markdown(f"Ranking floor: {config.RANKING_FLOOR_PAX_TTM:,} pax TTM · recovery threshold: {config.RECOVERY_THRESHOLD:+.0%}")
    st.caption(f"Model: `{MODEL}` · this conversation: {_fmt_usage(st.session_state.usage_total)}")

    st.markdown("**Conversations**")
    if st.button("➕ New conversation"):
        st.session_state.clear(); st.rerun()
    for p in sorted(CHAT_DIR.glob("*.json"), reverse=True)[:20]:
        title = json.loads(p.read_text(encoding="utf-8"))["title"]
        active = p.stem == st.session_state.get("chat_id")
        if st.button(("▶ " if active else "") + title, key=f"chat-{p.stem}"):
            _load_chat(p); st.rerun()

# ── history ───────────────────────────────────────────────────────────────────
if not st.session_state.display:
    st.markdown("Ask about US airport capacity pressure. Try one of the exam questions:")
    cols = st.columns(2)
    for i, q in enumerate(SAMPLE_QUESTIONS):
        if cols[i % 2].button(q, key=f"sample{i}"):
            st.session_state["pending"] = q

for m in st.session_state.display:
    with st.chat_message(m["role"]):
        st.markdown(m["text"])
        if m.get("trace") or m.get("usage"):
            with st.expander(f"How this was computed — {len(m.get('trace', []))} tool call(s)"):
                if m.get("usage"):
                    st.caption(_fmt_usage(m["usage"]))
                for t in m.get("trace", []):
                    st.markdown(f"**`{t['tool']}`** `{json.dumps(t['input'])}` · {t['ms']} ms")
                    if t["error"]:
                        st.error(t["output"])
                        continue
                    out = t["output"]
                    st.caption(f"source: **{out.get('source')}** · method: {out.get('method')}")
                    for c in out.get("caveats", []):
                        st.caption(f"⚠ {c}")
                    if t["tool"] == "trend" and out.get("result", {}).get("series"):   # the one time-series tool → draw it
                        res = out["result"]
                        st.line_chart({res["metric"]: {p["month"]: p[res["metric"]] for p in res["series"]}})
                    st.json(out.get("result"), expanded=False)

# ── input ─────────────────────────────────────────────────────────────────────
prompt = st.chat_input("Ask about an airport, a region, or how the score works…")
prompt = prompt or st.session_state.pop("pending", None)
if prompt:
    st.session_state.display.append({"role": "user", "text": prompt, "trace": []})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Calling tools…"):
            try:
                text, trace, history, usage = run_turn(st.session_state.history, prompt)
            except Exception as exc:
                text, trace, history, usage = f"**Error:** {type(exc).__name__}: {exc}", [], st.session_state.history, None
        st.markdown(text)
    st.session_state.history = history
    st.session_state.display.append({"role": "assistant", "text": text, "trace": trace, "usage": usage})
    if usage:
        for k in EMPTY_USAGE:
            st.session_state.usage_total[k] = (st.session_state.usage_total.get(k) or 0) + (usage.get(k) or 0)
    _save_chat()
    st.rerun()
