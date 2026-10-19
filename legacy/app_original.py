"""
PriceRadar Backend — Flask + Playwright + Email Alerts
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
from playwright.sync_api import sync_playwright
import time
import re
import logging
import urllib.request
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# EMAIL CONFIG — update these with your Gmail
# ─────────────────────────────────────────────
SENDER_EMAIL = os.environ.get("EMAIL_USER")
SENDER_PASSWORD = os.environ.get("EMAIL_PASS")

# In-memory alert store: { alert_id: { url, target_price, email, product_name, triggered } }
alerts_store = {}
alert_counter = 0

# Price stability filter — stores last confirmed good price per URL
last_known_price = {}  # { url: int }


# ─────────────────────────────────────────────
# EMAIL SENDER
# ─────────────────────────────────────────────

def send_price_alert_email(to_email, product_name, current_price, target_price, product_url):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"PriceRadar Alert: {product_name[:50]} is now Rs.{current_price:,}!"
        msg["From"] = SENDER_EMAIL
        msg["To"] = to_email

        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background: #0a0a0f; color: #e0e0ff; padding: 30px;">
            <div style="max-width: 600px; margin: 0 auto; background: #111118; border-radius: 12px; padding: 30px; border: 1px solid #00e5ff33;">
                <h1 style="color: #00e5ff; font-size: 24px;">📡 PriceRadar Alert!</h1>
                <p style="color: #00ff88; font-size: 18px; font-weight: bold;">Your target price has been hit!</p>
                <hr style="border-color: #1e1e2e;">
                <p style="font-size: 16px;"><strong>Product:</strong><br>{product_name}</p>
                <table style="width: 100%; margin: 20px 0;">
                    <tr>
                        <td style="padding: 12px; background: #00ff8811; border-radius: 8px; text-align: center;">
                            <div style="color: #5a5a7a; font-size: 12px;">CURRENT PRICE</div>
                            <div style="color: #00ff88; font-size: 28px; font-weight: bold;">₹{current_price:,}</div>
                        </td>
                        <td style="width: 20px;"></td>
                        <td style="padding: 12px; background: #1e1e2e; border-radius: 8px; text-align: center;">
                            <div style="color: #5a5a7a; font-size: 12px;">YOUR TARGET</div>
                            <div style="color: #ffd700; font-size: 28px; font-weight: bold;">₹{target_price:,}</div>
                        </td>
                    </tr>
                </table>
                <p style="color: #00ff88;">✅ Current price is at or below your target price!</p>
                <a href="{product_url}" style="display: inline-block; background: linear-gradient(135deg, #00e5ff, #0099ff); color: #000; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: bold; margin-top: 10px;">
                    🛒 Buy Now on Amazon
                </a>
                <p style="color: #5a5a7a; font-size: 12px; margin-top: 20px;">This alert was sent by PriceRadar. Prices may change quickly.</p>
            </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, to_email, msg.as_string())

        logger.info(f"[Email] Alert sent to {to_email} for {product_name}")
        return True
    except Exception as e:
        logger.error(f"[Email] Failed to send: {e}")
        return False


# ─────────────────────────────────────────────
# URL EXPANDER
# ─────────────────────────────────────────────

def expand_url(url: str) -> str:
    from urllib.parse import urlparse, urlunparse
    import urllib.parse as urlparse_mod

    # ── Step 0: Extract actual URL if user pasted a full WhatsApp/share message ──
    # e.g. "Take a look at this product on Flipkart https://dl.flipkart.com/s/eA1OC6NNNN"
    url = url.strip()
    if not url.startswith("http"):
        # Find first http/https URL in the text
        match = re.search(r'https?://[^\s]+', url)
        if match:
            url = match.group(0).rstrip('.,)')
            logger.info(f"[URL] Extracted URL from text: {url}")
        else:
            logger.warning(f"[URL] No URL found in input: {url}")
            return url

    # ── Step 1: Handle dl.flipkart.com/s/ short links ──
    if "dl.flipkart.com" in url and "/s/" in url:
        logger.info(f"[URL] Flipkart short link, expanding: {url}")
        clean_url = url.split("?")[0]

        # Method A: urllib redirect (fast, but Flipkart blocks with 403 sometimes)
        try:
            req = urllib.request.Request(clean_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "en-IN,en;q=0.9",
            })
            final_url = urllib.request.urlopen(req, timeout=10).url
            if "flipkart.com" in final_url and "dl.flipkart.com" not in final_url:
                logger.info(f"[URL] Short link expanded via urllib: {final_url}")
                return final_url
        except Exception as e:
            logger.warning(f"[URL] urllib failed ({e}), trying Playwright...")

        # Method B: Playwright — follows JS redirects, bypasses 403
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    locale="en-IN"
                )
                page = context.new_page()
                page.goto(clean_url, wait_until="commit", timeout=20000)
                time.sleep(2)
                final_url = page.url
                browser.close()
                if "flipkart.com" in final_url and "dl.flipkart.com" not in final_url:
                    logger.info(f"[URL] Short link expanded via Playwright: {final_url}")
                    return final_url
                else:
                    logger.warning(f"[URL] Playwright landed on: {final_url}")
        except Exception as e:
            logger.warning(f"[URL] Playwright redirect failed: {e}")

        # Method C: Extract pid from URL and build direct product URL
        # dl.flipkart.com/s/XXXX often has pid in the query string
        pid_match = re.search(r'pid=([A-Z0-9]+)', url)
        if pid_match:
            pid = pid_match.group(1)
            direct_url = f"https://www.flipkart.com/product/p/item?pid={pid}"
            logger.info(f"[URL] Built direct URL from pid: {direct_url}")
            return direct_url

        logger.warning(f"[URL] All expansion methods failed, scraping short link directly")
        return url

    # ── Step 2: Handle dl.flipkart.com/dl/ deep links ──
    if "dl.flipkart.com" in url:
        logger.info(f"[URL] Flipkart deep link detected: {url}")
        try:
            parsed = urlparse(url)
            path = parsed.path
            # Strip the leading /dl from path: /dl/product/p/item → /product/p/item
            if path.startswith("/dl/"):
                path = path[3:]
            elif path.startswith("/dl"):
                path = path[3:]
            params = urlparse_mod.parse_qs(parsed.query)
            pid = params.get("pid", [None])[0]
            new_query = f"pid={pid}" if pid else parsed.query
            new_url = f"https://www.flipkart.com{path}?{new_query}"
            logger.info(f"[URL] Converted Flipkart deep link to: {new_url}")
            return new_url
        except Exception as e:
            logger.warning(f"[URL] Flipkart deep link fix failed: {e}")

    # ── Step 3: Handle Amazon / fkrt short links ──
    short_domains = ["amzn.in", "amzn.to", "a.co", "fkrt.it"]
    if not any(d in url for d in short_domains):
        return url

    logger.info(f"[URL] Expanding short URL: {url}")

    # Method 1: urllib redirect
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-IN,en;q=0.9",
        })
        response = urllib.request.urlopen(req, timeout=10)
        final_url = response.url
        if "amazon" in final_url or "flipkart" in final_url:
            logger.info(f"[URL] Method1 expanded: {final_url}")
            return final_url
    except Exception as e:
        logger.warning(f"[URL] Method1 failed: {e}")

    # Method 2: Playwright networkidle
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(user_agent="Mozilla/5.0", locale="en-IN")
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(3)
            final_url = page.url
            browser.close()
            if "amazon" in final_url or "flipkart" in final_url:
                logger.info(f"[URL] Method2 expanded: {final_url}")
                return final_url
    except Exception as e:
        logger.warning(f"[URL] Method2 failed: {e}")

    # Method 3: Construct amazon.in/dp directly
    match = re.search(r"amzn\.in/d/([A-Za-z0-9]+)", url)
    if match:
        constructed = f"https://www.amazon.in/dp/{match.group(1)}"
        logger.info(f"[URL] Method3 constructed: {constructed}")
        return constructed

    return url


# ─────────────────────────────────────────────
# BROWSER SETUP
# ─────────────────────────────────────────────

def get_browser_page(playwright):
    browser = playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox",
              "--disable-blink-features=AutomationControlled",
              "--window-size=1366,768"]
    )
    context = browser.new_context(
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        viewport={"width": 1366, "height": 768},
        locale="en-IN", timezone_id="Asia/Kolkata",
        extra_http_headers={"Accept-Language": "en-IN,en;q=0.9"}
    )
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
    """)
    return browser, context, context.new_page()


