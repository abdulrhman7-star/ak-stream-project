# ============================================================
# app.py
# AK Stream Project - Catalog API + Background Crawler
# ============================================================

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

import requests
from bs4 import BeautifulSoup

from urllib.parse import urljoin, urlparse
from threading import Thread, RLock
from collections import OrderedDict

import json
import os
import re
import time
import logging
from datetime import datetime, timezone


# ============================================================
# Flask
# ============================================================

app = Flask(__name__)
CORS(app)

app.config["JSON_AS_ASCII"] = False


# ============================================================
# Configuration
# ============================================================

SOURCE_BASE = "https://ak.sv"

MOVIES_URL = f"{SOURCE_BASE}/movies"
SERIES_URL = f"{SOURCE_BASE}/series"

# يمكنك تغييرها حسب حجم المصدر
MAX_MOVIE_PAGES = int(os.getenv("MAX_MOVIE_PAGES", "400"))
MAX_SERIES_PAGES = int(os.getenv("MAX_SERIES_PAGES", "250"))

# التأخير بين الطلبات
CRAWL_DELAY = float(os.getenv("CRAWL_DELAY", "0.5"))

# مهلة HTTP
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))

# عدد العناصر في الصفحة API
DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 100

# حفظ JSON
DATA_DIR = os.getenv("DATA_DIR", "data")
SAVE_JSON = os.getenv("SAVE_JSON", "true").lower() == "true"

MOVIES_JSON = os.path.join(DATA_DIR, "movies.json")
SERIES_JSON = os.path.join(DATA_DIR, "series.json")
STATUS_JSON = os.path.join(DATA_DIR, "status.json")


# ============================================================
# HTTP Headers
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ar,en-US;q=0.8,en;q=0.5",
    "Referer": SOURCE_BASE + "/",
}


# ============================================================
# Runtime Database
# ============================================================

# HTML pages
pages_db = OrderedDict()

# Extracted catalog
movies_db = []
series_db = []

# Thread state
db_lock = RLock()

_is_loading = False
_background_started = False

_last_update = None
_last_error = None

_movie_pages_loaded = 0
_series_pages_loaded = 0


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("ak-stream")


# ============================================================
# Utility
# ============================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


def normalize_url(url, base_url=SOURCE_BASE):
    if not url:
        return ""

    url = url.strip()

    if url.startswith("//"):
        return "https:" + url

    if not url.startswith("http://") and not url.startswith("https://"):
        return urljoin(base_url, url)

    return url


def clean_text(value):
    if not value:
        return ""

    return re.sub(r"\s+", " ", value).strip()


def safe_float(value, default=0):
    try:
        return float(value)
    except Exception:
        return default


# ============================================================
# Extract catalog cards
# ============================================================

def extract_catalog_from_html(html, item_type):
    """
    استخراج عناصر .entry-box-1 من صفحة الأفلام/المسلسلات.
    """

    soup = BeautifulSoup(html, "html.parser")

    results = []

    for item in soup.select(".entry-box-1"):

        # ----------------------------------------------------
        # Title / URL
        # ----------------------------------------------------

        title_el = item.select_one(".entry-title a")

        if not title_el:
            continue

        title = clean_text(title_el.get_text(" ", strip=True))

        link = normalize_url(title_el.get("href"))

        if not title or not link:
            continue

        # ----------------------------------------------------
        # Image
        # ----------------------------------------------------

        image = ""

        img_el = item.select_one("img")

        if img_el:

            candidates = [
                img_el.get("data-src"),
                img_el.get("data-lazy-src"),
                img_el.get("data-original"),
                img_el.get("src"),
            ]

            for candidate in candidates:
                if candidate:
                    image = normalize_url(candidate)
                    break

        # ----------------------------------------------------
        # Rating
        # ----------------------------------------------------

        rating = "0.0"

        rating_el = item.select_one(".label.rating")

        if rating_el:
            rating = clean_text(
                rating_el.get_text(" ", strip=True)
                .replace("⭐", "")
            )

        # ----------------------------------------------------
        # Year
        # ----------------------------------------------------

        year = ""

        year_selectors = [
            ".badge-secondary",
            ".year",
            ".release-year",
        ]

        for selector in year_selectors:

            year_el = item.select_one(selector)

            if year_el:

                candidate = clean_text(
                    year_el.get_text(" ", strip=True)
                )

                if candidate:
                    year = candidate
                    break

        if not year:

            year_match = re.search(
                r"\b(19|20)\d{2}\b",
                item.get_text(" ", strip=True)
            )

            if year_match:
                year = year_match.group(0)

        # ----------------------------------------------------
        # Genres
        # ----------------------------------------------------

        genres = []

        for genre_el in item.select(".badge-light"):

            genre = clean_text(
                genre_el.get_text(" ", strip=True)
            )

            if genre and genre not in genres:
                genres.append(genre)

        # ----------------------------------------------------
        # Quality
        # ----------------------------------------------------

        quality = ""

        quality_el = item.select_one(".label.quality")

        if quality_el:
            quality = clean_text(
                quality_el.get_text(" ", strip=True)
            )

        # ----------------------------------------------------
        # Result
        # ----------------------------------------------------

        results.append({
            "id": link,
            "title": title,
            "link": link,
            "image": image,
            "rating": rating,
            "year": year,
            "genres": genres,
            "quality": quality,
            "type": item_type,
        })

    return results


