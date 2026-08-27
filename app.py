# ============================================
# app.py – خادم Flask المحدث ليتوافق مع الواجهة الجديدة
# ============================================
from flask import Flask, render_template, request, jsonify, Response
from flask_cors import CORS  # مهم جداً للاتصال عبر ngrok
import requests
import json
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)
CORS(app) # تفعيل CORS لجميع المسارات

# ------------------- الإعدادات والترويسات -------------------
HEADERS = {
    'Referer': 'https://ak.sv/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# ------------------- قاعدة البيانات في الذاكرة -------------------
# سنخزن البيانات هنا بعد جلبها لتسريع الاستجابة للواجهة
all_movies_db = []
all_series_db = []

# دوال استخراج البيانات
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
        movies.append({
            'title': title,
            'link': link,
            'image': img_src,
            'rating': rating,
            'year': year
        })
    return movies

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

def get_movie_links(movie_url):
    try:
        r = requests.get(movie_url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        html = r.text
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
        if watch_el:
            links.append({
                'quality': quality, 
                'watch': watch_el.get('href'), 
                'download': download_el.get('href') if download_el else None
            })
    
    if not links:
        sources = extract_video_sources(html)
        for src in sources:
            links.append({
                'quality': src.get('size', 'unknown'),
                'watch': src['src'],
                'download': src['src']
            })
    return links

# ------------------- جلب البيانات الأولية -------------------
def scrape_pages(base_url, pages_count=3):
    """جلب عدد معين من الصفحات ودمجها في قائمة واحدة"""
    results = []
    for i in range(1, pages_count + 1):
        url = f"{base_url}/page/{i}" if i > 1 else base_url
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                items = extract_movies_from_html(r.text, base_url='https://ak.sv')
                results.extend(items)
                print(f"تم جلب {url}")
        except Exception as e:
            print(f"فشل جلب {url}: {e}")
    return results

def fetch_initial_data():
    global all_movies_db, all_series_db
    print("⏳ جاري جلب الأفلام (أول 5 صفحات)...")
    all_movies_db = scrape_pages('https://ak.sv/movies', pages_count=5)
    
    print("⏳ جاري جلب المسلسلات (أول 5 صفحات)...")
    all_series_db = scrape_pages('https://ak.sv/series', pages_count=5)
    print(f"✅ تم الانتهاء! ({len(all_movies_db)} فيلم و {len(all_series_db)} مسلسل)")

# ------------------- نقاط النهاية (APIs) الجديدة -------------------
@app.route('/api/all-movies')
def api_all_movies():
    """إرجاع جميع الأفلام المخزنة"""
    return jsonify({'success': True, 'data': all_movies_db})

@app.route('/api/all-series')
def api_all_series():
    """إرجاع جميع المسلسلات المخزنة"""
    return jsonify({'success': True, 'data': all_series_db})

@app.route('/api/search')
def api_search():
    """البحث في الأفلام والمسلسلات"""
    query = request.args.get('q', '').lower()
    if not query:
        return jsonify({'success': False, 'error': 'كلمة البحث فارغة'})
    
    # دمج القائمتين للبحث
    combined = all_movies_db + all_series_db
    results = [item for item in combined if query in item['title'].lower()]
    
    return jsonify({'success': True, 'data': results})

@app.route('/api/movie-links')
def movie_links():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'رابط مطلوب'}), 400
    links = get_movie_links(url)
    return jsonify({'success': True, 'data': links}) # تغيير من links إلى data لتطابق الواجهة

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

# ------------------- تشغيل الخادم -------------------
if __name__ == '__main__':
    fetch_initial_data() # جلب البيانات عند بدء التشغيل
    app.run(host='0.0.0.0', port=5001, debug=False)
