#!/usr/bin/env python3
"""
Shopee Điện Thoại & Phụ Kiện crawler
Strategy: Playwright Firefox + pycookiecheat (decrypted Chrome cookies) + DOM scraping
"""

import asyncio, json, logging, math, os, random, re, signal, sqlite3, sys, time
from playwright.async_api import async_playwright
import pycookiecheat

# ── Config ───────────────────────────────────────────────────────
CATEGORY_URL = "https://shopee.vn/%C4%90i%E1%BB%87n-Tho%E1%BA%A1i-Ph%E1%BB%A5-Ki%E1%BB%87n-cat.11036030"
CATEGORY_ID  = 11036030
MAX_PAGES    = int(os.getenv("MAX_PAGES", "100"))
DB_PATH      = os.getenv("DB_PATH", "shopee.db")
DISPLAY      = os.getenv("DISPLAY", ":99")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# ── DB ────────────────────────────────────────────────────────────
def db_connect(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS products (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id       INTEGER NOT NULL UNIQUE,
        shop_id       INTEGER,
        name          TEXT,
        price         INTEGER,
        original_price INTEGER,
        discount_pct  INTEGER,
        sold_text     TEXT,
        location      TEXT,
        image_url     TEXT,
        item_url      TEXT,
        page_number   INTEGER,
        crawled_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS crawl_progress (
        id INTEGER PRIMARY KEY CHECK(id=1),
        last_page INTEGER DEFAULT 0,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS crawl_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pages INTEGER DEFAULT 0,
        total INTEGER DEFAULT 0,
        status TEXT DEFAULT 'running',
        error TEXT,
        started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        finished_at DATETIME
    );
    """)
    conn.commit()
    return conn

def db_get_last_page(conn):
    row = conn.execute("SELECT last_page FROM crawl_progress WHERE id=1").fetchone()
    return row[0] if row else 0

def db_update_progress(conn, page):
    conn.execute("""
        INSERT INTO crawl_progress(id,last_page) VALUES(1,?)
        ON CONFLICT(id) DO UPDATE SET last_page=excluded.last_page, updated_at=CURRENT_TIMESTAMP
    """, (page,))
    conn.commit()

def db_upsert(conn, p):
    exists = conn.execute("SELECT id FROM products WHERE item_id=?", (p["item_id"],)).fetchone()
    if not exists:
        conn.execute("""
            INSERT INTO products
                (item_id,shop_id,name,price,original_price,discount_pct,
                 sold_text,location,image_url,item_url,page_number)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (p["item_id"],p["shop_id"],p["name"],p["price"],p["original_price"],
              p["discount_pct"],p["sold_text"],p["location"],p["image_url"],p["item_url"],p["page"]))
        return True
    conn.execute("""
        UPDATE products SET name=?,price=?,original_price=?,discount_pct=?,
            sold_text=?,updated_at=CURRENT_TIMESTAMP WHERE item_id=?
    """, (p["name"],p["price"],p["original_price"],p["discount_pct"],p["sold_text"],p["item_id"]))
    return False

def db_count(conn):
    return conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]

# ── Parser ────────────────────────────────────────────────────────
ITEM_RE = re.compile(r'[-/]i\.(\d+)\.(\d+)')

def parse_price(text):
    nums = re.sub(r'[^\d]', '', text)
    return int(nums) if nums else 0

def parse_items(raw_items, page):
    products = []
    for item in raw_items:
        href = item.get("href","")
        text = item.get("text","")
        img  = item.get("img","")

        m = ITEM_RE.search(href)
        if not m:
            continue
        shop_id = int(m.group(1))
        item_id = int(m.group(2))

        lines = [l.strip() for l in text.split("\n") if l.strip()]

        # Name: first long line not containing price/sold/discount
        name = ""
        for l in lines:
            if len(l) > 10 and "₫" not in l and "Đã bán" not in l and not re.match(r'^-?\d+%', l):
                name = l
                break

        # Discount
        m_disc = re.search(r'-(\d+)%', text)
        discount_pct = int(m_disc.group(1)) if m_disc else 0

        # Prices: numbers before ₫
        prices = []
        for m_p in re.finditer(r'([\d.,]+)\s*₫', text):
            val = parse_price(m_p.group(1))
            if val > 100:
                prices.append(val)
        price = prices[0] if prices else 0
        original_price = prices[1] if len(prices) > 1 else 0

        # Sold
        m_sold = re.search(r'Đã bán\s*(.+?)(?:\n|$)', text)
        sold_text = m_sold.group(1).strip() if m_sold else ""

        # Location: short line at end
        location = ""
        for l in reversed(lines):
            if 2 < len(l) < 30 and "₫" not in l and "Đã bán" not in l and "%" not in l:
                location = l
                break

        products.append({
            "item_id": item_id, "shop_id": shop_id,
            "name": name, "price": price,
            "original_price": original_price, "discount_pct": discount_pct,
            "sold_text": sold_text, "location": location,
            "image_url": img,
            "item_url": f"https://shopee.vn/i.{shop_id}.{item_id}",
            "page": page,
        })
    return products

