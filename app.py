from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import requests
import re
import time
import threading
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)
CORS(app)

# =========================================================
# CONFIG
# =========================================================

SOURCE_BASE = "https://ak.sv"

HEADERS = {
    "Referer": SOURCE_BASE + "/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

MOVIES_MAX_PAGES = 400
SERIES_MAX_PAGES = 250
FETCH_DELAY = 0.5

# =========================================================
# TEMP DATABASE
# =========================================================

pages_db = {}

movies_cache = []
series_cache = []

_is_loading = False
_background_started = False
_last_refresh = None
_last_error = None

db_lock = threading.RLock()


# =========================================================
# HELPERS
# =========================================================

def absolute_url(url):
    if not url:
        return ""

    return urljoin(SOURCE_BASE, url)


def normalize_text(value):
    if not value:
        return ""

    return re.sub(r"\s+", " ", value).strip()


def unique_by_link(items):
    result = []
    seen = set()

    for item in items:
        link = item.get("link") or item.get("url") or ""

        if not link:
            continue

        if link in seen:
            continue

        seen.add(link)
        result.append(item)

    return result


# =========================================================
# EXTRACT MOVIES
# =========================================================

def extract_movies_from_html(html):

    soup = BeautifulSoup(html, "html.parser")

    movies = []

    for item in soup.select(".entry-box-1"):

        title_el = item.select_one(".entry-title a")

        if not title_el:
            continue

        title = normalize_text(title_el.get_text(" ", strip=True))

        link = absolute_url(title_el.get("href"))

        img_el = item.select_one("img")

        image = ""

        if img_el:

            image = (
                img_el.get("data-src")
                or img_el.get("data-lazy-src")
                or img_el.get("src")
                or ""
            )

            image = absolute_url(image)

        rating_el = item.select_one(".label.rating")

        rating = "0.0"

        if rating_el:
            rating = normalize_text(
                rating_el.get_text(" ", strip=True)
            ).replace("⭐", "").strip()

        year_el = item.select_one(".badge-secondary")

        year = "----"

        if year_el:
            year = normalize_text(
                year_el.get_text(" ", strip=True)
            )

        genres = []

        for genre in item.select(".badge-light"):

            value = normalize_text(
                genre.get_text(" ", strip=True)
            )

            if value:
                genres.append(value)

        quality_el = item.select_one(".label.quality")

        quality = ""

        if quality_el:
            quality = normalize_text(
                quality_el.get_text(" ", strip=True)
            )

        movies.append({
            "id": link,
            "title": title,
            "link": link,
            "image": image,
            "rating": rating,
            "year": year,
            "genres": genres,
            "quality": quality,
            "type": "movie"
        })

    return unique_by_link(movies)


# =========================================================
# EXTRACT SERIES
# =========================================================

def extract_series_from_html(html):

    items = extract_movies_from_html(html)

    for item in items:
        item["type"] = "series"

    return items


# =========================================================
# EXTRACT EPISODES
# =========================================================

def extract_episodes_from_html(html):

    soup = BeautifulSoup(html, "html.parser")

    episodes = []

    selectors = [
        "#series-episodes .bg-primary2",
        "#series-episodes .episode",
        ".episodes .episode"
    ]

    elements = []

    for selector in selectors:

        elements = soup.select(selector)

        if elements:
            break

    for index, item in enumerate(elements, start=1):

        link_el = (
            item.select_one("h2 a")
            or item.select_one("a")
        )

        if not link_el:
            continue

        title = normalize_text(
            link_el.get_text(" ", strip=True)
        )

        url = absolute_url(
            link_el.get("href")
        )

        img_el = item.select_one("img")

        image = ""

        if img_el:

            image = (
                img_el.get("data-src")
                or img_el.get("data-lazy-src")
                or img_el.get("src")
                or ""
            )

            image = absolute_url(image)

        match = re.search(
            r"(?:episode|ep|الحلقة|حلقة)?\s*(\d+)",
            title,
            re.IGNORECASE
        )

        number = (
            match.group(1)
            if match
            else str(index)
        )

        episodes.append({
            "number": number,
            "title": title,
            "url": url,
            "image": image
        })

    return episodes


# =========================================================
# FETCH PAGES
# =========================================================

def fetch_page(url):

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=20
    )

    response.raise_for_status()

    return response.text


def fetch_all_pages(
    base_url,
    max_pages,
    delay=FETCH_DELAY
):

    pages = []

    for page in range(max_pages):

        url = f"{base_url}?page={page}"

        print(f"[FETCH] {url}")

        try:

            html = fetch_page(url)

            soup = BeautifulSoup(
                html,
                "html.parser"
            )

            items = soup.select(
                ".entry-box-1"
            )

            if not items:

                print(
                    f"[STOP] No items at page {page}"
                )

                break

            pages.append({
                "page": page,
                "url": url,
                "html": html,
                "items": len(items)
            })

            time.sleep(delay)

        except Exception as exc:

            print(
                f"[ERROR] {url}: {exc}"
            )

            break

    return pages


