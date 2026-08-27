# ============================================
# app.py – خادم Flask مع جميع الـ APIs وقاعدة البيانات pages_db
# ============================================
from flask import Flask, render_template, request, jsonify, Response
from flask_cors import CORS
import requests
import json
import re
import time
import threading
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, quote

app = Flask(__name__)

# ======================================================
#  🔥 إعدادات CORS المتقدمة
# ======================================================
CORS(app, 
     origins="*",
     allow_headers=["Content-Type", "Accept", "Authorization", "X-Requested-With"],
     methods=["GET", "POST", "OPTIONS", "PUT", "DELETE"],
     expose_headers=["Content-Range", "X-Content-Range"],
     supports_credentials=False,
     max_age=600)

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Accept, Authorization, X-Requested-With'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS, PUT, DELETE'
    response.headers['Access-Control-Allow-Credentials'] = 'false'
    return response

# ------------------- الإعدادات -------------------
HEADERS = {
    'Referer': 'https://ak.sv/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# ======================================================
#  📦 قاعدة البيانات المؤقتة (في الذاكرة)
# ======================================================
pages_db = {}          # المفتاح: الرابط، القيمة: {'html': str, 'type': str}
_is_loading = False    # علم لمنع تشغيل الجلب مرتين
_background_started = False

# ======================================================
#  📦 بيانات احتياطية (Fallback) في حال تعذر الجلب
# ======================================================
FALLBACK_MEDIA = [
    {
        'id': '11330',
        'title': 'Dark',
        'link': 'https://ak.sv/movie/11330/dark',
        'image': 'https://img.downet.net/thumb/178x260/uploads/Ea5Bm.jpg',
        'rating': '6.1',
        'quality': 'WEB-DL',
        'year': '2026',
        'genres': ['رعب', 'اثارة'],
        'type': 'movie'
    },
    {
        'id': '11329',
        'title': 'Motor City',
        'link': 'https://ak.sv/movie/11329/motor-city',
        'image': 'https://img.downet.net/thumb/178x260/uploads/599zZ.jpg',
        'rating': '6.1',
        'quality': 'WEB-DL',
        'year': '2026',
        'genres': ['اكشن', 'اثارة', 'جريمة'],
        'type': 'movie'
    },
    {
        'id': '5697',
        'title': 'مسلسل Lucky الموسم الأول',
        'link': 'https://ak.sv/series/5697/lucky',
        'image': 'https://img.downet.net/thumb/178x260/uploads/nRnUq.jpg',
        'rating': '7.0',
        'quality': 'WEB-DL',
        'year': '2026',
        'genres': ['اثارة', 'دراما', 'جريمة'],
        'type': 'series'
    },
    {
        'id': '5680',
        'title': 'مسلسل House of the Dragon الموسم الثاني',
        'link': 'https://ak.sv/series/5680/house-of-the-dragon',
        'image': 'https://img.downet.net/thumb/178x260/uploads/UdjTF.jpg',
        'rating': '8.7',
        'quality': '1080p WebRip',
        'year': '2025',
        'genres': ['اكشن', 'دراما', 'فانتازيا', 'مغامرة'],
        'type': 'series'
    },
    {
        'id': '5685',
        'title': 'مسلسل الحشاشين',
        'link': 'https://ak.sv/series/5685/al-hashashin',
        'image': 'https://img.downet.net/thumb/178x260/uploads/hG2Et.jpg',
        'rating': '8.9',
        'quality': 'HDTV 1080p',
        'year': '2025',
        'genres': ['تاريخي', 'دراما', 'اكشن'],
        'type': 'series'
    }
]

# ------------------- دوال الاستخراج -------------------
def extract_movies_from_html(html, base_url='https://ak.sv'):
    soup = BeautifulSoup(html, 'html.parser')
    movies = []
    for item in soup.select('.entry-box-1'):
        title_el = item.select_one('.entry-title a')
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        link = title_el.get('href')
        if link and not link.startswith('http'):
            link = urljoin(base_url, link)
        img_el = item.select_one('img')
        img_src = img_el.get('data-src') or img_el.get('src') if img_el else ''
        if img_src and not img_src.startswith('http'):
            img_src = urljoin(base_url, img_src)
        rating_el = item.select_one('.label.rating')
        rating = rating_el.get_text(strip=True).replace('⭐', '').strip() if rating_el else '0.0'
        year_el = item.select_one('.badge-secondary')
        year = year_el.get_text(strip=True) if year_el else '----'
        genre_els = item.select('.badge-light')
        genres = [g.get_text(strip=True) for g in genre_els]
        quality_el = item.select_one('.label.quality')
        quality = quality_el.get_text(strip=True) if quality_el else ''
        movies.append({
            'title': title,
            'link': link,
            'image': img_src,
            'rating': rating,
            'year': year,
            'genres': genres,
            'quality': quality,
            'type': 'movie'
        })
    return movies

def extract_series_from_html(html):
    series = extract_movies_from_html(html)
    for item in series:
        item['type'] = 'series'
    return series

def extract_episodes_from_html(html, base_url='https://ak.sv'):
    soup = BeautifulSoup(html, 'html.parser')
    episodes = []
    for item in soup.select('#series-episodes .bg-primary2'):
        link_el = item.select_one('h2 a')
        if not link_el:
            continue
        title = link_el.get_text(strip=True)
        href = link_el.get('href')
        if href and not href.startswith('http'):
            href = urljoin(base_url, href)
        img_el = item.select_one('img')
        img_src = img_el.get('data-src') or img_el.get('src') if img_el else ''
        if img_src and not img_src.startswith('http'):
            img_src = urljoin(base_url, img_src)
        number_match = re.search(r'\d+', title) or re.search(r'\d+', href)
        number = number_match.group(0) if number_match else '?'
        episodes.append({
            'number': number,
            'title': title,
            'url': href,
            'image': img_src
        })
    return episodes

def extract_video_sources(html):
    soup = BeautifulSoup(html, 'html.parser')
    video = soup.find('video')
    if not video:
        return []
    sources = []
    for source in video.find_all('source'):
        src = source.get('src')
        if src:
            clean = re.sub(r'^https://ak\.sv(vlc://|intent:)', '', src)
            clean = re.sub(r'^vlc://|^intent:', '', clean)
            clean = clean.split('#Intent;')[0] if '#Intent;' in clean else clean
            if clean.startswith('https://'):
                sources.append({
                    'src': clean,
                    'type': source.get('type', 'video/mp4'),
                    'size': source.get('size', '')
                })
    return sources

def clean_video_url(raw_url):
    """تنظيف روابط الفيديو من الشوائب (مثل vlc://)"""
    if not raw_url:
        return ''
    cleaned = re.sub(r'^(https?://ak\.sv)?(vlc://|intent:)', '', raw_url)
    cleaned = cleaned.split('#Intent')[0]
    return cleaned

# ------------------- دوال جلب الصفحات (تعمل في الخلفية) -------------------
def fetch_all_pages(base_url, max_pages=500, delay=0.5):
    all_html = []
    page = 0
    while page < max_pages:
        url = f"{base_url}?page={page}" if '?' not in base_url else f"{base_url}&page={page}"
        print(f"⏳ [Background] جلب {url} ...")
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            if response.status_code != 200:
                print(f"⚠️ توقف: استجابة {response.status_code} في الصفحة {page}")
                break
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')
            items = soup.select('.entry-box-1')
            if not items:
                print(f"✅ لا توجد عناصر في الصفحة {page}، نتوقف.")
                break
            all_html.append(html)
            print(f"✅ تم جلب الصفحة {page} (عدد العناصر: {len(items)})")
            page += 1
            time.sleep(delay)
        except Exception as e:
            print(f"❌ خطأ في الصفحة {page}: {e}")
            break
    return all_html

def populate_pages_db_background():
    global _is_loading, pages_db
    if _is_loading:
        return
    _is_loading = True
    print("🚀 بدء جلب جميع الصفحات في الخلفية...")

    try:
        movies_html = fetch_all_pages('https://ak.sv/movies', max_pages=400, delay=0.5)
        for idx, html in enumerate(movies_html):
            pages_db[f"https://ak.sv/movies?page={idx}"] = {'html': html, 'type': 'movies'}

        series_html = fetch_all_pages('https://ak.sv/series', max_pages=250, delay=0.5)
        for idx, html in enumerate(series_html):
            pages_db[f"https://ak.sv/series?page={idx}"] = {'html': html, 'type': 'series'}

        print(f"✅ اكتمل الجلب! إجمالي الصفحات: {len(pages_db)}")
    except Exception as e:
        print(f"❌ فشل الجلب الخلفي: {e}")
    finally:
        _is_loading = False

def start_background_fetch():
    global _background_started
    if not _background_started:
        _background_started = True
        thread = threading.Thread(target=populate_pages_db_background)
        thread.daemon = True
        thread.start()
        print("✅ تم إطلاق خيط الجلب الخلفي.")

# ------------------- دوال جلب الروابط التفصيلية -------------------
def get_movie_links(movie_url):
    if movie_url in pages_db:
        html = pages_db[movie_url]['html']
    else:
        try:
            r = requests.get(movie_url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                return []
            html = r.text
            pages_db[movie_url] = {'html': html, 'type': 'movie_detail'}
        except:
            return []
    
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    tabs = soup.select('.tab-content.quality')
    for tab in tabs:
        quality_id = tab.get('id', '')
        quality_map = {'tab-4': '720p', 'tab-3': '480p', 'tab-5': '1080p', 'tab-6': '4K'}
        quality = quality_map.get(quality_id, quality_id.replace('tab-', ''))
        watch_el = tab.select_one('a.link-show')
        download_el = tab.select_one('a.link-download')
        watch = watch_el.get('href') if watch_el else None
        download = download_el.get('href') if download_el else None
        if watch:
            links.append({'quality': quality, 'watch': watch, 'download': download})
    
    if not links:
        sources = extract_video_sources(html)
        for src in sources:
            links.append({
                'quality': src.get('size', 'unknown'),
                'watch': src['src'],
                'download': src['src']
            })
    return links

def get_series_episodes(series_url):
    if series_url in pages_db:
        html = pages_db[series_url]['html']
    else:
        try:
            r = requests.get(series_url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                return []
            html = r.text
            pages_db[series_url] = {'html': html, 'type': 'series_detail'}
        except:
            return []
    return extract_episodes_from_html(html)

# ------------------- دوال مساعدة للبحث -------------------
def get_all_movies():
    all_movies = []
    for url, data in pages_db.items():
        if data['type'] == 'movies':
            all_movies.extend(extract_movies_from_html(data['html']))
    return all_movies

def get_all_series():
    all_series = []
    for url, data in pages_db.items():
        if data['type'] == 'series':
            all_series.extend(extract_series_from_html(data['html']))
    return all_series

# ======================================================
#  📌 نقاط النهاية (APIs) – كلها تعمل مع pages_db
# ======================================================

# ---------- الحالة ----------
@app.route('/api/status')
def api_status():
    """حالة الخادم وعدد العناصر المحملة"""
    movies_count = len(get_all_movies())
    series_count = len(get_all_series())
    return jsonify({
        'success': True,
        'movies': movies_count,
        'series': series_count,
        'pages': len(pages_db),
        'loading': _is_loading,
        'ready': not _is_loading and len(pages_db) > 0
    })

# ---------- جميع الأفلام ----------
@app.route('/api/all-movies')
def api_all_movies():
    if not pages_db:
        return jsonify({'success': False, 'error': 'البيانات لا تزال تُحمّل'}), 503
    all_movies = get_all_movies()
    return jsonify({'success': True, 'total': len(all_movies), 'data': all_movies})

# ---------- جميع المسلسلات ----------
@app.route('/api/all-series')
def api_all_series():
    if not pages_db:
        return jsonify({'success': False, 'error': 'البيانات لا تزال تُحمّل'}), 503
    all_series = get_all_series()
    return jsonify({'success': True, 'total': len(all_series), 'data': all_series})

# ---------- أفلام الصفحة الأولى ----------
@app.route('/api/movies')
def api_movies():
    if not pages_db:
        return jsonify({'success': False, 'error': 'البيانات لا تزال تُحمّل'}), 503
    for url, data in pages_db.items():
        if data['type'] == 'movies':
            movies = extract_movies_from_html(data['html'])
            return jsonify({'success': True, 'data': movies})
    return jsonify({'error': 'لا توجد بيانات'}), 404

# ---------- مسلسلات الصفحة الأولى ----------
@app.route('/api/series')
def api_series():
    if not pages_db:
        return jsonify({'success': False, 'error': 'البيانات لا تزال تُحمّل'}), 503
    for url, data in pages_db.items():
        if data['type'] == 'series':
            series = extract_series_from_html(data['html'])
            return jsonify({'success': True, 'data': series})
    return jsonify({'error': 'لا توجد بيانات'}), 404

# ---------- البحث ----------
@app.route('/api/search')
def api_search():
    """البحث في الأفلام والمسلسلات"""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'success': True, 'data': []})
    
    results = []
    for movie in get_all_movies():
        if query.lower() in movie['title'].lower():
            results.append(movie)
    for series in get_all_series():
        if query.lower() in series['title'].lower():
            results.append(series)
    
    results.sort(key=lambda x: x['title'].lower().find(query.lower()))
    return jsonify({'success': True, 'data': results[:50]})

# ---------- تحديث البيانات ----------
@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    """إعادة جلب جميع البيانات في الخلفية"""
    if _is_loading:
        return jsonify({'success': False, 'message': 'جاري التحميل بالفعل'}), 409
    thread = threading.Thread(target=populate_pages_db_background)
    thread.daemon = True
    thread.start()
    return jsonify({'success': True, 'message': 'بدأ تحديث البيانات في الخلفية'})

# ---------- روابط الفيلم ----------
@app.route('/api/movie-links')
def movie_links():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'رابط مطلوب'}), 400
    links = get_movie_links(url)
    return jsonify({'success': True, 'links': links})