JS_ITEM_MAP = """els => els.map(el => ({
    href: (el.querySelector('a') || {}).href || '',
    text: el.innerText || '',
    img:  (el.querySelector('img') || {}).src || ''
}))"""

# ── Human-like helpers ────────────────────────────────────────────
async def human_mouse_wiggle(page, n=6):
    """Random mouse movements to simulate human presence."""
    vw = await page.evaluate("window.innerWidth")
    vh = await page.evaluate("window.innerHeight")
    for _ in range(n):
        x = random.randint(100, int(vw) - 100)
        y = random.randint(100, int(vh) - 100)
        await page.mouse.move(x, y, steps=random.randint(5, 15))
        await asyncio.sleep(random.uniform(0.05, 0.2))

async def human_scroll(page):
    """Smooth, human-like scroll down the page in small steps."""
    total_height = await page.evaluate("document.body.scrollHeight")
    viewport_h   = await page.evaluate("window.innerHeight")
    current_y    = 0

    while current_y < total_height:
        # Variable step size with occasional pauses
        step = random.randint(150, 400)
        current_y = min(current_y + step, total_height)
        await page.evaluate(f"window.scrollTo({{top: {current_y}, behavior: 'smooth'}})")
        await asyncio.sleep(random.uniform(0.15, 0.45))

        # Occasional random pause (reading behavior)
        if random.random() < 0.15:
            await asyncio.sleep(random.uniform(0.5, 1.5))

        # Small upward scroll occasionally
        if random.random() < 0.08:
            back = random.randint(50, 150)
            current_y = max(0, current_y - back)
            await page.evaluate(f"window.scrollTo({{top: {current_y}, behavior: 'smooth'}})")
            await asyncio.sleep(random.uniform(0.1, 0.3))

        await human_mouse_wiggle(page, n=2)

    # Scroll back to top slowly
    await asyncio.sleep(random.uniform(0.3, 0.8))
    await page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
    await asyncio.sleep(random.uniform(0.5, 1.0))

async def human_delay(min_s=1.0, max_s=3.0):
    await asyncio.sleep(random.uniform(min_s, max_s))

# ── Crawl one page ────────────────────────────────────────────────
async def crawl_page(page, pagenum):
    url = f"{CATEGORY_URL}?page={pagenum-1}&sortBy=pop"
    await page.goto(url, wait_until="domcontentloaded", timeout=35000)

    # Human-like initial pause + small mouse movement
    await human_delay(1.5, 3.0)
    await human_mouse_wiggle(page, n=4)

    # Human scroll to load all lazy items
    await human_scroll(page)
    await human_delay(0.5, 1.5)

    # Wait for items to appear
    for attempt in range(8):
        count = await page.eval_on_selector_all('[data-sqe=item]', 'els => els.length')
        log.info(f"  Check {attempt+1}: {count} items")
        if count >= 5:
            break
        # Check if blocked
        current = page.url
        if "verify" in current or "captcha" in current or "login" in current:
            raise RuntimeError(f"Blocked: {current[:80]}")
        await human_delay(2.0, 4.0)
        await human_mouse_wiggle(page, n=3)
    else:
        current = page.url
        raise RuntimeError(f"No items after 8 checks. URL: {current[:80]}")

    # Scroll again to load remaining lazy web components (anchors)
    for attempt in range(6):
        a_count = await page.eval_on_selector_all('[data-sqe=item] a', 'els => els.length')
        log.info(f"  Anchors: {a_count}/{count}")
        if a_count >= count * 0.85:
            break
        await human_scroll(page)
        await human_delay(1.0, 2.0)

    # Final mouse wiggle before extracting
    await human_mouse_wiggle(page, n=5)
    await human_delay(0.5, 1.0)

    raw_items = await page.eval_on_selector_all('[data-sqe=item]', JS_ITEM_MAP)
    return parse_items(raw_items, pagenum)

