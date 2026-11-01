"""
Bulk import Sessionize event URLs from SQLite into Supabase conference_url_candidates.
"""
import os
import sqlite3
import time
import httpx
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SQLITE_PATH = ROOT / "more info" / "sourceplay" / "sessionize_event_urls.sqlite"

if not SQLITE_PATH.exists():
    # Try alternative path
    SQLITE_PATH = Path(r"C:\codes\crwdstag\more info\sourceplay\sessionize_event_urls.sqlite")

print(f"Reading from: {SQLITE_PATH}")

con = sqlite3.connect(SQLITE_PATH)
cur = con.cursor()
cur.execute("SELECT url, source_sitemap, sitemap_lastmod, first_seen_at FROM sessionize_event_urls WHERE is_listed=1")
rows = cur.fetchall()
print(f"Loaded {len(rows)} URLs from SQLite")

headers = {
    "apikey": SERVICE_ROLE_KEY,
    "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=ignore-duplicates,return=minimal"
}

endpoint = f"{SUPABASE_URL}/rest/v1/conference_url_candidates"

BATCH_SIZE = 500
total = len(rows)
uploaded = 0

client = httpx.Client(timeout=30)

start_time = time.time()
for i in range(0, total, BATCH_SIZE):
    batch = rows[i:i + BATCH_SIZE]
    payload = []
    for url, sitemap, lastmod, first_seen in batch:
        if not url.startswith("https://"):
            continue
        payload.append({
            "url": url,
            "discovered_from": "sessionize-sitemap",
            "found_at": first_seen or "2026-10-15T19:10:10Z",
            "review_state": "needs_review",
            "notes": f"Lastmod: {lastmod}" if lastmod else None
        })
    
    res = client.post(endpoint, headers=headers, json=payload)
    if res.status_code in (200, 201, 204):
        uploaded += len(payload)
        print(f"Uploaded {uploaded}/{total} ({(uploaded/total)*100:.1f}%)")
    else:
        print(f"Error on batch {i}-{i+len(payload)}: {res.status_code} {res.text[:200]}")

print(f"Finished in {time.time() - start_time:.1f}s. Total uploaded: {uploaded}")
