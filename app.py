# ============================================================
# 1. INSTALL DEPENDENCIES
# ============================================================
!pip install flask flask-cors requests beautifulsoup4 pyngrok -q

# ============================================================
# 2. WRITE THE app.py FILE
# ============================================================
%%writefile app.py
# ============================================================
# ak-stream-project
# app.py
#
# Flask API + pages_db + persistent catalog
#
# الوظائف:
#   - جلب صفحات الأفلام والمسلسلات تدريجياً
#   - تخزين HTML في pages_db
#   - استخراج Catalog من الصفحات
#   - حفظ catalog.json و pages_db.json
#   - Pagination
#   - Search
#   - Status
#   - Refresh
#   - Item lookup
#   - Page lookup
#   - Export
# ============================================================

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

import requests
from bs4 import BeautifulSoup

from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone

import threading
import time
import json
import os
import re
import hashlib


# ============================================================
# APP
# ============================================================

app = Flask(__name__)
CORS(app)


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://ak.sv"

MOVIES_URL = f"{BASE_URL}/movies"
SERIES_URL = f"{BASE_URL}/series"

MOVIES_MAX_PAGES = int(os.getenv("MOVIES_MAX_PAGES", "400"))
SERIES_MAX_PAGES = int(os.getenv("SERIES_MAX_PAGES", "250"))

FETCH_DELAY = float(os.getenv("FETCH_DELAY", "0.5"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))

DATA_DIR = os.getenv("DATA_DIR", ".")

PAGES_DB_FILE = os.path.join(DATA_DIR, "pages_db.json")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

HEADERS = {
    "Referer": BASE_URL + "/",
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
}


# ============================================================
# DATABASE
# ============================================================

pages_db = {}

catalog = {
    "movies": [],
    "series": [],
    "updated_at": None,
}

_is_loading = False
_background_started = False

_db_lock = threading.RLock()


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# JSON HELPERS
# ============================================================

def atomic_write_json(path, data):
    """
    حفظ JSON بطريقة آمنة:
    يكتب إلى ملف مؤقت ثم يستبدل الملف القديم.
    """

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    temp_path = path + ".tmp"

    with open(
        temp_path,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp_path, path)


def load_json_file(path, default):
    if not os.path.exists(path):
        return default

    try:
        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ فشل قراءة {path}: {e}")
        return default


# ============================================================
# LOAD DATABASE
# ============================================================

def load_database():

    global pages_db
    global catalog

    loaded_pages = load_json_file(
        PAGES_DB_FILE,
        {}
    )

    loaded_catalog = load_json_file(
        CATALOG_FILE,
        {
            "movies": [],
            "series": [],
            "updated_at": None
        }
    )

    if isinstance(loaded_pages, dict):
        pages_db = loaded_pages

    if isinstance(loaded_catalog, dict):

        catalog = {
            "movies": loaded_catalog.get(
                "movies",
                []
            ),
            "series": loaded_catalog.get(
                "series",
                []
            ),
            "updated_at": loaded_catalog.get(
                "updated_at"
            )
        }

    print(
        "📦 Database loaded:"
        f" pages={len(pages_db)},"
        f" movies={len(catalog['movies'])},"
        f" series={len(catalog['series'])}"
    )


# ============================================================
# SAVE DATABASE
# ============================================================

def save_pages_db():

    with _db_lock:

        # لا تحفظ HTML ضخماً إذا أردت Catalog فقط.
        # هنا نحفظ الصفحات كما طلب المشروع.
        atomic_write_json(
            PAGES_DB_FILE,
            pages_db
        )


def save_catalog():

    with _db_lock:

        catalog["updated_at"] = utc_now()

        atomic_write_json(
            CATALOG_FILE,
            catalog
        )


# ============================================================
# URL HELPERS
# ============================================================

def absolute_url(url):

    if not url:
        return ""

    url = url.strip()

    if url.startswith("//"):
        return "https:" + url

    if url.startswith("http://"):
        return url

    if url.startswith("https://"):
        return url

    return urljoin(
        BASE_URL,
        url
    )