# ============================================================
# Deduplicate
# ============================================================

def deduplicate_items(items):

    result = []
    seen = set()

    for item in items:

        key = (
            item.get("link")
            or item.get("id")
            or item.get("title", "").lower()
        )

        if not key:
            continue

        if key in seen:
            continue

        seen.add(key)
        result.append(item)

    return result


# ============================================================
# Fetch one page
# ============================================================

def fetch_page(base_url, page):

    if page == 0:
        url = base_url
    else:
        url = f"{base_url}?page={page}"

    logger.info("Fetching %s", url)

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            logger.warning(
                "HTTP %s for %s",
                response.status_code,
                url
            )

            return None

        response.encoding = response.apparent_encoding or response.encoding

        html = response.text

        if not html:
            return None

        return html

    except requests.RequestException as exc:

        logger.error(
            "Request error %s: %s",
            url,
            exc
        )

        return None


# ============================================================
# Fetch category pages
# ============================================================

def crawl_category(base_url, item_type, max_pages):

    global _movie_pages_loaded
    global _series_pages_loaded

    pages_loaded = 0

    for page in range(max_pages):

        html = fetch_page(base_url, page)

        if not html:
            logger.info(
                "Stopping %s crawler at page %s",
                item_type,
                page
            )
            break

        items = extract_catalog_from_html(
            html,
            item_type
        )

        if not items:

            logger.info(
                "No items found on %s page %s",
                item_type,
                page
            )

            break

        page_url = (
            base_url
            if page == 0
            else f"{base_url}?page={page}"
        )

        with db_lock:

            pages_db[page_url] = {
                "html": html,
                "type": item_type,
                "page": page,
                "items_count": len(items),
                "updated_at": utc_now(),
            }

        pages_loaded += 1

        if item_type == "movie":
            _movie_pages_loaded = pages_loaded
        else:
            _series_pages_loaded = pages_loaded

        logger.info(
            "%s page=%s items=%s",
            item_type,
            page,
            len(items)
        )

        time.sleep(CRAWL_DELAY)

    return pages_loaded


# ============================================================
# Rebuild catalog from pages_db
# ============================================================

def rebuild_catalog():

    global movies_db
    global series_db

    movies = []
    series = []

    with db_lock:

        for page_url, page_data in pages_db.items():

            html = page_data.get("html", "")
            item_type = page_data.get("type")

            if not html:
                continue

            if item_type == "movie":

                movies.extend(
                    extract_catalog_from_html(
                        html,
                        "movie"
                    )
                )

            elif item_type == "series":

                series.extend(
                    extract_catalog_from_html(
                        html,
                        "series"
                    )
                )

        movies = deduplicate_items(movies)
        series = deduplicate_items(series)

        movies_db = movies
        series_db = series

    logger.info(
        "Catalog rebuilt: movies=%s series=%s",
        len(movies_db),
        len(series_db)
    )


# ============================================================
# Save JSON database
# ============================================================

def save_database():

    if not SAVE_JSON:
        return

    try:

        os.makedirs(DATA_DIR, exist_ok=True)

        with db_lock:

            with open(
                MOVIES_JSON,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    movies_db,
                    file,
                    ensure_ascii=False,
                    indent=2
                )

            with open(
                SERIES_JSON,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    series_db,
                    file,
                    ensure_ascii=False,
                    indent=2
                )

            status = {
                "updated_at": utc_now(),
                "movies": len(movies_db),
                "series": len(series_db),
                "pages": len(pages_db),
            }

            with open(
                STATUS_JSON,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    status,
                    file,
                    ensure_ascii=False,
                    indent=2
                )

        logger.info("JSON database saved")

    except Exception as exc:

        logger.error(
            "Failed to save JSON database: %s",
            exc
        )


# ============================================================
# Load JSON database
# ============================================================