# ── Main ──────────────────────────────────────────────────────────
async def main():
    os.environ.setdefault("DISPLAY", DISPLAY)

    conn = db_connect(DB_PATH)
    start_page = db_get_last_page(conn) + 1
    log.info(f"Start page: {start_page} | DB: {DB_PATH} | max: {MAX_PAGES}")

    session_id = conn.execute("INSERT INTO crawl_sessions DEFAULT VALUES").lastrowid
    conn.commit()

    # Get Shopee cookies from Chrome
    cookies = pycookiecheat.chrome_cookies('https://shopee.vn', browser='Chrome')
    log.info(f"Loaded {len(cookies)} Shopee cookies from Chrome")

    interrupted = False
    def _sig(s, f):
        nonlocal interrupted
        log.info("Interrupted!")
        interrupted = True
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    started = time.time()
    total_new = total_updated = pages_ok = 0

    async def new_browser_context(p):
        """Fresh browser + context with latest cookies."""
        fresh_cookies = pycookiecheat.chrome_cookies('https://shopee.vn', browser='Chrome')
        b = await p.firefox.launch(headless=False)
        ctx = await b.new_context(locale='vi-VN', viewport={'width': 1280, 'height': 900})
        await ctx.add_cookies([
            {'name': k, 'value': v, 'domain': '.shopee.vn', 'path': '/'}
            for k, v in fresh_cookies.items()
        ])
        pg = await ctx.new_page()
        # Warm up: visit homepage first
        await pg.goto("https://shopee.vn", wait_until="domcontentloaded", timeout=20000)
        await human_delay(2.0, 4.0)
        await human_mouse_wiggle(pg, n=5)
        log.info(f"  New browser context ready ({len(fresh_cookies)} cookies)")
        return b, ctx, pg

    PAGES_PER_SESSION = 2  # restart browser every N pages to reset fingerprint

    async with async_playwright() as p:
        browser, context, page = await new_browser_context(p)

        for pagenum in range(start_page, MAX_PAGES + 1):
            if interrupted:
                break

            # Restart browser context periodically
            if pages_ok > 0 and pages_ok % PAGES_PER_SESSION == 0:
                log.info(f"  Restarting browser context (session refresh)...")
                await browser.close()
                await asyncio.sleep(random.uniform(5, 10))
                browser, context, page = await new_browser_context(p)

            log.info(f"[Page {pagenum}/{MAX_PAGES}]")
            try:
                products = await crawl_page(page, pagenum)
            except RuntimeError as e:
                if "Blocked" in str(e) or "captcha" in str(e).lower() or "No items" in str(e):
                    log.warning(f"  Blocked — restarting browser with fresh cookies...")
                    await browser.close()
                    await asyncio.sleep(random.uniform(10, 20))
                    browser, context, page = await new_browser_context(p)
                    try:
                        products = await crawl_page(page, pagenum)
                    except Exception as e2:
                        log.error(f"  Still blocked after restart: {e2}")
                        break
                else:
                    log.error(f"  Error: {e}")
                    break
            except Exception as e:
                log.error(f"  Error: {e} — retrying in 8s...")
                await asyncio.sleep(8)
                try:
                    products = await crawl_page(page, pagenum)
                except Exception as e2:
                    log.error(f"  Retry failed: {e2}")
                    conn.execute(
                        "UPDATE crawl_sessions SET status='error',error=?,finished_at=CURRENT_TIMESTAMP WHERE id=?",
                        (str(e2), session_id)
                    )
                    conn.commit()
                    await browser.close()
                    sys.exit(1)

            if not products:
                log.info("  0 products — stopping.")
                break

            pn = pu = 0
            for prod in products:
                if db_upsert(conn, prod): pn += 1
                else: pu += 1
            conn.commit()

            total_new += pn
            total_updated += pu
            pages_ok += 1
            db_update_progress(conn, pagenum)
            total = db_count(conn)
            log.info(f"  {total:,} total | +{pn} new | {pu} updated")

            if len(products) < 10:
                log.info("  Last page (few results).")
                break

            # Human-like inter-page break
            delay = random.uniform(20.0, 40.0)
            log.info(f"  Sleeping {delay:.1f}s...")
            # Browse homepage briefly to reset session signals
            await page.goto("https://shopee.vn", wait_until="domcontentloaded", timeout=20000)
            await human_delay(2.0, 4.0)
            await human_mouse_wiggle(page, n=random.randint(5, 10))
            await asyncio.sleep(delay - 6)

        await browser.close()

    status = "interrupted" if interrupted else "done"
    total = db_count(conn)
    conn.execute(
        "UPDATE crawl_sessions SET pages=?,total=?,status=?,finished_at=CURRENT_TIMESTAMP WHERE id=?",
        (pages_ok, total, status, session_id)
    )
    conn.commit()
    conn.close()

    print(f"\n{'='*40}")
    print(f"Status:   {status}")
    print(f"Pages:    {pages_ok}")
    print(f"Products: {total:,}")
    print(f"New:      {total_new:,}")
    print(f"Updated:  {total_updated:,}")
    print(f"Duration: {time.time()-started:.0f}s")
    print('='*40)

if __name__ == "__main__":
    asyncio.run(main())
