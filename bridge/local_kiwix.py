"""Query a local Kiwix ZIM library without copying its contents into SQLite."""

import html
import os
import re
from urllib.parse import quote, urljoin

import requests


KIWIX_URL = os.environ.get("LOCAL_KIWIX_URL", "http://127.0.0.1:8766").rstrip("/")


def search(query, limit=1):
    """Return a compact source-labeled result from the local Kiwix server."""
    if not query.strip():
        return ""
    try:
        response = requests.get(
            f"{KIWIX_URL}/search?pattern={quote(query)}",
            timeout=8,
        )
        response.raise_for_status()
    except requests.RequestException:
        return ""

    links = re.findall(
        r'href="(/content/[^"#]+)"[^>]*>(.*?)</a>', response.text, re.S | re.I
    )
    if not links:
        return ""
    results = []
    for href, title_html in links[:limit]:
        title = re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", "", title_html))).strip()
        if not title:
            continue
        page = requests.get(urljoin(f"{KIWIX_URL}/", href), timeout=8)
        page.raise_for_status()
        text = re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", " ", page.text))).strip()
        text = text[:1600]
        results.append(f"TITLE: {title}\nURL: {KIWIX_URL}{href}\n{text}")
    return "SOURCE: Local offline Kiwix library\n" + "\n\n".join(results) if results else ""