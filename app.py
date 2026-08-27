# ============================================
# app.py – خادم Flask مع جلب تدريجي في الخلفية
# ============================================
from flask import Flask, render_template, request, jsonify, Response
from flask_cors import CORS
import requests
import json
import re
import time
import threading
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)
CORS(app)  # لتسهيل الاختبار من متصفحات مختلفة

# ------------------- الإعدادات -------------------
HEADERS = {
    'Referer': 'https://ak.sv/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

pages_db = {}
_is_loading = False
_background_started = False  # علم لمنع التشغيل المتكرر

# ------------------- دوال الاستخراج (نفسها سابقاً) -------------------
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
            'quality': quality
        })
    return movies

def extract_series_from_html(html):
    return extract_movies_from_html(html)

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
        # جلب الأفلام
        movies_html = fetch_all_pages('https://ak.sv/movies', max_pages=400, delay=0.5)
        for idx, html in enumerate(movies_html):
            pages_db[f"https://ak.sv/movies?page={idx}"] = {'html': html, 'type': 'movies'}

        # جلب المسلسلات
        series_html = fetch_all_pages('https://ak.sv/series', max_pages=250, delay=0.5)
        for idx, html in enumerate(series_html):
            pages_db[f"https://ak.sv/series?page={idx}"] = {'html': html, 'type': 'series'}

        print(f"✅ اكتمل الجلب! إجمالي الصفحات: {len(pages_db)}")
    except Exception as e:
        print(f"❌ فشل الجلب الخلفي: {e}")
    finally:
        _is_loading = False

def start_background_fetch():
    """تشغيل خيط الجلب إذا لم يكن قد بدأ من قبل"""
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

# ------------------- نقاط النهاية API -------------------
@app.route('/api/movies')
def api_movies():
    if not pages_db:
        return jsonify({'success': False, 'error': 'البيانات لا تزال تُحمّل، حاول مرة أخرى بعد دقيقة'}), 503
    for url, data in pages_db.items():
        if data['type'] == 'movies':
            movies = extract_movies_from_html(data['html'])
            return jsonify({'success': True, 'data': movies})
    return jsonify({'error': 'لا توجد بيانات'}), 404

@app.route('/api/series')
def api_series():
    if not pages_db:
        return jsonify({'success': False, 'error': 'البيانات لا تزال تُحمّل، حاول مرة أخرى بعد دقيقة'}), 503
    for url, data in pages_db.items():
        if data['type'] == 'series':
            series = extract_series_from_html(data['html'])
            return jsonify({'success': True, 'data': series})
    return jsonify({'error': 'لا توجد بيانات'}), 404

@app.route('/api/all-movies')
def api_all_movies():
    if not pages_db:
        return jsonify({'success': False, 'error': 'البيانات لا تزال تُحمّل'}), 503
    all_movies = []
    for url, data in pages_db.items():
        if data['type'] == 'movies':
            all_movies.extend(extract_movies_from_html(data['html']))
    return jsonify({'success': True, 'total': len(all_movies), 'data': all_movies})

@app.route('/api/all-series')
def api_all_series():
    if not pages_db:
        return jsonify({'success': False, 'error': 'البيانات لا تزال تُحمّل'}), 503
    all_series = []
    for url, data in pages_db.items():
        if data['type'] == 'series':
            all_series.extend(extract_series_from_html(data['html']))
    return jsonify({'success': True, 'total': len(all_series), 'data': all_series})

@app.route('/api/movie-links')
def movie_links():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'رابط مطلوب'}), 400
    links = get_movie_links(url)
    return jsonify({'success': True, 'links': links})

@app.route('/api/series-episodes')
def series_episodes():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'رابط مطلوب'}), 400
    episodes = get_series_episodes(url)
    return jsonify({'success': True, 'episodes': episodes})

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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/watch')
def watch():
    return render_template('watch.html')

# ------------------- بدء الجلب الخلفي فوراً (حتى مع Gunicorn) -------------------
# يتم استدعاؤها بمجرد استيراد الوحدة
start_background_fetch()

# ------------------- (اختياري) تشغيل التطبيق مباشرة للاختبار المحلي -------------------
if __name__ == '__main__':
    # عند التشغيل المحلي بـ python app.py، لن يبدأ خيطاً جديداً لأن start_background_fetch() قد دُعي بالفعل
    # لكننا نضع debug=False لتجنب إعادة التشغيل المتكرر
    app.run(host='0.0.0.0', port=5001, debug=False)