def make_id(url):

    if not url:
        return ""

    # محاولة استخراج ID من الرابط
    match = re.search(
        r"/(?:movie|movies|series|show)/(\d+)",
        url,
        re.IGNORECASE
    )

    if match:
        return match.group(1)

    # fallback
    return hashlib.sha1(
        url.encode("utf-8")
    ).hexdigest()[:16]


# ============================================================
# TEXT HELPERS
# ============================================================

def clean_text(value):

    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        value
    ).strip()


def extract_year(text):

    if not text:
        return ""

    match = re.search(
        r"\b(19\d{2}|20\d{2})\b",
        text
    )

    return match.group(1) if match else ""


def extract_rating(text):

    if not text:
        return ""

    match = re.search(
        r"(\d+(?:\.\d+)?)",
        text
    )

    return match.group(1) if match else ""


# ============================================================
# IMAGE
# ============================================================

def extract_image(item):

    img = item.select_one("img")

    if not img:
        return ""

    candidates = [
        img.get("data-src"),
        img.get("data-lazy-src"),
        img.get("data-original"),
        img.get("src"),
    ]

    for value in candidates:

        if value:
            return absolute_url(value)

    return ""


# ============================================================
# GENERIC CARD PARSER
# ============================================================

def extract_card(item, media_type):

    title_el = item.select_one(
        ".entry-title a"
    )

    if not title_el:
        title_el = item.select_one("a")

    if not title_el:
        return None

    title = clean_text(
        title_el.get_text(" ", strip=True)
    )

    link = absolute_url(
        title_el.get("href", "")
    )

    if not title or not link:
        return None

    # --------------------------------------------------------
    # Rating
    # --------------------------------------------------------

    rating_el = item.select_one(
        ".label.rating"
    )

    rating = ""

    if rating_el:
        rating = clean_text(
            rating_el.get_text(
                " ",
                strip=True
            )
        )

        rating = rating.replace(
            "⭐",
            ""
        ).strip()

        rating = extract_rating(
            rating
        )

    # --------------------------------------------------------
    # Year
    # --------------------------------------------------------

    year_el = item.select_one(
        ".badge-secondary"
    )

    year = ""

    if year_el:

        year = clean_text(
            year_el.get_text(
                " ",
                strip=True
            )
        )

    if not year:
        year = extract_year(
            item.get_text(
                " ",
                strip=True
            )
        )

    # --------------------------------------------------------
    # Genres
    # --------------------------------------------------------

    genres = []

    for genre_el in item.select(
        ".badge-light"
    ):

        genre = clean_text(
            genre_el.get_text(
                " ",
                strip=True
            )
        )

        if genre and genre not in genres:
            genres.append(genre)

    # --------------------------------------------------------
    # Quality
    # --------------------------------------------------------

    quality_el = item.select_one(
        ".label.quality"
    )

    quality = ""

    if quality_el:

        quality = clean_text(
            quality_el.get_text(
                " ",
                strip=True
            )
        )

    # --------------------------------------------------------
    # Image
    # --------------------------------------------------------

    image = extract_image(item)

    # --------------------------------------------------------
    # Object
    # --------------------------------------------------------

    return {
        "id": make_id(link),
        "title": title,
        "link": link,
        "image": image,
        "rating": rating,
        "year": year,
        "genres": genres,
        "quality": quality,
        "type": media_type,
    }


# ============================================================
# MOVIES
# ============================================================