# ---------- حلقات المسلسل ----------
@app.route('/api/series-episodes')
def series_episodes():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'رابط مطلوب'}), 400
    episodes = get_series_episodes(url)
    return jsonify({'success': True, 'episodes': episodes})

# ---------- وكيل الفيديو (Proxy) ----------
@app.route('/api/stream')
def stream_video():
    video_url = request.args.get('url')
    if not video_url:
        return jsonify({'error': 'رابط الفيديو مطلوب'}), 400
    try:
        r = requests.get(video_url, headers=HEADERS, stream=True, timeout=30)
        r.raise_for_status()
        return Response(r.iter_content(chunk_size=8192),
                        content_type=r.headers.get('content-type', 'video/mp4'),
                        headers={'Accept-Ranges': 'bytes'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ---------- وكيل فيديو متقدم (يدعم M3U8 و Range) ----------
@app.route('/api/proxy-video')
def proxy_video():
    video_url = request.args.get('url')
    if not video_url:
        return jsonify({'error': 'Missing url'}), 400

    # منع SSRF (الهجمات الداخلية)
    parsed = urlparse(video_url)
    if parsed.hostname in ['localhost', '127.0.0.1', '0.0.0.0']:
        return jsonify({'error': 'Forbidden'}), 403

    range_header = request.headers.get('Range')
    headers = {
        'User-Agent': request.headers.get('User-Agent', HEADERS['User-Agent']),
        'Referer': 'https://ak.sv/'
    }
    if range_header:
        headers['Range'] = range_header

    try:
        r = requests.get(video_url, headers=headers, stream=True, timeout=25)
        r.raise_for_status()

        content_type = r.headers.get('content-type', '')
        if 'mpegurl' in content_type or video_url.endswith('.m3u8'):
            m3u8_content = r.text
            base_url = video_url[:video_url.rfind('/') + 1]
            lines = m3u8_content.split('\n')
            new_lines = []
            for line in lines:
                if line.strip() and not line.startswith('#'):
                    abs_url = urljoin(base_url, line.strip())
                    new_lines.append(f'/api/proxy-video?url={quote(abs_url)}')
                elif '#EXT-X-STREAM-INF:' in line or '#EXT-X-I-FRAME-STREAM-INF:' in line:
                    new_line = re.sub(r'URI="(.*?)"', 
                                     lambda m: f'URI="/api/proxy-video?url={quote(urljoin(base_url, m.group(1)))}"', 
                                     line)
                    new_lines.append(new_line)
                else:
                    new_lines.append(line)
            return Response('\n'.join(new_lines), content_type=content_type)

        response = Response(r.iter_content(chunk_size=8192), 
                           content_type=r.headers.get('content-type', 'video/mp4'))
        response.headers['Accept-Ranges'] = 'bytes'
        if r.headers.get('content-length'):
            response.headers['Content-Length'] = r.headers['content-length']
        if r.headers.get('content-range'):
            response.headers['Content-Range'] = r.headers['content-range']
        return response

    except Exception as e:
        return jsonify({'error': str(e)}), 502

# ---------- استخراج روابط الفيديو من صفحة HTML ----------
@app.route('/api/get-links')
def get_links():
    page_url = request.args.get('url')
    if not page_url:
        return jsonify({'error': 'Missing url'}), 400
    try:
        r = requests.get(page_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        links = []
        for source in soup.select('video source'):
            src = source.get('src')
            if src:
                links.append({
                    'url': clean_video_url(src),
                    'type': source.get('type', 'video/mp4'),
                    'size': source.get('size', 'unknown')
                })
        if not links:
            pattern = re.compile(r'<source\s+src="([^"]+)"(?:[^>]*size="([^"]+)")?')
            for match in pattern.finditer(r.text):
                links.append({
                    'url': clean_video_url(match.group(1)),
                    'type': 'video/mp4',
                    'size': match.group(2) or 'unknown'
                })
        return jsonify({'success': True, 'links': links})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ---------- التصنيف مع الترقيم (مستوحى من Node.js) ----------
@app.route('/api/v1/feed/<category>')
def api_feed(category):
    page = int(request.args.get('page', 0))
    
    # محاولة الجلب المباشر من المصدر
    try:
        url = f'https://ak.sv/v/{category}/{page}'
        r = requests.get(url, headers=HEADERS, timeout=6)
        items = extract_movies_from_html(r.text)
        if items:
            return jsonify({
                'success': True,
                'source': 'live_scraped',
                'category': category,
                'page': page,
                'count': len(items),
                'hasMore': len(items) >= 12,
                'data': items
            })
    except:
        pass
    
    # Fallback: استخدام البيانات الاحتياطية
    filtered = [m for m in FALLBACK_MEDIA if category == 'home' or m.get('type') == category]
    start = page * 12
    paginated = filtered[start:start+12]
    return jsonify({
        'success': True,
        'source': 'resilient_cached',
        'category': category,
        'page': page,
        'count': len(paginated),
        'hasMore': (start + 12) < len(filtered),
        'data': paginated
    })

# ---------- الصفحات الأمامية ----------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/watch')
def watch():
    return render_template('watch.html')

# ------------------- بدء الجلب الخلفي فوراً -------------------
start_background_fetch()

# ------------------- تشغيل التطبيق -------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)
