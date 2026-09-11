"""
Lock-tolerant DuckDB connections for the API.

DuckDB permits a single writer process and excludes readers while it holds the
lock. The enrichment pipelines flush in short bursts (see fetch_visuals /
fetch_lyrics), so an API request that lands inside a flush window fails with
`IOException: Could not set lock on file`.

That window is on the order of milliseconds, but the Dashboard fires ~8 chart
requests at once — so a single flush takes the whole page down together rather
than dropping one chart. Retrying briefly makes the flush invisible instead.

Anything that must not lose data (the pipelines themselves) should keep using
`get_connection()` directly and handle failure explicitly; this is for the
read path, where a short wait is always better than a 500.
"""
import time

import duckdb

from backend.db.schema import DB_PATH

# Pipeline flush windows are short; ~2s of retries covers them with room to
# spare while still failing fast if a pipeline is genuinely stuck holding it.
_ATTEMPTS = 8
_BACKOFF = 0.25


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    last = None
    for attempt in range(_ATTEMPTS):
        try:
            return duckdb.connect(str(DB_PATH), read_only=read_only)
        except duckdb.IOException as e:
            if "lock" not in str(e).lower():
                raise
            last = e
            time.sleep(_BACKOFF * (attempt + 1))
    raise last