def extract_movies_from_html(
    html,
    base_url=BASE_URL
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    movies = []

    for item in soup.select(
        ".entry-box-1"
    ):

        movie = extract_card(
            item,
            "movie"
        )

        if movie:
            movies.append(movie)

    return movies


# ============================================================
# SERIES
# ============================================================

def extract_series_from_html(
    html,
    base_url=BASE_URL
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    series = []

    for item in soup.select(
        ".entry-box-1"
    ):

        show = extract_card(
            item,
            "series"
        )

        if show:
            series.append(show)

    return series


# ============================================================
# DEDUPLICATION
# ============================================================

def deduplicate_items(items):

    result = []
    seen = set()

    for item in items:

        key = (
            item.get("link")
            or item.get("id")
            or item.get("title")
        )

        if not key:
            continue

        key = str(key).lower().strip()

        if key in seen:
            continue

        seen.add(key)

        result.append(item)

    return result


# ============================================================
# FETCH PAGE
# ============================================================

def fetch_page(url):

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.text


# ============================================================
# FETCH ALL PAGINATED PAGES
# ============================================================

def fetch_all_pages(
    base_url,
    media_type,
    max_pages,
    delay=FETCH_DELAY
):

    fetched = 0

    for page in range(max_pages):

        url = (
            f"{base_url}?page={page}"
        )

        print(
            f"⏳ [{media_type}] "
            f"page={page}"
        )

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
                    f"🛑 لا توجد عناصر في "
                    f"page={page}"
                )

                break

            page_type = (
                "movies"
                if media_type == "movie"
                else "series"
            )

            with _db_lock:

                pages_db[url] = {
                    "url": url,
                    "html": html,
                    "type": page_type,
                    "page": page,
                    "items_count": len(items),
                    "fetched_at": utc_now()
                }

            fetched += 1

            print(
                f"✅ {url} "
                f"({len(items)} items)"
            )

            # حفظ دوري
            if fetched % 10 == 0:
                save_pages_db()

            if delay > 0:
                time.sleep(delay)

        except Exception as e:

            print(
                f"❌ فشل {url}: {e}"
            )

            # لا نكمل إلى ما لا نهاية
            # إذا حصل خطأ اتصال.
            break

    return fetched


# ============================================================
# REBUILD CATALOG
# ============================================================

def rebuild_catalog():

    movies = []
    series = []

    with _db_lock:

        page_values = list(
            pages_db.values()
        )

    for page_data in page_values:

        html = page_data.get(
            "html",
            ""
        )

        if not html:
            continue

        page_type = page_data.get(
            "type"
        )

        if page_type == "movies":

            movies.extend(
                extract_movies_from_html(
                    html
                )
            )

        elif page_type == "series":

            series.extend(
                extract_series_from_html(
                    html
                )
            )

    movies = deduplicate_items(
        movies
    )

    series = deduplicate_items(
        series
    )

    with _db_lock:

        catalog["movies"] = movies
        catalog["series"] = series
        catalog["updated_at"] = utc_now()

    save_catalog()

    print(
        "📚 Catalog rebuilt:"
        f" movies={len(movies)},"
        f" series={len(series)}"
    )


# ============================================================
# BACKGROUND REFRESH
# ============================================================

def populate_pages_db_background():

    global _is_loading

    with _db_lock:

        if _is_loading:
            return

        _is_loading = True

    print(
        "🚀 بدء تحديث قاعدة البيانات..."
    )

    try:

        # ----------------------------------------------------
        # Movies
        # ----------------------------------------------------

        fetch_all_pages(
            MOVIES_URL,
            "movie",
            MOVIES_MAX_PAGES
        )

        save_pages_db()

        # ----------------------------------------------------
        # Series
        # ----------------------------------------------------

        fetch_all_pages(
            SERIES_URL,
            "series",
            SERIES_MAX_PAGES
        )

        save_pages_db()

        # ----------------------------------------------------
        # Build catalog
        # ----------------------------------------------------

        rebuild_catalog()

        print(
            "✅ اكتمل تحديث Catalog"
        )

    except Exception as e:

        print(
            f"❌ خطأ في التحديث: {e}"
        )

    finally:

        with _db_lock:
            _is_loading = False


def start_background_fetch():

    global _background_started

    with _db_lock:

        if _background_started:
            return

        _background_started = True

    thread = threading.Thread(
        target=populate_pages_db_background,
        daemon=True
    )

    thread.start()

    print(
        "🟢 Background crawler started"
    )


# ============================================================
# CATALOG HELPERS
# ============================================================

def get_movies():

    with _db_lock:
        return list(
            catalog["movies"]
        )


def get_series():

    with _db_lock:
        return list(
            catalog["series"]
        )


def get_all_items():

    return (
        get_movies()
        + get_series()
    )


def paginate(
    items,
    page=1,
    limit=24
):

    try:
        page = max(
            1,
            int(page)
        )
    except Exception:
        page = 1

    try:
        limit = max(
            1,
            min(
                int(limit),
                100
            )
        )
    except Exception:
        limit = 24

    total = len(items)

    start = (
        page - 1
    ) * limit

    end = (
        start + limit
    )

    data = items[
        start:end
    ]

    total_pages = (
        (total + limit - 1)
        // limit
        if total
        else 0
    )

    return {
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "has_more": end < total,
        "data": data
    }


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return jsonify({
        "success": True,
        "status": "ok",
        "time": utc_now()
    })


# ============================================================
# STATUS
# ============================================================

@app.route("/api/status")
def api_status():

    with _db_lock:

        movies_count = len(
            catalog["movies"]
        )

        series_count = len(
            catalog["series"]
        )

        pages_count = len(
            pages_db
        )

        loading = _is_loading

        updated_at = catalog.get(
            "updated_at"
        )

    return jsonify({

        "success": True,

        "ready": (
            not loading
            and (
                movies_count > 0
                or series_count > 0
            )
        ),

        "loading": loading,

        "movies": movies_count,

        "series": series_count,

        "total": (
            movies_count
            + series_count
        ),

        "pages": pages_count,

        "updated_at": updated_at

    })


# ============================================================
# MOVIES
# ============================================================

@app.route("/api/movies")
def api_movies():

    page = request.args.get(
        "page",
        1
    )

    limit = request.args.get(
        "limit",
        24
    )

    result = paginate(
        get_movies(),
        page,
        limit
    )

    result["success"] = True
    result["type"] = "movie"

    return jsonify(result)


# ============================================================
# SERIES
# ============================================================

@app.route("/api/series")
def api_series():

    page = request.args.get(
        "page",
        1
    )

    limit = request.args.get(
        "limit",
        24
    )

    result = paginate(
        get_series(),
        page,
        limit
    )

    result["success"] = True
    result["type"] = "series"

    return jsonify(result)


# ============================================================
# ALL MOVIES
# ============================================================

@app.route("/api/all-movies")
def api_all_movies():

    all_movies = get_movies()

    # all=1 يعيد كل العناصر
    if request.args.get(
        "all"
    ) == "1":

        return jsonify({
            "success": True,
            "type": "movie",
            "total": len(all_movies),
            "data": all_movies
        })

    page = request.args.get(
        "page",
        1
    )

    limit = request.args.get(
        "limit",
        50
    )

    result = paginate(
        all_movies,
        page,
        limit
    )

    result["success"] = True
    result["type"] = "movie"

    return jsonify(result)


# ============================================================
# ALL SERIES
# ============================================================

@app.route("/api/all-series")
def api_all_series():

    all_series = get_series()

    if request.args.get(
        "all"
    ) == "1":

        return jsonify({
            "success": True,
            "type": "series",
            "total": len(all_series),
            "data": all_series
        })

    page = request.args.get(
        "page",
        1
    )

    limit = request.args.get(
        "limit",
        50
    )

    result = paginate(
        all_series,
        page,
        limit
    )

    result["success"] = True
    result["type"] = "series"

    return jsonify(result)


# ============================================================
# SEARCH
# ============================================================

@app.route("/api/search")
def api_search():

    query = clean_text(
        request.args.get(
            "q",
            ""
        )
    )

    media_type = clean_text(
        request.args.get(
            "type",
            "all"
        )
    ).lower()

    if not query:

        return jsonify({
            "success": True,
            "query": "",
            "total": 0,
            "data": []
        })

    query_lower = query.lower()

    if media_type == "movie":

        source = get_movies()

    elif media_type == "series":

        source = get_series()

    else:

        source = get_all_items()

    results = []

    for item in source:

        title = clean_text(
            item.get(
                "title",
                ""
            )
        )

        genres = " ".join(
            item.get(
                "genres",
                []
            )
        )

        searchable = (
            title
            + " "
            + genres
            + " "
            + str(
                item.get(
                    "year",
                    ""
                )
            )
        ).lower()

        if query_lower in searchable:

            # أولوية العنوان
            title_lower = title.lower()

            if title_lower == query_lower:
                score = 0

            elif title_lower.startswith(
                query_lower
            ):
                score = 1

            elif query_lower in title_lower:
                score = 2

            else:
                score = 3

            results.append(
                (
                    score,
                    title_lower,
                    item
                )
            )

    results.sort(
        key=lambda x: (
            x[0],
            x[1]
        )
    )

    limit = request.args.get(
        "limit",
        50
    )

    try:
        limit = min(
            max(
                int(limit),
                1
            ),
            100
        )
    except Exception:
        limit = 50

    data = [
        item
        for _, _, item
        in results[:limit]
    ]

    return jsonify({
        "success": True,
        "query": query,
        "type": media_type,
        "total": len(results),
        "data": data
    })


# ============================================================
# ITEM BY ID / URL
# ============================================================

@app.route("/api/item")
def api_item():

    item_id = clean_text(
        request.args.get(
            "id",
            ""
        )
    )

    item_url = clean_text(
        request.args.get(
            "url",
            ""
        )
    )

    if not item_id and not item_url:

        return jsonify({
            "success": False,
            "error": "id أو url مطلوب"
        }), 400

    items = get_all_items()

    for item in items:

        if item_id:

            if str(
                item.get("id", "")
            ) == item_id:

                return jsonify({
                    "success": True,
                    "data": item
                })

        if item_url:

            if item.get(
                "link"
            ) == item_url:

                return jsonify({
                    "success": True,
                    "data": item
                })

    return jsonify({
        "success": False,
        "error": "العنصر غير موجود"
    }), 404


# ============================================================
# HOME FEED
# ============================================================

@app.route("/api/v1/feed/<category>")
def api_feed(category):

    category = category.lower().strip()

    page = request.args.get(
        "page",
        1
    )

    limit = request.args.get(
        "limit",
        24
    )

    if category == "movies":

        items = get_movies()

    elif category == "series":

        items = get_series()

    elif category in (
        "home",
        "all",
        "shows"
    ):

        items = get_all_items()

    else:

        return jsonify({
            "success": False,
            "error": "تصنيف غير صالح",
            "allowed": [
                "home",
                "movies",
                "series",
                "shows"
            ]
        }), 404

    result = paginate(
        items,
        page,
        limit
    )

    return jsonify({
        "success": True,
        "source": "catalog",
        "category": category,
        **result
    })


# ============================================================
# PAGES
# ============================================================

@app.route("/api/pages")
def api_pages():

    page_type = clean_text(
        request.args.get(
            "type",
            ""
        )
    ).lower()

    with _db_lock:

        data = []

        for url, item in pages_db.items():

            if page_type:

                if item.get(
                    "type"
                ) != page_type:

                    continue

            data.append({
                "url": url,
                "type": item.get(
                    "type"
                ),
                "page": item.get(
                    "page"
                ),
                "items_count": item.get(
                    "items_count",
                    0
                ),
                "fetched_at": item.get(
                    "fetched_at"
                )
            })

    data.sort(
        key=lambda x: (
            x.get("type") or "",
            x.get("page")
            if x.get("page") is not None
            else 0
        )
    )

    return jsonify({
        "success": True,
        "total": len(data),
        "data": data
    })


# ============================================================
# PAGE BY URL
# ============================================================

@app.route("/api/page")
def api_page():

    url = request.args.get(
        "url",
        ""
    ).strip()

    if not url:

        return jsonify({
            "success": False,
            "error": "url مطلوب"
        }), 400

    with _db_lock:

        item = pages_db.get(
            url
        )

    if not item:

        return jsonify({
            "success": False,
            "error": "الصفحة غير موجودة في pages_db"
        }), 404

    # لا نعيد HTML افتراضياً
    # حتى لا تكون الاستجابة ضخمة.
    return_html = (
        request.args.get(
            "html"
        ) == "1"
    )

    response = {
        "success": True,
        "url": url,
        "type": item.get(
            "type"
        ),
        "page": item.get(
            "page"
        ),
        "items_count": item.get(
            "items_count",
            0
        ),
        "fetched_at": item.get(
            "fetched_at"
        )
    }

    if return_html:
        response["html"] = item.get(
            "html",
            ""
        )

    return jsonify(response)


# ============================================================
# EXPORT CATALOG
# ============================================================

@app.route("/api/export")
def api_export():

    export_data = {
        "success": True,
        "updated_at": catalog.get(
            "updated_at"
        ),
        "movies": get_movies(),
        "series": get_series()
    }

    return jsonify(
        export_data
    )


# ============================================================
# REFRESH
# ============================================================

@app.route(
    "/api/refresh",
    methods=["POST"]
)
def api_refresh():

    with _db_lock:

        if _is_loading:

            return jsonify({
                "success": False,
                "message": "جاري التحديث بالفعل"
            }), 409

    thread = threading.Thread(
        target=populate_pages_db_background,
        daemon=True
    )

    thread.start()

    return jsonify({
        "success": True,
        "message": "بدأ تحديث البيانات في الخلفية"
    })


# ============================================================
# REBUILD ONLY
# ============================================================

@app.route(
    "/api/rebuild",
    methods=["POST"]
)
def api_rebuild():

    try:

        rebuild_catalog()

        return jsonify({
            "success": True,
            "message": "تمت إعادة بناء Catalog",
            "movies": len(
                get_movies()
            ),
            "series": len(
                get_series()
            )
        })

    except Exception as e:

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============================================================
# FRONTEND
# ============================================================

@app.route("/")
def index():

    return render_template(
        "index.html"
    )


@app.route("/watch")
def watch():

    # إذا كان لديك watch.html
    # سيعمل بشكل طبيعي.
    return render_template(
        "watch.html"
    )


# ============================================================
# STARTUP
# ============================================================

load_database()


# إذا كان Catalog موجوداً فلا نحتاج
# إعادة تحليل HTML عند كل تشغيل.
#
# إذا كانت قاعدة البيانات فارغة،
# يبدأ crawler.

if (
    len(catalog["movies"]) == 0
    and len(catalog["series"]) == 0
):

    start_background_fetch()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "5001"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )

