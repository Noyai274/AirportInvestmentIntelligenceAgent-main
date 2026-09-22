"""
Owner: Claude. Plan §5.2. The Claude tool-use loop.

run_turn(history, user_text) -> (assistant_text, tool_trace, new_history, usage)
  - appends the user message, calls the Anthropic Messages API with the system prompt and TOOL_SCHEMAS
  - while the response asks for tools: run them via agent.tools, append the results, call again
  - at most MAX_ROUNDS tool rounds per turn
  - returns the final text, a trace of every tool call (for the UI expander), the updated history, and
    the token usage / estimated cost of the whole turn

Reads ANTHROPIC_API_KEY (required) and ANTHROPIC_MODEL (optional) from .env. No scoring logic here.
"""

import json
import os
import time

from dotenv import load_dotenv

from agent import tools as T
from agent.prompts import build_system

load_dotenv()

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_ROUNDS = 6
MAX_TOKENS = 6000

# USD per million tokens: (input, output, cache write, cache read). Matched by longest prefix of MODEL.
# Source: platform.claude.com/docs/en/about-claude/pricing (Sept 2026). Estimates only — the bill is the truth.
PRICES = {
    "claude-sonnet-5":   (2.0, 10.0, 2.50, 0.20),
    "claude-sonnet-4-5": (3.0, 15.0, 3.75, 0.30),
    "claude-haiku-4-5":  (1.0,  5.0, 1.25, 0.10),
    "claude-opus-5":     (5.0, 25.0, 6.25, 0.50),
}

_client = None


# _get_client() -> anthropic.Anthropic
def _get_client():
    global _client
    if _client is None:
        import anthropic
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set — copy .env.example to .env and add your key")
        _client = anthropic.Anthropic()
    return _client


# data_note() -> str
def data_note() -> str:
    """One line for the system prompt: latest month, airports, cache date. From the tools, not hard-coded."""
    e = T.explain_scoring()
    d = e["result"]["data"]
    return (f"latest month in the data: {d['ttm_end']}; {d['airports_in_data']:,} airports, "
            f"{d['airports_scored']} scored; source: {e['source']}")


# _run_tool(name, args) -> (dict | str, error: bool)
def _run_tool(name: str, args: dict):
    fn = T.TOOLS.get(name)
    if fn is None:
        return f"unknown tool {name}", True
    try:
        return fn(**args), False
    except Exception as exc:                          # bad argument, unknown code, BTS down…
        return f"{type(exc).__name__}: {exc}", True


# _add_usage(usage, resp) -> None
def _add_usage(usage: dict, resp) -> None:
    u = getattr(resp, "usage", None)
    if u is None:                                     # stub client in tests
        return
    usage["api_calls"] += 1
    usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
    usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
    usage["cache_write_tokens"] += getattr(u, "cache_creation_input_tokens", 0) or 0
    usage["cache_read_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0
    price = next((p for k, p in sorted(PRICES.items(), key=lambda kv: -len(kv[0])) if MODEL.startswith(k)), None)
    if price:
        usage["cost_usd"] = round((usage["input_tokens"] * price[0] + usage["output_tokens"] * price[1]
                                   + usage["cache_write_tokens"] * price[2]
                                   + usage["cache_read_tokens"] * price[3]) / 1e6, 5)


# run_turn(history, user_text, client=None) -> (text, trace, history, usage)
def run_turn(history: list, user_text: str, client=None) -> tuple[str, list, list, dict]:
    client = client or _get_client()
    # cache_control on the system prompt caches tools + system across calls: reads cost 10% of input price
    system = [{"type": "text", "text": build_system(data_note()), "cache_control": {"type": "ephemeral"}}]
    messages = history + [{"role": "user", "content": user_text}]
    trace: list[dict] = []
    usage = {"api_calls": 0, "input_tokens": 0, "output_tokens": 0,
             "cache_write_tokens": 0, "cache_read_tokens": 0, "cost_usd": None}
    parts: list[str] = []          # text from every round — a truncated round plus its continuation
    continued = False              # one continuation per turn, or a long answer loops forever

    for _ in range(MAX_ROUNDS + 1):
        resp = client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, system=system,
                                      tools=T.TOOL_SCHEMAS, messages=messages)
        _add_usage(usage, resp)
        content = [_block_to_dict(b) for b in resp.content]
        messages.append({"role": "assistant", "content": content})
        parts.extend(b["text"] for b in content if b["type"] == "text")

        if resp.stop_reason == "max_tokens" and not continued:      # cut mid-answer: ask for the rest once
            continued = True
            messages.append({"role": "user", "content": "Continue exactly where you stopped."})
            continue
        if resp.stop_reason != "tool_use":                          # end_turn, or a second max_tokens
            text = "".join(parts).strip()
            if resp.stop_reason == "max_tokens":
                text += "\n\n*(answer truncated — ask a narrower question)*"
            return text or "The model returned no text — please rephrase.", trace, messages, usage

        results = []
        for b in content:
            if b["type"] != "tool_use":
                continue
            t0 = time.time()
            out, is_err = _run_tool(b["name"], b["input"])
            trace.append({"tool": b["name"], "input": b["input"], "output": out, "error": is_err,
                          "ms": int((time.time() - t0) * 1000)})
            results.append({"type": "tool_result", "tool_use_id": b["id"], "is_error": is_err,
                            "content": out if is_err else json.dumps(out, default=str)})
        messages.append({"role": "user", "content": results})
        parts = []                                                  # "Let me check…" before a tool call is not the answer

    text = "".join(parts).strip() or "I stopped after too many tool calls without reaching an answer — please narrow the question."
    return text, trace, messages, usage


# _block_to_dict(block) -> dict
def _block_to_dict(b) -> dict:
    """SDK content blocks → plain dicts, so history is JSON-safe (Streamlit session state, tests)."""
    if b.type == "text":
        return {"type": "text", "text": b.text}
    if b.type == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": dict(b.input)}
    if b.type == "thinking":                          # must be echoed back intact (with signature)
        return {"type": "thinking", "thinking": b.thinking, "signature": b.signature}
    if b.type == "redacted_thinking":
        return {"type": "redacted_thinking", "data": b.data}
    return {"type": b.type}
