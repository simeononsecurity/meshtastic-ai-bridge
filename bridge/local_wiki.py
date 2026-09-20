#!/usr/bin/env python3
"""Small local Wikipedia search index for the Meshtastic bridge.

The index is SQLite FTS5 so the runtime has no embedding model or vector
database to keep resident on a Raspberry Pi.  The builder accepts one JSON
object per line with ``title``, ``text`` (or ``extract``), and an optional
``url``.
"""

import os
import sqlite3
from pathlib import Path


DEFAULT_INDEX = "/opt/meshtastic-ai-bridge/data/wiki.sqlite3"


def _connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def search(query, index_path=None, limit=3):
    """Return source-labeled local Wikipedia matches, or an empty string."""
    path = Path(index_path or os.environ.get("LOCAL_WIKI_INDEX", DEFAULT_INDEX))
    if not query.strip() or not path.is_file():
        return ""

    # FTS5 treats punctuation as syntax. Quoting terms makes radio queries
    # predictable while still allowing a useful OR fallback below.
    terms = [part.strip('"') for part in query.split() if part.strip('"')]
    if not terms:
        return ""
    match = " AND ".join(f'"{term.replace(chr(34), "")}"' for term in terms)
    connection = None
    try:
        connection = _connect(path)
        rows = connection.execute(
            """SELECT title, text, url, bm25(wiki_fts) AS rank
               FROM wiki_fts WHERE wiki_fts MATCH ?
               ORDER BY rank LIMIT ?""",
            (match, int(limit)),
        ).fetchall()
    except (sqlite3.DatabaseError, OSError):
        return ""
    finally:
        if connection is not None:
            connection.close()

    if not rows:
        return ""
    parts = ["SOURCE: Local Wikipedia index"]
    for row in rows:
        parts.append(f"TITLE: {row['title']}")
        if row["url"]:
            parts.append(f"URL: {row['url']}")
        parts.append(row["text"][:1200].strip())
    return "\n".join(parts)[:3500]