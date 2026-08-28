import os
import json
import re
import time
import threading
from datetime import datetime, timezone
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)
CORS(app)

# ======== الإعدادات ========
BASE_URL = "https://ak.sv"
DATA_DIR = "data"
MOVIES_FILE = os.path.join(DATA_DIR, "movies.json")
SERIES_FILE = os.path.join(DATA_DIR, "series.json")
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")

MOVIES_MAX_PAGES = 400
SERIES_MAX_PAGES = 250
FETCH_DELAY = 0.5
REQUEST_TIMEOUT = 30

# ======== الكوكيز والهيدرز المستخرجة من طلب ناجح ========
COOKIES = {
    "HstCfa4403638": "1787511457024",
    "HstCmu4403638": "1787511457024",
    "__dtsu": "6D0017877739451FCBABA677687BFB08",
    "_gid": "GA1.2.1661775904.1787774035",
    "_pubcid": "d02d3583-bd2b-4f46-906b-4fb6949ef354",
    "_cc_id": "b129b91fb9960972158bd9ce2d40d79e",
    "_cc_cc": "ACZ4nGNQSDI0skyyNExLsrQ0M7A0NzI0tUhKsUxONUoxMUgxt0xlAIKs%2FnrXy53%2F%2F%2FMzwIDQzJ8LWBglz%2F8FCv5nZGRo%2BGLJ%2BFH2wX8od93rF7rI%2FF9T%2Fpsj888dPcSMzN%2B977IAMv%2FkfXVk7uHFc1iQ%2BZdOPWJD5k89hmrcuyWo6v9snILCv3warB%2Fu%2BnOnUPmXIfYh%2BB%2FvWCDzDy6biiK%2FGU09AD9IoR4%3D",
    "_cc_aud": "ABR4nGNgYGDI6q93ZYADABYMAb4%3D",
    "panoramaId_expiry": "1787875525004",
    "XSRF-TOKEN": "eyJpdiI6Im92M1puWjNUOE05MXVFQ3lpR1JmdkE9PSIsInZhbHVlIjoiUmZHVjVNTVhoN3VpVWl0RmpKT3pic21FUVgrdWRTYW1cL3ZmbmZUVHcxSDVrd3NqbmdYSmR5QW9iYTMzaU9LY3QiLCJtYWMiOiJlNjExNjEwOWYwZWY4ZTM2NDE4Y2M4M2M5ZjI5Njg1NTgxNTNhZGRkN2ZhMzQzZmNmYzQ5MDYxNzUxMTAzNjMyIn0%3D",
    "akwam_session": "eyJpdiI6IldLdUVSN3BzeVplYXRIZ3NqdXNCQWc9PSIsInZhbHVlIjoidjRpSEJzdXZXWmdLU3J2SGc0TzhINTNLVEk3WjJaR3NyUUxjVGFyUXdYeXZBYmVsbkx4XC9FT2xZS3pmR2V0RzkiLCJtYWMiOiIzOTQ0NGNmYTljYmNhNTdiYzc2Mzg2M2ZjNzRhNDA3NmI0ZjMxMGUzYzdlNTY0MzFkOGQwZWVkYTg2ZWUzMTJhIn0%3D",
    "mymlcksi3OCGvijCdihHCYDpQeVlJWV9hMuNk2hY": "eyJpdiI6Im8yVnZ3bVdmVDZ4bmR1XC9UVytwaTJBPT0iLCJ2YWx1ZSI6IlJablVpSGNBbkl5SytNRlpYQWs1b0hla1ppQytNOFg3c1wveGp6T0ZZN1lZUTJRWG1QeVNBZmdcLzluSk1WQVNsdXVDM1FjbmQrRGVva01PNURaaXMzelFyMXVRTytSN3ZMcHBodVFlTG9nZmh5YWREdThoV3lRb29VaTRaUkk4dlBRQ200emtoVFNWMEtxQmVSZkZYR0IyODViazM0Y3RIS2xsSDlIZ2RQOFptY1NFRm80d2dIamdhXC9aVGlCR0syS3QwYUFIaWJhdW9uaklzWGRCdkxFXC9zR054aG9PTVJYT3M3Mk54V3l0Vm52RUx0MjRpMlpPWkJCNDVqaVhrcXFzb2ZXbndxdkEyakwyaVRHTG9TV3V2NEZlZ21BelQzXC96VytkbnRmYUNSbWozTDhGTUoweTkxcnBaK3o5dWZhTVgiLCJtYWMiOiI4NzcyOTExYzI5NmQ4ZjcwY2FiNDNmMGZmODYyNjkxYTU0MjgwMzA0M2FhMzQxYWJhM2Q2NGY5YzQ3ODM0NTVkIn0%3D",
    "HstCnv4403638": "6",
    "HstCns4403638": "15",
    "cf_clearance": "VIoTNdlP6WFsIfScfuAGREaIGfQVs6dQWG4hdhW2dMA-1787875359-1.2.1.1-xTBYR_.YjKcQWohLsXFKU9yhfy5bzMgvmlhISQrkRRHEvwCLsVdqYSLPXIPnvvYfnnGG8MV9ilE00ZWtefcQEwD20IfIYHQRL0tNJ8SYfM7bguk7GTsS6RU.fv2PMtLja0TC5T2unabmpBrvZhiCpzns9nAmDpYImJXca_JiUkBwTeRDT3B8WLrPK8xorZ3HuM6YWbDmzbnwR0RpZgp.9J_T7OTZWo3VtUINPypX35gpXR7LV0omNHr2fIubxgBaS742lufd_W8RdWqYb5ZUdcOmw6lyhBKGTlV5_ScY4yikaGwAzXIizpA60kSFs3uE75EoPsWHzX6WvRUqL0FoD8ofSKPSr9WeqDw8K1z0msw",
    "_gat_gtag_UA_262083515_1": "1",
    "_ga_LYBJP286GM": "GS2.1.s1787875420$o11$g1$t1787875437$j43$l0$h0",
    "_ga": "GA1.1.1705875142.1787511453",
    "HstCla4403638": "1787875437888",
    "HstPn4403638": "3",
    "HstPt4403638": "38"
}

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Referer": "https://ak.sv/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://ak.sv",
    "Sec-Ch-Ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
    "Sec-Ch-Ua-Mobile": "?1",
    "Sec-Ch-Ua-Platform": '"Android"',
    "Priority": "u=1, i",
}

