#!/usr/bin/env node
/**
 * scripts/fetch_all.js
 * - Fetches /api/all-movies and /api/all-series and saves JSON files.
 * - Cron-ready. Requires Node 18+ (global fetch). If using older Node, install node-fetch.
 *
 * ENV:
 *   API_BASE (default: http://localhost:5001)
 *   OUT_DIR  (default: ./data)
 *
 * Example:
 *   API_BASE=http://localhost:5001 OUT_DIR=./data node scripts/fetch_all.js
 */

import fs from 'fs/promises';
import path from 'path';

const API_BASE = process.env.API_BASE || 'http://localhost:5001';
const OUT_DIR = process.env.OUT_DIR || './data';
const RETRIES = 3;
const RETRY_DELAY_MS = 1500;

async function sleep(ms){ return new Promise(r => setTimeout(r, ms)); }

async function safeFetch(url, retries = RETRIES){
  for(let i=0;i<=retries;i++){
    try{
      const res = await fetch(url, { timeout: 30000 });
      if(!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
      const json = await res.json();
      return json;
    }catch(err){
      if(i===retries) throw err;
      await sleep(RETRY_DELAY_MS*(i+1));
    }
  }
}

function normalizeItem(item){
  return {
    title: item.title ?? item.name ?? null,
    link: item.link ?? item.url ?? null,
    image: item.image ?? item.thumbnail ?? null,
    year: item.year ?? item.release_year ?? null,
    rating: item.rating ?? null,
    genres: item.genres ?? item.category ?? [],
    quality: item.quality ?? null,
    type: item.type ?? null
  };
}

async function saveJSON(filename, obj){
  await fs.mkdir(OUT_DIR, { recursive: true });
  const filePath = path.join(OUT_DIR, filename);
  await fs.writeFile(filePath, JSON.stringify(obj, null, 2), 'utf8');
  console.log(`Saved ${filePath}`);
}

async function run(){
  console.log(`API base: ${API_BASE}`);
  try{
    const moviesUrl = `${API_BASE.replace(/\/$/, '')}/api/all-movies`;
    const seriesUrl = `${API_BASE.replace(/\/$/, '')}/api/all-series`;

    console.log('Fetching movies...');
    const moviesRes = await safeFetch(moviesUrl);
    console.log('Fetching series...');
    const seriesRes = await safeFetch(seriesUrl);

    const movies = (moviesRes.data ?? moviesRes.items ?? moviesRes).map(normalizeItem);
    const series = (seriesRes.data ?? seriesRes.items ?? seriesRes).map(normalizeItem);

    const timestamp = new Date().toISOString().replace(/:/g,'-');
    await saveJSON(`all-movies.${timestamp}.json`, { fetchedAt: timestamp, count: movies.length, data: movies });
    await saveJSON(`all-series.${timestamp}.json`, { fetchedAt: timestamp, count: series.length, data: series });

    await saveJSON('all-movies.latest.json', { fetchedAt: timestamp, count: movies.length, data: movies });
    await saveJSON('all-series.latest.json', { fetchedAt: timestamp, count: series.length, data: series });

    console.log('Done.');
  }catch(err){
    console.error('Error:', err.message || err);
    process.exitCode = 1;
  }
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith('fetch_all.js')){
  run();
}