# =========================================================
# DATABASE REFRESH
# =========================================================

def rebuild_caches():

    global movies_cache
    global series_cache

    movies = []
    series = []

    with db_lock:

        for data in pages_db.values():

            if data["type"] == "movies":

                movies.extend(
                    extract_movies_from_html(
                        data["html"]
                    )
                )

            elif data["type"] == "series":

                series.extend(
                    extract_series_from_html(
                        data["html"]
                    )
                )

    movies_cache = unique_by_link(movies)
    series_cache = unique_by_link(series)

    print(
        f"[CACHE] movies={len(movies_cache)} "
        f"series={len(series_cache)}"
    )


# =========================================================
# BACKGROUND LOADER
# =========================================================

def populate_pages_db():

    global _is_loading
    global _last_refresh
    global _last_error

    with db_lock:

        if _is_loading:
            return

        _is_loading = True
        _last_error = None

    print("[DB] Starting refresh")

    try:

        # -----------------------------
        # MOVIES
        # -----------------------------

        movie_pages = fetch_all_pages(
            SOURCE_BASE + "/movies",
            MOVIES_MAX_PAGES
        )

        with db_lock:

            # حذف صفحات الأفلام القديمة
            for key in list(pages_db.keys()):

                if pages_db[key]["type"] == "movies":
                    del pages_db[key]

            for page in movie_pages:

                key = (
                    f"{SOURCE_BASE}/movies"
                    f"?page={page['page']}"
                )

                pages_db[key] = {
                    "html": page["html"],
                    "type": "movies",
                    "page": page["page"],
                    "items": page["items"]
                }

        # -----------------------------
        # SERIES
        # -----------------------------

        series_pages = fetch_all_pages(
            SOURCE_BASE + "/series",
            SERIES_MAX_PAGES
        )

        with db_lock:

            for key in list(pages_db.keys()):

                if pages_db[key]["type"] == "series":
                    del pages_db[key]

            for page in series_pages:

                key = (
                    f"{SOURCE_BASE}/series"
                    f"?page={page['page']}"
                )

                pages_db[key] = {
                    "html": page["html"],
                    "type": "series",
                    "page": page["page"],
                    "items": page["items"]
                }

        rebuild_caches()

        _last_refresh = time.time()

        print(
            f"[DB] Refresh completed "
            f"pages={len(pages_db)}"
        )

    except Exception as exc:

        _last_error = str(exc)

        print(
            f"[DB ERROR] {exc}"
        )

    finally:

        with db_lock:
            _is_loading = False


def start_background_refresh():

    global _background_started

    with db_lock:

        if _background_started:
            return

        _background_started = True

    thread = threading.Thread(
        target=populate_pages_db,
        daemon=True
    )

    thread.start()


# =========================================================
# PAGINATION
# =========================================================

def paginate(items, page, limit):

    try:
        page = max(int(page), 1)
    except:
        page = 1

    try:
        limit = max(min(int(limit), 100), 1)
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
        "pages": (
            (total + limit - 1) // limit
            if total
            else 0
        ),
        "hasNext": end < total,
        "hasPrevious": page > 1,
        "data": data
    }


# =========================================================
# STATUS API
# =========================================================

@app.route("/api/status")
def api_status():

    with db_lock:

        return jsonify({
            "success": True,
            "ready": (
                len(movies_cache) > 0
                or len(series_cache) > 0
            ),
            "loading": _is_loading,
            "pages": len(pages_db),
            "movies": len(movies_cache),
            "series": len(series_cache),
            "lastRefresh": _last_refresh,
            "error": _last_error
        })


# =========================================================
# MOVIES API
# =========================================================

@app.route("/api/movies")
def api_movies():

    page = request.args.get("page", 1)
    limit = request.args.get("limit", 24)

    result = paginate(
        movies_cache,
        page,
        limit
    )

    return jsonify({
        "success": True,
        "type": "movie",
        **result
    })


# =========================================================
# SERIES API
# =========================================================

@app.route("/api/series")
def api_series():

    page = request.args.get("page", 1)
    limit = request.args.get("limit", 24)

    result = paginate(
        series_cache,
        page,
        limit
    )

    return jsonify({
        "success": True,
        "type": "series",
        **result
    })


# =========================================================
# ALL MOVIES
# =========================================================

@app.route("/api/all-movies")
def api_all_movies():

    return jsonify({
        "success": True,
        "type": "movie",
        "total": len(movies_cache),
        "data": movies_cache
    })


# =========================================================
# ALL SERIES
# =========================================================