# ─────────────────────────────────────────────
# AMAZON SCRAPER
# ─────────────────────────────────────────────

def scrape_amazon(url: str) -> dict:
    with sync_playwright() as p:
        browser, context, page = get_browser_page(p)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            title = page.title()

            if "robot" in title.lower() or "captcha" in title.lower():
                return {"success": False, "error": "Amazon CAPTCHA detected. Try again.", "platform": "Amazon"}

            name = None
            for sel in ["#productTitle", "span#title"]:
                try:
                    el = page.query_selector(sel)
                    if el:
                        name = el.inner_text().strip()
                        if name: break
                except: continue

            price = None
            for sel in ["span.a-price-whole",
                        ".reinventPricePriceToPayMargin span.a-price-whole",
                        "#corePrice_feature_div span.a-price-whole",
                        "#priceblock_ourprice", "#priceblock_dealprice"]:
                try:
                    el = page.query_selector(sel)
                    if el:
                        raw = re.sub(r"[^\d]", "", el.inner_text().split(".")[0])
                        if raw and int(raw) > 0:
                            price = int(raw); break
                except: continue

            mrp = None
            for sel in ["span.a-price.a-text-price span.a-offscreen", "#listPrice"]:
                try:
                    el = page.query_selector(sel)
                    if el:
                        raw = re.sub(r"[^\d]", "", el.inner_text())
                        if raw: mrp = int(raw); break
                except: continue

            rating = None
            try:
                el = page.query_selector("span.a-icon-alt")
                if el:
                    m = re.search(r"(\d+\.\d+)", el.inner_text())
                    if m: rating = m.group(1)
            except: pass

            reviews = None
            try:
                el = page.query_selector("#acrCustomerReviewText")
                if el:
                    raw = re.sub(r"[^\d]", "", el.inner_text())
                    if raw: reviews = int(raw)
            except: pass

            return {
                "platform": "Amazon", "name": name or "Amazon Product",
                "price": price, "mrp": mrp, "rating": rating,
                "reviews": reviews, "url": url, "currency": "INR",
                "success": price is not None,
                "error": None if price else f"Price not found. Title: '{title}'",
            }
        except Exception as e:
            return {"success": False, "error": str(e), "platform": "Amazon"}
        finally:
            browser.close()