def load_database():

    global movies_db
    global series_db
    global _last_update

    if not SAVE_JSON:
        return

    try:

        if os.path.exists(MOVIES_JSON):

            with open(
                MOVIES_JSON,
                "r",
                encoding="utf-8"
            ) as file:

                movies_db = json.load(file)

        if os.path.exists(SERIES_JSON):

            with open(
                SERIES_JSON,
                "r",
                encoding="utf-8"
            ) as file:

                series_db = json.load(file)

        if os.path.exists(STATUS_JSON):

            with open(
                STATUS_JSON,
                "r",
                encoding="utf-8"
            ) as file:

                status = json.load(file)

                _last_update = status.get(
                    "updated_at"
                )

        logger.info(
            "Loaded cached JSON: movies=%s series=%s",
            len(movies_db),
            len(series_db)
        )

    except Exception as exc:

        logger.warning(
            "Could not load JSON cache: %s",
            exc
        )


# ============================================================
# Background crawler
# ============================================================

def populate_database():

    global _is_loading
    global _last_update
    global _last_error

    if _is_loading:
        return False

    with db_lock:

        if _is_loading:
            return False

        _is_loading = True
        _last_error = None

    logger.info("==========================================")
    logger.info("Starting background crawler")
    logger.info("==========================================")

    try:

        # ----------------------------------------------------
        # Movies
        # ----------------------------------------------------

        crawl_category(
            MOVIES_URL,
            "movie",
            MAX_MOVIE_PAGES
        )

        # ----------------------------------------------------
        # Series
        # ----------------------------------------------------

        crawl_category(
            SERIES_URL,
            "series",
            MAX_SERIES_PAGES
        )

        # ----------------------------------------------------
        # Build catalog
        # ----------------------------------------------------

        rebuild_catalog()

        # ----------------------------------------------------
        # Save
        # ----------------------------------------------------

        save_database()

        _last_update = utc_now()

        logger.info(
            "Crawler completed successfully"
        )

    except Exception as exc:

        _last_error = str(exc)

        logger.exception(
            "Crawler failed"
        )

    finally:

        _is_loading = False

    return True


def start_background_fetch():

    global _background_started

    if _background_started:
        return

    _background_started = True

    thread = Thread(
        target=populate_database,
        daemon=True
    )

    thread.start()

    logger.info(
        "Background crawler started"
    )


# ============================================================
# Pagination
# ============================================================

def paginate(items, page, limit):

    total = len(items)

    if page < 0:
        page = 0

    if limit < 1:
        limit = DEFAULT_PAGE_SIZE

    if limit > MAX_PAGE_SIZE:
        limit = MAX_PAGE_SIZE

    start = page * limit
    end = start + limit

    data = items[start:end]

    total_pages = (
        (total + limit - 1) // limit
        if total
        else 0
    )

    has_more = end < total

    return {
        "data": data,
        "page": page,
        "limit": limit,
        "total": total,
        "totalPages": total_pages,
        "hasMore": has_more,
    }


# ============================================================
# Search
# ============================================================

def search_catalog(query, item_type=None):

    query = clean_text(query).lower()

    if not query:
        return []

    if item_type == "movie":
        source = movies_db

    elif item_type == "series":
        source = series_db

    else:
        source = movies_db + series_db

    results = []

    for item in source:

        title = clean_text(
            item.get("title", "")
        )

        if query in title.lower():

            title_lower = title.lower()

            if title_lower == query:
                score = 0

            elif title_lower.startswith(query):
                score = 1

            else:
                score = 2

            results.append(
                (score, title_lower, item)
            )

    results.sort(
        key=lambda value: (
            value[0],
            value[1]
        )
    )

    return [
        item
        for _, _, item in results
    ]


# ============================================================
# API: Status
# ============================================================

@app.route("/api/status")
def api_status():

    with db_lock:

        return jsonify({
            "success": True,

            "ready": (
                len(movies_db) > 0
                or len(series_db) > 0
            ),

            "loading": _is_loading,

            "movies": len(movies_db),
            "series": len(series_db),

            "movie_pages": _movie_pages_loaded,
            "series_pages": _series_pages_loaded,

            "pages": len(pages_db),

            "last_update": _last_update,

            "error": _last_error,

            "limits": {
                "max_movie_pages": MAX_MOVIE_PAGES,
                "max_series_pages": MAX_SERIES_PAGES,
            }
        })


# ============================================================
# API: Movies
# ============================================================

@app.route("/api/movies")
def api_movies():

    page = request.args.get(
        "page",
        default=0,
        type=int
    )

    limit = request.args.get(
        "limit",
        default=DEFAULT_PAGE_SIZE,
        type=int
    )

    result = paginate(
        movies_db,
        page,
        limit
    )

    return jsonify({
        "success": True,
        "type": "movie",
        **result
    })


# ============================================================
# API: Series
# ============================================================

