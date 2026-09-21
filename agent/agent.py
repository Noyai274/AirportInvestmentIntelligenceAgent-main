"""
Owner: Claude. Plan §5.2. The Claude tool-use loop.

run_turn(history: list[message], user_text: str) -> (assistant_text, tool_trace, new_history)
  - appends the user message, calls the Anthropic Messages API with prompts.SYSTEM and tools.TOOL_SCHEMAS
  - while the response has tool_use blocks: dispatch to agent.tools by name, append tool_result, re-call
  - max ~5 tool rounds per turn
  - returns the final text plus tool_trace: [(tool_name, args, return_dict), ...] for the UI expander

Reads ANTHROPIC_API_KEY from .env via python-dotenv. Model name in one constant at the top.
No scoring logic here — this file only routes.
"""
