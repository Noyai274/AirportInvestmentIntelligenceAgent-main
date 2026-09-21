"""
Owner: Claude. Plan §3.1, §3.5.

Minimal Socrata SODA client. ~25 lines. No third-party SODA library (see conversation: cfasodapy is
an odd dependency, socrata-py is the publishing SDK). Just `requests`.

Exposes one function:
  fetch(dataset, domain="data.bts.gov", token=None, **soql) -> list[dict]
    - GET https://{domain}/resource/{dataset}.json with SoQL params ($where, $select, ...)
    - X-App-Token header when SODA_APP_TOKEN is set
    - paginates with $limit=50000 / $offset, and ALWAYS sets $order=:id — without a stable order
      Socrata paging silently duplicates and drops rows
    - returns all rows concatenated; raises on non-200

Not responsible for: renaming columns, caching, SQLite. That is cache.py.
"""
