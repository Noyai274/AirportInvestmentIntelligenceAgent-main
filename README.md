# Airport Investment Intelligence Agent

<!--
Owner: Claude. Plan §3.5, §11. Written last, once everything runs.

Must contain:
  - One paragraph: what the agent does and the one design idea (Python computes, the LLM narrates — plan §0).
  - Setup: python 3.10+, pip install -r requirements.txt, copy .env.example → .env, add ANTHROPIC_API_KEY.
  - Run: streamlit run app.py
  - Data: "the agent queries BTS through the Socrata SODA API on demand; a warmed cache
    (data/airports.db, dated) is committed so the app runs offline. Delete it to see live fetches.
    python -m ingest.build_db refreshes it."
  - The four sample questions from the exam, ready to paste.
  - Pointer to DESIGN.md for methodology.

Housekeeping note: node_modules/, package.json and package-lock.json in this folder are from an
accidental `npm install python venv` and have nothing to do with the project. They are gitignored;
delete them before submission.
-->
