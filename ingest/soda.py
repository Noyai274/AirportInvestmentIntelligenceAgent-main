"""
Owner: Amit (guided). Plan §3.1, §3.5.

Minimal Socrata SODA client. No third-party SODA library (socrata-py is the publishing SDK). Just `requests`.
Exposes one function, fetch(). Not responsible for: renaming columns, caching, SQLite. That is cache.py.
"""

import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

# Socrata returns at most 50,000 rows per request; bigger queries are read page by page.
PAGE_SIZE = 50000
# Hard ceiling on rows per call, so the paging loop is bounded by construction. r495-tyji is ~150k rows.
MAX_ROWS_TOTAL = 2_000_000
# Without an app token Socrata throttles by IP and answers 429. We wait and retry a few times.
RETRIES_ON_429 = 4
RETRY_WAIT_SECONDS = 5


# ──────────────────────────────────────────────────────────────────────────────
# fetch(dataset, domain, token, **params) -> list[dict]
#
# What it does: sends GET https://{domain}/resource/{dataset}.json with whatever SoQL parameters the
# caller passed ($where, $select, $group, ...) and returns every matching row as a list of
# dictionaries. Each dictionary is one row; every value is a STRING, because that is how Socrata
# sends them — converting to numbers is cache.py's job.
#
# How it does it:
#   1. Adds two defaults unless the caller set them: $order=:id (a stable sort — without it Socrata
#      paging silently duplicates and drops rows) and $limit=PAGE_SIZE.
#   2. Resolves the app token: the explicit argument, else the SODA_APP_TOKEN env var, else none.
#      The X-App-Token header is only sent when there is a value; an empty header gets rejected.
#   3. Walks offsets 0, 50000, 100000, ... up to MAX_ROWS_TOTAL. For each page: GET, raise on a
#      non-2xx status (a bad response is an error, never silent garbage), parse JSON, append.
#      A page shorter than $limit is the last one → stop. A 429 (throttled) → sleep and retry.
#
# Why a bounded `for` and not `while True`: the loop provably terminates whatever the server does.
# ──────────────────────────────────────────────────────────────────────────────
def fetch(dataset: str, domain: str = "data.bts.gov", token: str | None = None, **params) -> list[dict]:
    params.setdefault("$order", ":id")
    params.setdefault("$limit", PAGE_SIZE)
    limit = params["$limit"]
    url = f"https://{domain}/resource/{dataset}.json"
    token = token or os.environ.get("SODA_APP_TOKEN")
    headers = {"X-App-Token": token} if token else {}

    rows: list[dict] = []
    for offset in range(0, MAX_ROWS_TOTAL, limit):
        params["$offset"] = offset
        page = _get_page(url, params, headers)
        rows.extend(page)
        if len(page) < limit:
            break
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# _get_page(url, params, headers) -> list[dict]
#
# One HTTP request, with the throttling policy in one place. Tries up to RETRIES_ON_429 times:
# a 429 means "too many requests" → wait RETRY_WAIT_SECONDS and try again; any other error status
# raises immediately via raise_for_status(). Returns the parsed JSON list for that page.
# ──────────────────────────────────────────────────────────────────────────────
def _get_page(url: str, params: dict, headers: dict) -> list[dict]:
    for attempt in range(RETRIES_ON_429):
        resp = requests.get(url, params=params, headers=headers, timeout=60)
        if resp.status_code == 429 and attempt < RETRIES_ON_429 - 1:
            time.sleep(RETRY_WAIT_SECONDS * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("unreachable")
