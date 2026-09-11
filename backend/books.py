"""
Reading log: the record-keeping half of the guitar practice diary.

Not a Goodreads clone. Goodreads owns the catalogue; this owns what I did with
it — when I started something, what I made of it, and how it sits next to the
music. The whole library is small (a few hundred rows), so the list endpoint
returns everything and the table sorts and filters in the browser.

Books arrive two ways: a Goodreads CSV export (pipelines/goodreads.py, upserted
on Goodreads' book_id) or typed in here, which gets a "manual-" id so an import
can never collide with it. date_started, notes and rating edits made here
survive re-imports — see the ON CONFLICT list in the importer.
"""
import uuid
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.connect import connect

router = APIRouter()

# Everything the table shows, in one shot.
COLUMNS = [
    "book_id", "title", "author", "isbn", "isbn13", "rating",
    "date_started", "date_read", "date_added", "exclusive_shelf",
    "publisher", "year_published", "original_year", "num_pages",
    "ol_subjects", "ol_description", "notes", "source",
]

EDITABLE = {
    "title", "author", "rating", "date_started", "date_read",
    "exclusive_shelf", "num_pages", "notes", "isbn13",
}


class BookCreate(BaseModel):
    title: str
    author: str | None = None
    rating: int | None = None
    date_started: date | None = None
    date_read: date | None = None
    exclusive_shelf: str = "read"
    num_pages: int | None = None
    notes: str | None = None
    isbn13: str | None = None


class BookUpdate(BaseModel):
    title: str | None = None
    author: str | None = None
    rating: int | None = None
    date_started: date | None = None
    date_read: date | None = None
    exclusive_shelf: str | None = None
    num_pages: int | None = None
    notes: str | None = None
    isbn13: str | None = None


def _rows(conn, where="", params=()):
    cols = ", ".join(COLUMNS)
    rows = conn.execute(f"SELECT {cols} FROM raw_books {where}", list(params)).fetchall()
    return [dict(zip(COLUMNS, r)) for r in rows]


@router.get("/books")
def list_books():
    """The whole library, newest activity first."""
    conn = connect()
    try:
        books = _rows(conn, """
            ORDER BY COALESCE(date_read, date_started, date_added) DESC NULLS LAST, title
        """)
        shelves = dict(conn.execute("""
            SELECT COALESCE(exclusive_shelf, 'unshelved'), COUNT(*) FROM raw_books GROUP BY 1
        """).fetchall())
        pages = conn.execute("""
            SELECT COALESCE(SUM(num_pages), 0) FROM raw_books WHERE exclusive_shelf = 'read'
        """).fetchone()[0]
        rated = conn.execute("""
            SELECT ROUND(AVG(rating), 2) FROM raw_books WHERE rating > 0
        """).fetchone()[0]
        return {
            "books": books,
            "counts": {"total": len(books), "shelves": shelves,
                       "pages_read": int(pages or 0), "average_rating": rated},
        }
    finally:
        conn.close()


@router.post("/books", status_code=201)
def add_book(book: BookCreate):
    conn = connect()
    try:
        book_id = f"manual-{uuid.uuid4().hex[:12]}"
        conn.execute("""
            INSERT INTO raw_books (book_id, title, author, rating, date_started, date_read,
                                   date_added, exclusive_shelf, num_pages, notes, isbn13, source)
            VALUES (?, ?, ?, ?, ?, ?, current_date, ?, ?, ?, ?, 'manual')
        """, [book_id, book.title.strip(), book.author, book.rating, book.date_started,
              book.date_read, book.exclusive_shelf, book.num_pages, book.notes, book.isbn13])
        return _rows(conn, "WHERE book_id = ?", [book_id])[0]
    finally:
        conn.close()


@router.patch("/books/{book_id}")
def update_book(book_id: str, patch: BookUpdate):
    fields = {k: v for k, v in patch.model_dump(exclude_unset=True).items() if k in EDITABLE}
    if not fields:
        raise HTTPException(status_code=400, detail="nothing to update")
    conn = connect()
    try:
        if not conn.execute("SELECT 1 FROM raw_books WHERE book_id = ?", [book_id]).fetchone():
            raise HTTPException(status_code=404, detail="no such book")
        assignments = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE raw_books SET {assignments} WHERE book_id = ?",
                     [*fields.values(), book_id])
        return _rows(conn, "WHERE book_id = ?", [book_id])[0]
    finally:
        conn.close()


@router.delete("/books/{book_id}", status_code=204)
def delete_book(book_id: str):
    conn = connect()
    try:
        conn.execute("DELETE FROM raw_books WHERE book_id = ?", [book_id])
    finally:
        conn.close()
