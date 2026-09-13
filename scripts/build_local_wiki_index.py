#!/usr/bin/env python3
"""Build the optional local Wikipedia FTS index from JSONL input.

Each line must be a JSON object containing ``title`` and either ``text`` or
``extract``.  ``url`` is optional. The input may be a curated export or a
Wikipedia-derived dataset; the script deliberately does not fetch data.
"""

import argparse
import json
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Wikipedia JSONL input")
    parser.add_argument("output", type=Path, help="SQLite index to create")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute(
            "CREATE VIRTUAL TABLE wiki_fts USING fts5(title, text, url UNINDEXED)"
        )
        count = 0
        with args.input.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SystemExit(f"Invalid JSON on line {line_number}: {exc}") from exc
                title = str(item.get("title", "")).strip()
                text = str(item.get("text", item.get("extract", ""))).strip()
                if not title or not text:
                    continue
                connection.execute(
                    "INSERT INTO wiki_fts(title, text, url) VALUES (?, ?, ?)",
                    (title, text, str(item.get("url", "")).strip()),
                )
                count += 1
                if count % 1000 == 0:
                    connection.commit()
        connection.commit()
        connection.execute("VACUUM")
        connection.close()
        temporary.replace(args.output)
        print(f"Indexed {count} articles into {args.output}")
    except Exception:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    main()