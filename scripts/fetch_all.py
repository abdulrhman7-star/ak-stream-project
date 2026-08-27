#!/usr/bin/env python3
"""
scripts/fetch_all.py
- يجلب /api/all-movies و /api/all-series ويحفظهما كـ JSON.
- Cron-ready. يعتمد على requests.

ENV:
  API_BASE (default: http://localhost:5001)
  OUT_DIR  (default: ./data)

Example:
  API_BASE=http://localhost:5001 OUT_DIR=./data python scripts/fetch_all.py
"""

import os
import time
import json
from datetime import datetime
from pathlib import Path
import requests

API_BASE = os.environ.get("API_BASE", "http://localhost:5001").rstrip("/")
OUT_DIR = Path(os.environ.get("OUT_DIR", "./data"))
RETRIES = 3
RETRY_DELAY = 1.5


def safe_get(url):
    for i in range(RETRIES + 1):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if i == RETRIES:
                raise
            time.sleep(RETRY_DELAY * (i + 1))


def normalize(item):
    return {
        "title": item.get("title") or item.get("name"),
        "link": item.get("link") or item.get("url"),
        "image": item.get("image"),
        "year": item.get("year"),
        "rating": item.get("rating"),
        "genres": item.get("genres") or [],
        "quality": item.get("quality"),
        "type": item.get("type")
    }


def save_json(name, obj):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / name
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Saved", p)


def main():
    movies_url = f"{API_BASE}/api/all-movies"
    series_url = f"{API_BASE}/api/all-series"
    print("Fetching movies:", movies_url)
    movies_res = safe_get(movies_url)
    print("Fetching series:", series_url)
    series_res = safe_get(series_url)

    movies = [(normalize(i)) for i in (movies_res.get("data") or movies_res)]
    series = [(normalize(i)) for i in (series_res.get("data") or series_res)]

    ts = datetime.utcnow().isoformat().replace(':','-')
    save_json(f"all-movies.{ts}.json", {"fetchedAt": ts, "count": len(movies), "data": movies})
    save_json(f"all-series.{ts}.json", {"fetchedAt": ts, "count": len(series), "data": series})
    save_json("all-movies.latest.json", {"fetchedAt": ts, "count": len(movies), "data": movies})
    save_json("all-series.latest.json", {"fetchedAt": ts, "count": len(series), "data": series})
    print("Done.")


if __name__ == "__main__":
    main()