# ─────────────────────────────────────────────
# FLIPKART SCRAPER
# ─────────────────────────────────────────────

def scrape_flipkart(url: str) -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-blink-features=AutomationControlled",
                  "--window-size=1366,768", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="en-IN", timezone_id="Asia/Kolkata",
            extra_http_headers={
                "Accept-Language": "en-IN,en;q=0.9",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
                "sec-ch-ua-platform": '"Windows"',
            }
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver',  { get: () => undefined });
            Object.defineProperty(navigator, 'plugins',    { get: () => [1,2,3,4,5] });
            Object.defineProperty(navigator, 'languages',  { get: () => ['en-IN','en'] });
            window.chrome = { runtime: {}, loadTimes: () => {}, csi: () => {} };
        """)
        page = context.new_page()
        page.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,mp4,mp3}",
                   lambda route: route.abort())

        try:
            # ── STEP 1: Navigate and let Playwright follow all redirects ──
            # This handles dl.flipkart.com/s/ links naturally — browser follows
            # the JS redirect to the real product page automatically
            logger.info(f"[Flipkart] Loading: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                page.goto(url, wait_until="commit", timeout=60000)

            # For short links — wait for JS to execute and price data to load
            if "dl.flipkart.com" in url:
                logger.info("[Flipkart] Short link — waiting for JS execution...")
                time.sleep(8)
            else:
                time.sleep(5)

            # After redirect, get the REAL URL we landed on
            final_url = page.url
            logger.info(f"[Flipkart] Landed on: {final_url}")

            # If still on dl.flipkart.com, wait more for JS redirect
            if "dl.flipkart.com" in final_url:
                time.sleep(4)
                final_url = page.url
                logger.info(f"[Flipkart] After extra wait: {final_url}")

            title = page.title()
            logger.info(f"[Flipkart] Title: {title}")

            # If homepage, the URL didn't redirect properly
            if "online shopping" in title.lower():
                return {"success": False,
                        "error": "Flipkart redirected to homepage. Please use the full product URL from Chrome browser, not the app share link.",
                        "platform": "Flipkart"}

            # Close login popup if present
            for sel in ["button._2KpZ6l._2doB4z", "button.col.oEUDFd", "span.kLCMXS"]:
                try:
                    el = page.query_selector(sel)
                    if el: el.click(); time.sleep(1); break
                except: continue

            time.sleep(2)

            # ── STEP 2: Get product name ──
            name = None
            for sel in ["span.VU-ZEz", "span.B_NuCI", "h1.yhB1nd", "h1._9E25nV", "h1"]:
                try:
                    el = page.query_selector(sel)
                    if el:
                        t = el.inner_text().strip()
                        if t and len(t) > 5: name = t; break
                except: continue

            # ── STEP 3: Extract price — JS window state first, then HTML ──
            # For short links (dl.flipkart.com/s/) Flipkart loads prices via JavaScript,
            # not embedded in initial HTML. So we MUST read from the JS runtime state.
            price = None

            # Method A: Read from JavaScript window state (works after JS executes)
            try:
                result = page.evaluate(r"""() => {
                    try {
                        const allPrices = [];
                        function scan(text) {
                            const pats = [
                                /"finalPrice"\s*:\s*(\d+)/g,
                                /"sellingPrice"\s*:\s*(\d+)/g,
                                /"discountedPrice"\s*:\s*(\d+)/g
                            ];
                            for (const p of pats) {
                                let m;
                                while ((m = p.exec(text)) !== null) {
                                    const n = parseInt(m[1]);
                                    if (n > 500 && n < 500000) allPrices.push(n);
                                }
                            }
                        }
                        // Check window state objects
                        if (window.__INITIAL_STATE__)
                            scan(JSON.stringify(window.__INITIAL_STATE__));
                        if (window.__STATE__)
                            scan(JSON.stringify(window.__STATE__));
                        if (window.pageDataManager)
                            scan(JSON.stringify(window.pageDataManager));
                        // Scan all script tags
                        document.querySelectorAll('script').forEach(s => {
                            if (s.textContent.length > 100 &&
                                (s.textContent.includes('finalPrice') ||
                                 s.textContent.includes('sellingPrice')))
                                scan(s.textContent);
                        });
                        if (!allPrices.length) return null;
                        return Math.min(...allPrices);
                    } catch(e) { return null; }
                }""")
                if result and 500 < result < 500000:
                    price = result
                    logger.info(f"[Flipkart] JS state price: Rs.{price}")
            except Exception as e:
                logger.warning(f"[Flipkart] JS state scan failed: {e}")

            # Method B: Raw HTML regex (works when JSON is in server-rendered HTML)
            if not price:
                html = page.content()
                for pattern in [
                    r'"finalPrice"\s*:\s*(\d+)',
                    r'"sellingPrice"\s*:\s*(\d+)',
                    r'"discountedPrice"\s*:\s*(\d+)',
                ]:
                    vals = [int(m) for m in re.findall(pattern, html)
                            if 500 < int(m) < 500000]
                    if vals:
                        price = min(vals)
                        logger.info(f"[Flipkart] HTML regex ({pattern}): "
                                    f"candidates={sorted(set(vals))} → Rs.{price}")
                        break

            # Method C: DOM scan of rupee elements — top 250 elements only
            if not price:
                logger.info("[Flipkart] DOM rupee scan...")
                try:
                    dom_prices = page.evaluate("""() => {
                        const skip = ['off','emi','month',' x ','bank','exchange',
                                      'protect','combo','cashback','no cost'];
                        const results = [];
                        const els = [...document.querySelectorAll('div,span')].slice(0,250);
                        for (const el of els) {
                            if (el.children.length) continue;
                            const t = el.textContent.trim();
                            if (!t.startsWith('₹') || t.length > 12) continue;
                            const pt = (el.parentElement?.textContent||'').toLowerCase();
                            if (skip.some(w => pt.includes(w))) continue;
                            const n = parseInt(t.replace(/[^0-9]/g,''));
                            if (n > 500 && n < 500000) results.push(n);
                        }
                        return results;
                    }""")
                    if dom_prices:
                        from collections import Counter
                        freq = Counter(dom_prices)
                        logger.info(f"[Flipkart] DOM prices: {freq.most_common(5)}")
                        top_price, top_count = freq.most_common(1)[0]
                        price = top_price if top_count >= 2 else min(dom_prices)
                        logger.info(f"[Flipkart] DOM picked: Rs.{price}")
                except Exception as e:
                    logger.warning(f"[Flipkart] DOM scan failed: {e}")


            # ── STEP 5: MRP ──
            mrp = None
            all_mrp = [int(m) for m in re.findall(r'"mrp"\s*:\s*(\d+)', html)
                       if 500 < int(m) < 500000]
            if all_mrp:
                mrp = min(all_mrp)  # smallest MRP = base product MRP
                logger.info(f"[Flipkart] MRP from HTML: Rs.{mrp}")
            else:
                for sel in ["div._3I9_wc", "div._27UcVY", "div.yRaY8j"]:
                    try:
                        el = page.query_selector(sel)
                        if el:
                            raw = re.sub(r"[^\d]", "", el.inner_text())
                            if raw and int(raw) > 0: mrp = int(raw); break
                    except: continue

            # ── Sanity check ──
            if price and mrp and price > mrp:
                logger.warning(f"[Flipkart] price Rs.{price} > MRP Rs.{mrp}, resetting")
                price = None

            # ── Rating ──
            rating = None
            for sel in ["div._3LWZlK", "div.XQDdHH", "span._2d4LTz"]:
                try:
                    el = page.query_selector(sel)
                    if el:
                        t = el.inner_text().strip()
                        if t: rating = t; break
                except: continue

            # ── Reviews ──
            reviews = None
            for sel in ["span._2_R_DZ", "span.Wphh3N", "span._13vcmD"]:
                try:
                    el = page.query_selector(sel)
                    if el:
                        raw = re.sub(r"[^\d]", "", el.inner_text())
                        if raw: reviews = int(raw); break
                except: continue

            logger.info(f"[Flipkart] FINAL — price:Rs.{price} mrp:Rs.{mrp} name:{name}")
            return {
                "platform": "Flipkart", "name": name or "Flipkart Product",
                "price": price, "mrp": mrp, "rating": rating,
                "reviews": reviews, "url": final_url, "currency": "INR",
                "success": price is not None,
                "error": None if price else f"Price not found on page: '{title}'",
            }

        except Exception as e:
            logger.error(f"[Flipkart] Error: {e}")
            return {"success": False, "error": str(e), "platform": "Flipkart"}
        finally:
            browser.close()


def scrape_product(url: str) -> dict:
    full_url = expand_url(url)
    if "amazon" in full_url.lower():
        return scrape_amazon(full_url)
    elif "flipkart" in full_url.lower():
        return scrape_flipkart(full_url)
    else:
        return {"success": False, "error": f"Could not resolve to Amazon or Flipkart. Got: {full_url}"}


# ─────────────────────────────────────────────
# FLASK ROUTES
# ─────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "PriceRadar backend is running"})


@app.route("/api/debug", methods=["GET"])
def debug_page():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    full_url = expand_url(url)
    try:
        with sync_playwright() as p:
            browser, context, page = get_browser_page(p)
            page.goto(full_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            page.screenshot(path="flipkart_debug.png", full_page=True)
            title = page.title()
            html = page.content()
            browser.close()
        # Extract all class names containing price-like keywords
        classes = re.findall(r'class="([^"]*(?:price|Price|Nx9|jeq|jK9)[^"]*?)"', html)
        unique_classes = list(set(classes))[:30]
        return jsonify({
            "title": title,
            "resolved_url": full_url,
            "price_classes_found": unique_classes,
            "screenshot": "flipkart_debug.png saved in Price_reader folder",
            "hint": "Open flipkart_debug.png to see what Playwright loaded"
        })
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/product", methods=["GET"])
def get_product():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"success": False, "error": "url required"}), 400
    return jsonify(scrape_product(url))

@app.route("/api/price", methods=["GET"])
def get_price():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"success": False, "error": "url required"}), 400
    result = scrape_product(url)

    # ── Price stability filter ──
    # If new price deviates more than 25% from last known price,
    # it's likely a wrong element (EMI, exchange offer, bank cashback)
    # Reject it and return the last known good price instead
    current_price = result.get("price")
    if current_price and url in last_known_price:
        prev = last_known_price[url]
        deviation = abs(current_price - prev) / prev
        if deviation > 0.25:
            logger.warning(
                f"[Stability] Price jump {prev} → {current_price} "
                f"({deviation*100:.1f}%) — looks like wrong element, "
                f"keeping last known Rs.{prev}"
            )
            current_price = prev  # reject bad reading, use last known
        else:
            last_known_price[url] = current_price  # update last known
    elif current_price:
        last_known_price[url] = current_price  # first reading, store it
    if current_price:
        for alert_id, alert in list(alerts_store.items()):
            if alert["url"] == url and not alert["triggered"]:
                if current_price <= alert["target_price"]:
                    # SEND EMAIL!
                    sent = send_price_alert_email(
                        to_email=alert["email"],
                        product_name=alert["product_name"],
                        current_price=current_price,
                        target_price=alert["target_price"],
                        product_url=url
                    )
                    if sent:
                        alerts_store[alert_id]["triggered"] = True
                        logger.info(f"[Alert] Triggered for {alert['email']} at Rs.{current_price}")

    return jsonify({
        "success": result.get("success", False),
        "price": current_price,
        "platform": result.get("platform"),
        "timestamp": time.time(),
        "error": result.get("error"),
    })


@app.route("/api/set-alert", methods=["POST"])
def set_alert():
    """
    Set a price alert.
    Body: { url, email, target_price, product_name }
    """
    global alert_counter
    data = request.json
    url = data.get("url", "").strip()
    email = data.get("email", "").strip()
    target_price = data.get("target_price")
    product_name = data.get("product_name", "Product")

    if not url or not email or not target_price:
        return jsonify({"success": False, "error": "url, email and target_price are required"}), 400

    alert_counter += 1
    alert_id = f"alert_{alert_counter}"
    alerts_store[alert_id] = {
        "url": url,
        "email": email,
        "target_price": int(target_price),
        "product_name": product_name,
        "triggered": False,
        "created_at": time.time()
    }

    logger.info(f"[Alert] Set: {email} wants Rs.{target_price} for {product_name}")
    return jsonify({
        "success": True,
        "alert_id": alert_id,
        "message": f"Alert set! We'll email {email} when price drops to Rs.{target_price:,}"
    })


@app.route("/api/delete-alert", methods=["POST"])
def delete_alert():
    data = request.json
    alert_id = data.get("alert_id")
    if alert_id in alerts_store:
        del alerts_store[alert_id]
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Alert not found"}), 404


@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    return jsonify({"alerts": list(alerts_store.values())})


@app.route("/api/expand", methods=["GET"])
def debug_expand():
    """Debug: show what URL expand_url() generates from your input"""
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "url param required"}), 400
    expanded = expand_url(url)
    return jsonify({"original": url, "expanded": expanded})


@app.route("/api/debug-flipkart", methods=["GET"])
def debug_flipkart():
    """
    DEBUG ENDPOINT — opens the Flipkart URL and returns:
    - page title
    - all div/span class names found on page
    - any text containing rupee symbol
    Usage: GET /api/debug-flipkart?url=https://www.flipkart.com/...
    """
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400

    # Expand if needed
    full_url = expand_url(url)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-IN",
            viewport={"width": 1366, "height": 768},
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        page = context.new_page()

        try:
            page.goto(full_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            title = page.title()

            # Extract all class names from divs and spans
            classes = page.evaluate("""() => {
                const els = document.querySelectorAll('div, span');
                const seen = new Set();
                els.forEach(el => {
                    el.className.split(' ').forEach(c => { if(c) seen.add(c); });
                });
                return Array.from(seen);
            }""")

            # Find all elements with rupee symbol
            rupee_texts = page.evaluate("""() => {
                const results = [];
                const all = document.querySelectorAll('*');
                all.forEach(el => {
                    if (el.children.length === 0) {
                        const txt = el.innerText || '';
                        if (txt.includes('₹') || txt.includes('Rs')) {
                            results.push({
                                tag: el.tagName,
                                class: el.className,
                                text: txt.trim().slice(0, 50)
                            });
                        }
                    }
                });
                return results.slice(0, 30);
            }""")

            browser.close()
            return jsonify({
                "title": title,
                "final_url": full_url,
                "all_classes": sorted(classes)[:100],
                "rupee_elements": rupee_texts,
            })
        except Exception as e:
            browser.close()
            return jsonify({"error": str(e)})


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  PriceRadar Backend Starting...")
    print("  http://localhost:5000")
    print("  Email alerts    : ENABLED")
    print("  Debug endpoint  : /api/debug-flipkart?url=...")
    print("="*50 + "\n")
    app.run(debug=False, host="0.0.0.0", port=5000)
