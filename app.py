# ============================================
# app.py – خادم Flask متكامل مع واجهة أمامية
# ============================================
from flask import Flask, render_template, request, jsonify, Response
import requests
import json
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)

# ------------------- الإعدادات والترويسات -------------------
HEADERS = {
    'Referer': 'https://ak.sv/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# ------------------- قاعدة البيانات المؤقتة -------------------
pages_db = {}  # ستُملأ بالبيانات (من ScraperAPI أو الجلب المباشر)

# دوال استخراج البيانات (مأخوذة من الكود السابق)
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

# ------------------- دوال جلب الروابط -------------------
def get_movie_links(movie_url):
    """جلب روابط المشاهدة والتحميل من صفحة الفيلم"""
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
    # البحث في التبويبات
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
    
    # إذا لم نجد التبويبات، نستخدم عنصر <video>
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
    """جلب قائمة حلقات المسلسل"""
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

# ------------------- تحميل البيانات الأولية (اختياري) -------------------
# هنا يمكنك استدعاء دوال لجلب الصفحات إذا لم تكن موجودة.
# مثلاً: جلب أول صفحة من الأفلام والمسلسلات.

def fetch_initial_data():
    """جلب الصفحات الأساسية لتشغيل الموقع"""
    print("جلب البيانات الأولية...")
    for url in ['https://ak.sv/movies', 'https://ak.sv/series']:
        if url not in pages_db:
            try:
                r = requests.get(url, headers=HEADERS, timeout=10)
                if r.status_code == 200:
                    pages_db[url] = {'html': r.text, 'type': 'movies' if 'movies' in url else 'series'}
                    print(f"تم جلب {url}")
            except Exception as e:
                print(f"فشل جلب {url}: {e}")
    print("تم الانتهاء من جلب البيانات الأولية.")

# ------------------- نقاط النهاية API -------------------
@app.route('/api/movies')
def api_movies():
    """إرجاع قائمة الأفلام (من الصفحة الأولى)"""
    if 'https://ak.sv/movies' in pages_db:
        html = pages_db['https://ak.sv/movies']['html']
        movies = extract_movies_from_html(html)
        return jsonify({'success': True, 'data': movies})
    else:
        return jsonify({'error': 'لم يتم جلب البيانات بعد'}), 404

@app.route('/api/series')
def api_series():
    if 'https://ak.sv/series' in pages_db:
        html = pages_db['https://ak.sv/series']['html']
        series = extract_series_from_html(html)
        return jsonify({'success': True, 'data': series})
    else:
        return jsonify({'error': 'لم يتم جلب البيانات بعد'}), 404

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
    """بث الفيديو عبر الوكيل مع ترويسة Referer"""
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

# ------------------- صفحات الواجهة الأمامية -------------------
@app.route('/')
def index():
    """الصفحة الرئيسية – تعرض قوائم الأفلام والمسلسلات"""
    return render_template('index.html')

@app.route('/watch')
def watch():
    """صفحة المشاهدة – تعرض تفاصيل الفيلم/الحلقة مع المشغل"""
    return render_template('watch.html')

# ------------------- تشغيل الخادم -------------------
if __name__ == '__main__':
    # جلب البيانات الأولية (يمكن تعطيلها إذا كانت لديك بيانات مسبقة)
    fetch_initial_data()
    app.run(host='0.0.0.0', port=5001, debug=True)
