# app.py – Full working proxy for ak.sv
# No notebook magic, pure Flask.

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re
import time
import threading
import json
import os
from datetime import datetime, timezone

app = Flask(__name__)
CORS(app)

# ------- CONFIG ----------
BASE_URL = "https://ak.sv"
HEADERS = {
    "Referer": BASE_URL + "/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
DATA_DIR = "."
PAGES_DB_FILE = os.path.join(DATA_DIR, "pages_db.json")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

pages_db = {}
catalog = {"movies": [], "series": [], "updated_at": None}
_db_lock = threading.RLock()
_is_loading = False

# ------- HELPERS ----------
def utc_now():
    return datetime.now(timezone.utc).isoformat()

def load_json_file(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def absolute_url(url):
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith(("http://", "https://")):
        return url
    return urljoin(BASE_URL, url)

def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()

def extract_card(item, media_type):
    title_el = item.select_one(".entry-title a") or item.select_one("a")
    if not title_el:
        return None
    title = clean_text(title_el.get_text(" ", strip=True))
    link = absolute_url(title_el.get("href", ""))
    if not title or not link:
        return None
    rating_el = item.select_one(".label.rating")
    rating = clean_text(rating_el.get_text(" ", strip=True)).replace("⭐", "").strip() if rating_el else ""
    year_el = item.select_one(".badge-secondary")
    year = clean_text(year_el.get_text(" ", strip=True)) if year_el else ""
    genres = [clean_text(g.get_text(" ", strip=True)) for g in item.select(".badge-light")]
    quality_el = item.select_one(".label.quality")
    quality = clean_text(quality_el.get_text(" ", strip=True)) if quality_el else ""
    img = item.select_one("img")
    image = ""
    if img:
        for attr in ["data-src", "data-lazy-src", "data-original", "src"]:
            val = img.get(attr)
            if val:
                image = absolute_url(val)
                break
    return {
        "id": link.split("/")[-1] if link else "",
        "title": title,
        "link": link,
        "image": image,
        "rating": rating,
        "year": year,
        "genres": genres,
        "quality": quality,
        "type": media_type
    }

def extract_movies_from_html(html):
    soup = BeautifulSoup(html, "html.parser")
    movies = []
    for item in soup.select(".entry-box-1"):
        card = extract_card(item, "movie")
        if card:
            movies.append(card)
    return movies

def extract_series_from_html(html):
    soup = BeautifulSoup(html, "html.parser")
    series = []
    for item in soup.select(".entry-box-1"):
        card = extract_card(item, "series")
        if card:
            series.append(card)
    return series

def load_database():
    global pages_db, catalog
    pages_db = load_json_file(PAGES_DB_FILE, {})
    loaded = load_json_file(CATALOG_FILE, {"movies": [], "series": [], "updated_at": None})
    catalog["movies"] = loaded.get("movies", [])
    catalog["series"] = loaded.get("series", [])
    catalog["updated_at"] = loaded.get("updated_at")
    print(f"📦 Loaded: {len(pages_db)} pages, {len(catalog['movies'])} movies, {len(catalog['series'])} series")

def get_movies():
    with _db_lock:
        return list(catalog["movies"])

def get_series():
    with _db_lock:
        return list(catalog["series"])

def paginate(items, page=1, limit=24):
    try:
        page = max(1, int(page))
    except:
        page = 1
    try:
        limit = max(1, min(int(limit), 100))
    except:
        limit = 24
    total = len(items)
    start = (page - 1) * limit
    end = start + limit
    data = items[start:end]
    return {
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": (total + limit - 1) // limit if total else 0,
        "has_more": end < total,
        "data": data
    }

# ------- ROUTES ----------
@app.route('/')
def index():
    return jsonify({"message": "🚀 API is running. Use /api/movies, /api/all-movies, /api/movie-links?url=..., /api/series-episodes?url=..."})

@app.route('/api/movies')
def api_movies():
    try:
        page = request.args.get("page", 1)
        limit = request.args.get("limit", 24)
        movies = get_movies()
        result = paginate(movies, page, limit)
        result["success"] = True
        result["type"] = "movie"
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/all-movies')
def api_all_movies():
    try:
        movies = get_movies()
        if request.args.get("all") == "1":
            return jsonify({"success": True, "total": len(movies), "data": movies})
        page = request.args.get("page", 1)
        limit = request.args.get("limit", 50)
        result = paginate(movies, page, limit)
        result["success"] = True
        result["type"] = "movie"
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/all-series')
def api_all_series():
    try:
        series = get_series()
        if request.args.get("all") == "1":
            return jsonify({"success": True, "total": len(series), "data": series})
        page = request.args.get("page", 1)
        limit = request.args.get("limit", 50)
        result = paginate(series, page, limit)
        result["success"] = True
        result["type"] = "series"
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/search')
def api_search():
    try:
        query = request.args.get("q", "").strip()
        if not query:
            return jsonify({"success": True, "total": 0, "data": []})
        q_lower = query.lower()
        all_items = get_movies() + get_series()
        results = []
        for item in all_items:
            title = item.get("title", "").lower()
            if q_lower in title:
                results.append(item)
        limit = min(int(request.args.get("limit", 50)), 100)
        return jsonify({"success": True, "query": query, "total": len(results), "data": results[:limit]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/status')
def api_status():
    with _db_lock:
        return jsonify({
            "success": True,
            "ready": len(catalog["movies"]) > 0 or len(catalog["series"]) > 0,
            "loading": _is_loading,
            "movies": len(catalog["movies"]),
            "series": len(catalog["series"]),
            "pages": len(pages_db),
            "updated_at": catalog.get("updated_at")
        })

@app.route('/api/movie-links')
def movie_links():
    """Extract video sources from a movie/episode page."""
    url = request.args.get("url")
    if not url:
        return jsonify({"success": False, "error": "url parameter required"}), 400
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        video = soup.find("video")
        if not video:
            return jsonify({"success": True, "links": []})
        links = []
        for source in video.find_all("source"):
            src = source.get("src")
            if not src:
                continue
            # Clean up bad prefixes
            clean = re.sub(r'^https://ak\.sv(vlc://|intent:)', '', src)
            clean = re.sub(r'^vlc://|^intent:', '', clean)
            clean = clean.split('#Intent;')[0] if '#Intent;' in clean else clean
            if clean.startswith('http'):
                quality = source.get("size", "")
                links.append({
                    "quality": quality or "SD",
                    "watch": clean,
                    "download": clean  # same for download; can add different logic if needed
                })
        return jsonify({"success": True, "links": links})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/series-episodes')
def series_episodes():
    """Extract episode list from a series page."""
    url = request.args.get("url")
    if not url:
        return jsonify({"success": False, "error": "url parameter required"}), 400
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        episodes = []
        # Common container for episodes on ak.sv
        for item in soup.select('#series-episodes .bg-primary2'):
            link_el = item.select_one('h2 a') or item.select_one('a')
            if not link_el:
                continue
            href = link_el.get('href')
            if not href:
                continue
            full_url = absolute_url(href)
            title = clean_text(link_el.get_text(" ", strip=True))
            # Try to extract episode number from title or URL
            num_match = re.search(r'\d+', title)
            if not num_match:
                num_match = re.search(r'\d+', href)
            number = num_match.group(0) if num_match else '?'
            episodes.append({
                "number": number,
                "title": title,
                "url": full_url
            })
        return jsonify({"success": True, "episodes": episodes})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ------- BACKGROUND CRAWLER (simplified) ----------
def fetch_all_pages():
    global _is_loading
    with _db_lock:
        if _is_loading:
            return
        _is_loading = True
    try:
        print("🚀 Starting background fetch...")
        # Only fetch first 3 pages for demo – you can increase
        for page in range(3):
            for base in ["https://ak.sv/movies", "https://ak.sv/series"]:
                url = f"{base}?page={page}"
                try:
                    resp = requests.get(url, headers=HEADERS, timeout=15)
                    if resp.status_code != 200:
                        continue
                    html = resp.text
                    soup = BeautifulSoup(html, "html.parser")
                    items = soup.select(".entry-box-1")
                    if not items:
                        continue
                    with _db_lock:
                        pages_db[url] = {"html": html, "type": "movies" if "movies" in base else "series"}
                    if "movies" in base:
                        movies = extract_movies_from_html(html)
                        with _db_lock:
                            catalog["movies"].extend(movies)
                            # deduplicate
                            seen = set()
                            deduped = []
                            for m in catalog["movies"]:
                                key = m.get("link")
                                if key and key not in seen:
                                    seen.add(key)
                                    deduped.append(m)
                            catalog["movies"] = deduped
                    else:
                        series = extract_series_from_html(html)
                        with _db_lock:
                            catalog["series"].extend(series)
                            seen = set()
                            deduped = []
                            for s in catalog["series"]:
                                key = s.get("link")
                                if key and key not in seen:
                                    seen.add(key)
                                    deduped.append(s)
                            catalog["series"] = deduped
                    print(f"✅ Fetched {url}")
                    time.sleep(1)
                except Exception as e:
                    print(f"❌ Error fetching {url}: {e}")
        save_json(CATALOG_FILE, catalog)
        save_json(PAGES_DB_FILE, pages_db)
        print("✅ Background fetch complete.")
    finally:
        with _db_lock:
            _is_loading = False

# Start background fetch if database is empty
load_database()
if not catalog["movies"] and not catalog["series"]:
    threading.Thread(target=fetch_all_pages, daemon=True).start()
    print("🟢 Background crawler started.")

# ------- RUN ----------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
