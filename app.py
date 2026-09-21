"""
Owner: Claude. Plan §6. Streamlit chat UI. One page.

  streamlit run app.py

  - st.chat_input / st.chat_message; conversation history in st.session_state so follow-ups work
  - sidebar: CPI weights and hub-tier cut-offs read from scoring.config; "data as of <month>" from the DB
  - under each assistant answer, an st.expander("How this was computed") showing the tool_trace:
    each tool call, its arguments, its return dict, and the `source` line
    (cache vs data.bts.gov live) — this is the demo of "not only LLM output" and "uses public APIs"
  - no voice (exam bonus, skipped on purpose)

Calls agent.agent.run_turn; nothing else. No scoring logic here.
"""