@app.route("/api/series")
def api_series():

    page = request.args.get(
        "page",
        default=0,
        type=int
    )

    limit = request.args.get(
        "limit",
        default=DEFAULT_PAGE_SIZE,
        type=int
    )

    result = paginate(
        series_db,
        page,
        limit
    )

    return jsonify({
        "success": True,
        "type": "series",
        **result
    })


# ============================================================
# API: All Movies
# ============================================================

@app.route("/api/all-movies")
def api_all_movies():

    return jsonify({
        "success": True,
        "total": len(movies_db),
        "data": movies_db
    })


# ============================================================
# API: All Series
# ============================================================

@app.route("/api/all-series")
def api_all_series():

    return jsonify({
        "success": True,
        "total": len(series_db),
        "data": series_db
    })


# ============================================================
# API: Search
# ============================================================

@app.route("/api/search")
def api_search():

    query = request.args.get(
        "q",
        ""
    )

    item_type = request.args.get(
        "type"
    )

    limit = request.args.get(
        "limit",
        default=50,
        type=int
    )

    results = search_catalog(
        query,
        item_type
    )

    return jsonify({
        "success": True,
        "query": query,
        "total": len(results),
        "data": results[:limit]
    })


# ============================================================
# API: Feed Pagination
# ============================================================

@app.route("/api/v1/feed/<category>")
def api_feed(category):

    page = request.args.get(
        "page",
        default=0,
        type=int
    )

    limit = request.args.get(
        "limit",
        default=DEFAULT_PAGE_SIZE,
        type=int
    )

    category = category.lower()

    if category in ("movies", "movie"):

        result = paginate(
            movies_db,
            page,
            limit
        )

        return jsonify({
            "success": True,
            "source": "pages_db",
            "category": "movies",
            **result
        })

    if category in ("series", "serie"):

        result = paginate(
            series_db,
            page,
            limit
        )

        return jsonify({
            "success": True,
            "source": "pages_db",
            "category": "series",
            **result
        })

    if category == "home":

        combined = (
            movies_db[:12]
            + series_db[:12]
        )

        return jsonify({
            "success": True,
            "source": "pages_db",
            "category": "home",
            "total": len(combined),
            "data": combined
        })

    return jsonify({
        "success": False,
        "error": "تصنيف غير معروف",
        "available": [
            "home",
            "movies",
            "series"
        ]
    }), 404


# ============================================================
# API: Refresh
# ============================================================

@app.route(
    "/api/refresh",
    methods=["POST"]
)
def api_refresh():

    if _is_loading:

        return jsonify({
            "success": False,
            "message": "جاري تحديث البيانات بالفعل"
        }), 409

    thread = Thread(
        target=populate_database,
        daemon=True
    )

    thread.start()

    return jsonify({
        "success": True,
        "message": "بدأ تحديث البيانات في الخلفية"
    })


# ============================================================
# API: Pages DB information
# ============================================================

@app.route("/api/pages")
def api_pages():

    page_type = request.args.get(
        "type"
    )

    result = []

    with db_lock:

        for url, data in pages_db.items():

            if (
                page_type
                and data.get("type") != page_type
            ):
                continue

            result.append({
                "url": url,
                "type": data.get("type"),
                "page": data.get("page"),
                "items_count": data.get(
                    "items_count",
                    0
                ),
                "updated_at": data.get(
                    "updated_at"
                )
            })

    return jsonify({
        "success": True,
        "total": len(result),
        "data": result
    })


# ============================================================
# API: Get one item by URL
# ============================================================

@app.route("/api/item")
def api_item():

    url = request.args.get(
        "url",
        ""
    ).strip()

    if not url:

        return jsonify({
            "success": False,
            "error": "url مطلوب"
        }), 400

    all_items = movies_db + series_db

    for item in all_items:

        if item.get("link") == url:

            return jsonify({
                "success": True,
                "data": item
            })

    return jsonify({
        "success": False,
        "error": "العنصر غير موجود"
    }), 404


# ============================================================
# API: Health
# ============================================================

@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "time": utc_now()
    })


# ============================================================
# Frontend
# ============================================================

@app.route("/")
def index():

    return render_template(
        "index.html"
    )


# ============================================================
# Error handlers
# ============================================================

@app.errorhandler(404)
def not_found(error):

    if request.path.startswith("/api/"):

        return jsonify({
            "success": False,
            "error": "API endpoint not found"
        }), 404

    return error


@app.errorhandler(500)
def internal_error(error):

    if request.path.startswith("/api/"):

        return jsonify({
            "success": False,
            "error": "Internal server error"
        }), 500

    return error


# ============================================================
# Startup
# ============================================================

load_database()

start_background_fetch()


# ============================================================
# Run
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv("PORT", "5001")
        ),
        debug=False,
        threaded=True
    )