# ======== إنشاء جلسة طلبات ========
session = requests.Session()
session.headers.update(HEADERS)
session.cookies.update(COOKIES)

# ======== المتغيرات العامة ========
catalog = {"movies": [], "series": [], "updated_at": None}
_is_loading = False
_db_lock = threading.RLock()

# ======== دوال مساعدة ========
def utc_now():
    return datetime.now(timezone.utc).isoformat()

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)

def load_json_file(path, default=None):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return default

def save_json_file(path, data):
    ensure_data_dir()
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

# ======== تحميل البيانات من القرص عند البدء ========
def load_catalog_from_disk():
    global catalog
    movies = load_json_file(MOVIES_FILE, [])
    series = load_json_file(SERIES_FILE, [])
    metadata = load_json_file(METADATA_FILE, {})
    if movies or series:
        with _db_lock:
            catalog["movies"] = movies
            catalog["series"] = series
            catalog["updated_at"] = metadata.get("updated_at")
        print(f"📦 تحميل من القرص: {len(movies)} فيلم, {len(series)} مسلسل")
        return True
    return False

# ======== دوال الجلب باستخدام الجلسة ========
def fetch_page(url):
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.text

def fetch_all_pages():
    global _is_loading, catalog
    with _db_lock:
        if _is_loading:
            return
        _is_loading = True

    try:
        print("🚀 بدء جلب جميع الصفحات باستخدام الجلسة المصرح بها...")
        fetched_movies = []
        fetched_series = []

        # جلب الصفحة الرئيسية أولاً لتحديث الجلسة إن لزم
        try:
            session.get("https://ak.sv/", timeout=10)
        except:
            pass

        # جلب صفحات الأفلام
        for page in range(MOVIES_MAX_PAGES):
            # المسار الفعلي للصفحات هو /v/movies/0 وليس /movies?page=0
            url = f"https://ak.sv/v/movies/{page}"
            print(f"⏳ [Movies] page {page}")
            try:
                html = fetch_page(url)
                soup = BeautifulSoup(html, "html.parser")
                items = soup.select(".entry-box-1")
                if not items:
                    print(f"🛑 لا توجد عناصر في page {page}، توقف.")
                    break
                movies = extract_movies_from_html(html)
                fetched_movies.extend(movies)
                print(f"✅ page {page} ({len(items)} عناصر)")
                time.sleep(FETCH_DELAY)
            except Exception as e:
                print(f"❌ خطأ في page {page}: {e}")
                break

        # جلب صفحات المسلسلات
        for page in range(SERIES_MAX_PAGES):
            url = f"https://ak.sv/v/series/{page}"
            print(f"⏳ [Series] page {page}")
            try:
                html = fetch_page(url)
                soup = BeautifulSoup(html, "html.parser")
                items = soup.select(".entry-box-1")
                if not items:
                    print(f"🛑 لا توجد عناصر في page {page}، توقف.")
                    break
                series = extract_series_from_html(html)
                fetched_series.extend(series)
                print(f"✅ page {page} ({len(items)} عناصر)")
                time.sleep(FETCH_DELAY)
            except Exception as e:
                print(f"❌ خطأ في page {page}: {e}")
                break

        # حذف المكررات
        def deduplicate(items):
            seen = set()
            unique = []
            for item in items:
                key = item.get("link")
                if key and key not in seen:
                    seen.add(key)
                    unique.append(item)
            return unique

        with _db_lock:
            catalog["movies"] = deduplicate(fetched_movies)
            catalog["series"] = deduplicate(fetched_series)
            catalog["updated_at"] = utc_now()

        save_json_file(MOVIES_FILE, catalog["movies"])
        save_json_file(SERIES_FILE, catalog["series"])
        save_json_file(METADATA_FILE, {"updated_at": catalog["updated_at"]})

        print(f"✅ اكتمل الجلب: {len(catalog['movies'])} فيلم, {len(catalog['series'])} مسلسل")
    except Exception as e:
        print(f"❌ خطأ عام في الجلب: {e}")
    finally:
        with _db_lock:
            _is_loading = False