# ============================================================
# 3. KILL OLD PROCESSES
# ============================================================
!pkill -f flask 2>/dev/null
!pkill -f ngrok 2>/dev/null
import time
time.sleep(2)

# ============================================================
# 4. SETUP NGROK
# ============================================================
from pyngrok import ngrok

NGROK_AUTH_TOKEN = "3IQ8IzKiKHDrXl7mxEksppOvkBE_6BxHZQX5mwHXNs3uL4GtU"
ngrok.set_auth_token(NGROK_AUTH_TOKEN)
print("✅ ngrok authenticated.")

# ============================================================
# 5. START FLASK IN BACKGROUND
# ============================================================
import subprocess
import threading

def run_flask():
    subprocess.run(["python", "app.py"])

thread = threading.Thread(target=run_flask, daemon=True)
thread.start()
time.sleep(5)  # Allow Flask to start

print("✅ Flask server started on port 5001")

# ============================================================
# 6. CREATE NGROK TUNNEL
# ============================================================
try:
    public_url = ngrok.connect(5001, "http")
    print(f"\n✅ PUBLIC URL: {public_url}")
    print("\n🔗 USE THIS URL FOR YOUR REQUESTS:")
    print(f"   - {public_url}/api/movies")
    print(f"   - {public_url}/api/all-movies")
    print(f"   - {public_url}/api/all-series")
    print(f"   - {public_url}/api/search?q=Dark")
    print(f"   - {public_url}/api/status")
except Exception as e:
    print(f"❌ Failed to create ngrok tunnel: {e}")
    print("📌 You can access the server locally at http://127.0.0.1:5001")

# ============================================================
# 7. TEST THE NEW URL
# ============================================================
if 'public_url' in locals():
    print("\n🧪 Testing /api/movies...")
    import requests
    try:
        res = requests.get(f"{public_url}/api/movies", timeout=10)
        print(f"Status: {res.status_code}")
        if res.ok:
            data = res.json()
            print(f"✅ Success! Found {data.get('total', 0)} movies.")
        else:
            print(f"⚠️ Response: {res.text[:200]}")
    except Exception as e:
        print(f"❌ Test failed: {e}")
