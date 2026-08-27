"""ETL script to read latest JSON files and upsert into Postgres
Requirements: psycopg2-binary
ENV:
  POSTGRES_URL=postgresql://user:pass@host:5432/db
  DATA_DIR=./data

Usage:
  POSTGRES_URL="postgresql://..." DATA_DIR=./data python scripts/etl_postgres.py
"""

import os
import json
from pathlib import Path
from datetime import datetime
import psycopg2
from psycopg2.extras import execute_values

POSTGRES_URL = os.environ.get('POSTGRES_URL')
DATA_DIR = Path(os.environ.get('DATA_DIR', './data'))

if not POSTGRES_URL:
    print('ERROR: POSTGRES_URL is not set')
    exit(1)

# find latest JSON files
movies_file = DATA_DIR / 'all-movies.latest.json'
series_file = DATA_DIR / 'all-series.latest.json'

if not movies_file.exists() and not series_file.exists():
    print('No latest JSON files found in', DATA_DIR)
    exit(1)

conn = psycopg2.connect(POSTGRES_URL)
cur = conn.cursor()

# ensure table exists (simple check)
create_sql = '''
CREATE TABLE IF NOT EXISTS media_items (
  id SERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  link TEXT UNIQUE,
  image TEXT,
  year TEXT,
  rating TEXT,
  genres TEXT[],
  quality TEXT,
  type TEXT,
  source TEXT,
  fetched_at TIMESTAMP WITH TIME ZONE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);
'''
cur.execute(create_sql)
conn.commit()

def load_json(path):
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding='utf-8'))['data']

movies = load_json(movies_file)
series = load_json(series_file)
items = movies + series

if not items:
    print('No items to upsert')
    conn.close()
    exit(0)

vals = []
for it in items:
    vals.append((
        it.get('title'),
        it.get('link'),
        it.get('image'),
        it.get('year'),
        it.get('rating'),
        it.get('genres') or [],
        it.get('quality'),
        it.get('type'),
        'ak.sv',
        datetime.utcnow()
    ))

sql = '''
INSERT INTO media_items (title, link, image, year, rating, genres, quality, type, source, fetched_at)
VALUES %s
ON CONFLICT (link) DO UPDATE SET
  title = EXCLUDED.title,
  image = EXCLUDED.image,
  year = EXCLUDED.year,
  rating = EXCLUDED.rating,
  genres = EXCLUDED.genres,
  quality = EXCLUDED.quality,
  type = EXCLUDED.type,
  source = EXCLUDED.source,
  fetched_at = EXCLUDED.fetched_at,
  updated_at = now();
'''

execute_values(cur, sql, vals, page_size=100)
conn.commit()
print('Upserted', len(vals), 'items into media_items')
cur.close()
conn.close()