# ======== دوال الـ API ========
def get_movies():
    with _db_lock:
        return list(catalog["movies"])

def get_series():
    with _db_lock:
        return list(catalog["series"])

def paginate(items, page, limit=24):
    try:
        page = max(0, int(page))
    except:
        page = 0
    try:
        limit = max(1, min(int(limit), 100))
    except:
        limit = 24
    total = len(items)
    start = page * limit
    end = start + limit
    data = items[start:end]
    return {
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": (total + limit - 1) // limit if total else 0,
        "hasMore": end < total,
        "data": data
    }

# ======== نقاط النهاية (Endpoints) ========
@app.route('/')
def index():
    return jsonify({"message": "🚀 خادم أكوام برو يعمل. استخدم /api/v1/feed/movies?page=0"})

@app.route('/api/v1/feed/movies')
def feed_movies():
    page = request.args.get("page", 0)
    limit = request.args.get("limit", 24)
    movies = get_movies()
    result = paginate(movies, page, limit)
    result["success"] = True
    result["category"] = "movies"
    return jsonify(result)

@app.route('/api/v1/feed/series')
def feed_series():
    page = request.args.get("page", 0)
    limit = request.args.get("limit", 24)
    series = get_series()
    result = paginate(series, page, limit)
    result["success"] = True
    result["category"] = "series"
    return jsonify(result)

@app.route('/api/all-movies')
def api_all_movies():
    return jsonify({"success": True, "total": len(get_movies()), "data": get_movies()})

@app.route('/api/all-series')
def api_all_series():
    return jsonify({"success": True, "total": len(get_series()), "data": get_series()})

@app.route('/api/status')
def api_status():
    with _db_lock:
        return jsonify({
            "success": True,
            "ready": bool(catalog["movies"] or catalog["series"]),
            "loading": _is_loading,
            "movies": len(catalog["movies"]),
            "series": len(catalog["series"]),
            "updated_at": catalog.get("updated_at")
        })

@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    if _is_loading:
        return jsonify({"success": False, "message": "جاري التحديث بالفعل"}), 409
    thread = threading.Thread(target=fetch_all_pages, daemon=True)
    thread.start()
    return jsonify({"success": True, "message": "بدأ تحديث البيانات في الخلفية"})

@app.route('/api/movie-links')
def movie_links():
    url = request.args.get("url")
    if not url:
        return jsonify({"success": False, "error": "url مطلوب"}), 400
    try:
        html = fetch_page(url)
        soup = BeautifulSoup(html, "html.parser")
        video = soup.find("video")
        if not video:
            return jsonify({"success": True, "links": []})
        links = []
        for source in video.find_all("source"):
            src = source.get("src")
            if not src:
                continue
            clean = re.sub(r'^https://ak\.sv(vlc://|intent:)', '', src)
            clean = re.sub(r'^vlc://|^intent:', '', clean)
            clean = clean.split('#Intent;')[0] if '#Intent;' in clean else clean
            if clean.startswith('http'):
                quality = source.get("size", "")
                links.append({
                    "quality": quality or "SD",
                    "watch": clean,
                    "download": clean
                })
        return jsonify({"success": True, "links": links})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/series-episodes')
def series_episodes():
    url = request.args.get("url")
    if not url:
        return jsonify({"success": False, "error": "url مطلوب"}), 400
    try:
        html = fetch_page(url)
        soup = BeautifulSoup(html, "html.parser")
        episodes = []
        for item in soup.select('#series-episodes .bg-primary2'):
            link_el = item.select_one('h2 a') or item.select_one('a')
            if not link_el:
                continue
            href = link_el.get('href')
            if not href:
                continue
            full_url = absolute_url(href)
            title = clean_text(link_el.get_text(" ", strip=True))
            num_match = re.search(r'\d+', title) or re.search(r'\d+', href)
            number = num_match.group(0) if num_match else '?'
            episodes.append({
                "number": number,
                "title": title,
                "url": full_url
            })
        return jsonify({"success": True, "episodes": episodes})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/search')
def api_search():
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

# ======== تهيئة الخادم ========
if not load_catalog_from_disk():
    print("🔄 لا توجد بيانات، بدء الجلب الخلفي...")
    threading.Thread(target=fetch_all_pages, daemon=True).start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
