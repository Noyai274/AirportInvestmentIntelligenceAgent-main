"""
Owner: Claude. The tool-use loop, exercised with a stub client — no API key, no network.
The stub answers the first call with a tool_use for rank_airports and the second with text, which is
exactly the shape the real API returns. This proves the loop dispatches, records the trace, feeds the
tool result back, and returns JSON-safe history.
"""

import json
from types import SimpleNamespace as NS

from agent.agent import run_turn


class _Stub:
    def __init__(self):
        self.calls = 0
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls += 1
        assert kw["tools"] and kw["system"] and kw["messages"][0]["role"] == "user"
        if self.calls == 1:
            return NS(stop_reason="tool_use", content=[
                NS(type="text", text="Let me check."),
                NS(type="tool_use", id="tu_1", name="rank_airports",
                   input={"region": "New England", "top_n": 3})])
        # second call: the tool result must be in the last user message
        last = kw["messages"][-1]
        assert last["role"] == "user" and last["content"][0]["type"] == "tool_result"
        payload = json.loads(last["content"][0]["content"])
        codes = [a["code"] for a in payload["result"]["airports"]]
        return NS(stop_reason="end_turn", content=[NS(type="text", text=f"Top New England: {codes}")])


def test_loop_dispatches_tool_and_returns_text():
    stub = _Stub()
    text, trace, history, usage = run_turn([], "Which airports in New England…?", client=stub)
    assert stub.calls == 2
    assert text.startswith("Top New England:")
    assert len(trace) == 1 and trace[0]["tool"] == "rank_airports" and not trace[0]["error"]
    assert trace[0]["output"]["source"]
    json.dumps(history)                                   # history is plain dicts
    assert usage["api_calls"] == 0                        # stub has no .usage → nothing counted
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]


def test_bad_tool_args_become_is_error_not_crash():
    class Bad(_Stub):
        def _create(self, **kw):
            self.calls += 1
            if self.calls == 1:
                return NS(stop_reason="tool_use", content=[
                    NS(type="tool_use", id="tu_1", name="airport_profile", input={"code": "BOSTON"})])
            last = kw["messages"][-1]
            assert last["content"][0]["is_error"] is True
            return NS(stop_reason="end_turn", content=[NS(type="text", text="That code is invalid.")])
    text, trace, _, _ = run_turn([], "profile of BOSTON", client=Bad())
    assert trace[0]["error"] and "ValueError" in trace[0]["output"]
    assert "invalid" in text


def test_max_tokens_is_continued_once_and_text_is_joined():
    class Cut(_Stub):
        def _create(self, **kw):
            self.calls += 1
            if self.calls == 1:
                return NS(stop_reason="max_tokens", content=[NS(type="text", text="First half…")])
            assert kw["messages"][-1]["content"].startswith("Continue")
            return NS(stop_reason="end_turn", content=[NS(type="text", text=" second half.")])
    text, _, _, _ = run_turn([], "long question", client=Cut())
    assert text == "First half… second half."
