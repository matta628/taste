"""
Goodreads CSV ingestion + OpenLibrary enrichment pipeline.

Usage:
    python -m backend.pipelines.goodreads --csv data/goodreads_library_export.csv
"""
import csv
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

from backend.db.schema import get_connection

root = Path(__file__).parent.parent.parent
load_dotenv(root / ".env")
load_dotenv(root / ".env.secret")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

OL_API = "https://openlibrary.org/api/books"
CACHE_FILE = Path(__file__).parent.parent.parent / "data" / "ol_cache.json"


def load_ol_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def save_ol_cache(cache: dict):
    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def fetch_openlibrary(isbn: str, cache: dict) -> dict:
    """Fetch book data from OpenLibrary by ISBN. Returns subjects + description."""
    if isbn in cache:
        return cache[isbn]

    key = f"ISBN:{isbn}"
    try:
        resp = requests.get(
            OL_API,
            params={"bibkeys": key, "jscmd": "data", "format": "json"},
            timeout=15
        )
        resp.raise_for_status()
        data = resp.json().get(key, {})

        result = {
            "subjects": [s["name"] if isinstance(s, dict) else s
                         for s in data.get("subjects", [])],
            "description": (
                data.get("notes") or
                data.get("description", {}).get("value") if isinstance(data.get("description"), dict)
                else data.get("description") or ""
            ),
        }
    except Exception as e:
        log.warning(f"OpenLibrary fetch failed for ISBN {isbn}: {e}")
        result = {"subjects": [], "description": ""}

    cache[isbn] = result
    return result


def parse_date(s: str) -> date | None:
    if not s or s.strip() == "":
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return date.fromisoformat(s.strip().replace("/", "-"))
        except ValueError:
            pass
    return None


def parse_int(s: str) -> int | None:
    try:
        return int(float(s.strip())) if s.strip() else None
    except ValueError:
        return None


def run(csv_path: str):
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Goodreads CSV not found: {path}")

    cache = load_ol_cache()
    conn = get_connection()
    inserted = 0
    updated = 0

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    log.info(f"Loaded {len(rows)} books from {path}")

    for i, row in enumerate(rows):
        isbn = (row.get("ISBN13", "") or row.get("ISBN", "") or "").strip().strip('="')
        isbn10 = (row.get("ISBN", "") or "").strip().strip('="')

        # Enrich via OpenLibrary if we have an ISBN
        ol_data = {"subjects": [], "description": ""}
        if isbn:
            ol_data = fetch_openlibrary(isbn, cache)
            time.sleep(0.1)  # polite rate limiting

        book_id = row.get("Book Id", "").strip()
        if not book_id:
            continue

        conn.execute("""
            INSERT INTO raw_books (
                book_id, title, author, isbn, isbn13, rating,
                date_read, date_added, shelf, bookshelves, exclusive_shelf,
                publisher, year_published, original_year, num_pages,
                ol_subjects, ol_description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (book_id) DO UPDATE SET
                rating          = excluded.rating,
                date_read       = excluded.date_read,
                shelf           = excluded.shelf,
                exclusive_shelf = excluded.exclusive_shelf,
                ol_subjects     = excluded.ol_subjects,
                ol_description  = excluded.ol_description
        """, [
            book_id,
            row.get("Title", "").strip(),
            row.get("Author", "").strip(),
            isbn10 or None,
            isbn or None,
            parse_int(row.get("My Rating", "")),
            parse_date(row.get("Date Read", "")),
            parse_date(row.get("Date Added", "")),
            row.get("Shelves", "").strip() or None,
            row.get("Bookshelves", "").strip() or None,
            row.get("Exclusive Shelf", "").strip() or None,
            row.get("Publisher", "").strip() or None,
            parse_int(row.get("Year Published", "")),
            parse_int(row.get("Original Publication Year", "")),
            parse_int(row.get("Number of Pages", "")),
            ol_data["subjects"] if ol_data["subjects"] else None,
            ol_data["description"] or None,
        ])
        inserted += 1

        if (i + 1) % 25 == 0:
            save_ol_cache(cache)
            log.info(f"Progress: {i + 1}/{len(rows)} books processed")

    save_ol_cache(cache)
    conn.close()
    log.info(f"Done. Processed {inserted} books (OpenLibrary cache saved).")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path to Goodreads CSV export")
    args = parser.parse_args()
    run(args.csv)