@app.route("/api/all-series")
def api_all_series():

    return jsonify({
        "success": True,
        "type": "series",
        "total": len(series_cache),
        "data": series_cache
    })


# =========================================================
# SEARCH
# =========================================================

@app.route("/api/search")
def api_search():

    query = normalize_text(
        request.args.get("q", "")
    ).lower()

    if not query:

        return jsonify({
            "success": True,
            "query": "",
            "total": 0,
            "data": []
        })

    results = []

    combined = (
        movies_cache +
        series_cache
    )

    seen = set()

    for item in combined:

        title = item.get(
            "title",
            ""
        ).lower()

        genres = " ".join(
            item.get("genres", [])
        ).lower()

        if (
            query in title
            or query in genres
        ):

            key = item.get(
                "link"
            )

            if key in seen:
                continue

            seen.add(key)

            # ترتيب العنوان المطابق أولاً
            score = 0

            if title == query:
                score = 0
            elif title.startswith(query):
                score = 1
            elif query in title:
                score = 2
            else:
                score = 3

            results.append(
                (score, item)
            )

    results.sort(
        key=lambda x: (
            x[0],
            x[1].get("title", "").lower()
        )
    )

    data = [
        item
        for _, item in results[:100]
    ]

    return jsonify({
        "success": True,
        "query": query,
        "total": len(data),
        "data": data
    })


# =========================================================
# SEARCH MOVIES ONLY
# =========================================================

@app.route("/api/search/movies")
def search_movies():

    query = normalize_text(
        request.args.get("q", "")
    ).lower()

    results = [
        movie
        for movie in movies_cache
        if query in movie["title"].lower()
    ]

    return jsonify({
        "success": True,
        "query": query,
        "total": len(results),
        "data": results[:100]
    })


# =========================================================
# SEARCH SERIES ONLY
# =========================================================

@app.route("/api/search/series")
def search_series():

    query = normalize_text(
        request.args.get("q", "")
    ).lower()

    results = [
        item
        for item in series_cache
        if query in item["title"].lower()
    ]

    return jsonify({
        "success": True,
        "query": query,
        "total": len(results),
        "data": results[:100]
    })


# =========================================================
# GET ITEM BY URL
# =========================================================

@app.route("/api/item")
def api_item():

    url = request.args.get("url", "").strip()

    if not url:

        return jsonify({
            "success": False,
            "error": "url is required"
        }), 400

    for item in (
        movies_cache +
        series_cache
    ):

        if item.get("link") == url:

            return jsonify({
                "success": True,
                "data": item
            })

    return jsonify({
        "success": False,
        "error": "item not found"
    }), 404


# =========================================================
# SERIES EPISODES
# =========================================================

@app.route("/api/series-episodes")
def api_series_episodes():

    url = request.args.get(
        "url",
        ""
    ).strip()

    if not url:

        return jsonify({
            "success": False,
            "error": "url is required"
        }), 400

    try:

        html = fetch_page(url)

        episodes = extract_episodes_from_html(
            html
        )

        return jsonify({
            "success": True,
            "series": url,
            "total": len(episodes),
            "data": episodes
        })

    except Exception as exc:

        return jsonify({
            "success": False,
            "error": str(exc)
        }), 500


# =========================================================
# REFRESH
# =========================================================

@app.route(
    "/api/refresh",
    methods=["POST"]
)
def api_refresh():

    with db_lock:

        if _is_loading:

            return jsonify({
                "success": False,
                "message": "Refresh already running"
            }), 409

    thread = threading.Thread(
        target=populate_pages_db,
        daemon=True
    )

    thread.start()

    return jsonify({
        "success": True,
        "message": "Background refresh started"
    })


# =========================================================
# FEED API
# =========================================================

@app.route(
    "/api/v1/feed/<category>"
)
def api_feed(category):

    page = request.args.get(
        "page",
        1
    )

    limit = request.args.get(
        "limit",
        24
    )

    category = category.lower()

    if category == "movies":

        items = movies_cache

    elif category == "series":

        items = series_cache

    elif category in (
        "home",
        "all"
    ):

        items = (
            movies_cache +
            series_cache
        )

    else:

        return jsonify({
            "success": False,
            "error": "Unknown category"
        }), 404

    result = paginate(
        items,
        page,
        limit
    )

    return jsonify({
        "success": True,
        "source": "pages_db",
        "category": category,
        **result
    })


# =========================================================
# HEALTH
# =========================================================

@app.route("/health")
def health():

    return jsonify({
        "status": "ok"
    })


# =========================================================
# FRONTEND
# =========================================================

@app.route("/")
def index():

    return render_template(
        "index.html"
    )


@app.route("/watch")
def watch():

    return render_template(
        "watch.html"
    )


# =========================================================
# START
# =========================================================

start_background_refresh()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5001,
        debug=False
    )
