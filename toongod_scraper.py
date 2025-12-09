#!/usr/bin/env python3
"""
ToonGod Scraper - Downloads chapters and images from toongod.org series

This script is tailored for WordPress Madara-based ToonGod pages, e.g.:
https://www.toongod.org/webtoon/magnetic-pull/

Features
- Robust image extraction from chapter pages (img src/data-src variations)
- Proper headers (Referer, Origin, Accept) to bypass hotlink protection
- Optional Playwright rendering for dynamic sites, with cookie sync
- Parallel validation (optional) and concurrent downloads
- Retries for transient CDN/network errors
"""

import argparse
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib.parse import urljoin, urlparse
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('toongod_scraper.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class ToonGodScraper:
    def __init__(
        self,
        base_url: str = "https://www.toongod.org",
        download_dir: str = "downloads_toongod",
        use_playwright: bool = False,
        playwright_wait: float = 1.5,
        validate_urls: bool = False,
        max_workers: int = 6,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        self.download_dir = Path(download_dir)
        self.use_playwright = use_playwright
        self.playwright_wait = max(0.0, float(playwright_wait))
        self.validate_urls = validate_urls
        self.max_workers = max(1, int(max_workers))

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        # Retry policy
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"],
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

        # Playwright
        self.playwright = None
        self.playwright_browser = None
        self.playwright_context = None

    # --------------- Utils ---------------
    def sanitize(self, s: str) -> str:
        return re.sub(r'[<>:"/\\|?*]', '_', s).strip()

    def mkdir(self, p: Path) -> None:
        p.mkdir(parents=True, exist_ok=True)

    def _build_img_headers(self, referer: Optional[str]) -> Dict[str, str]:
        headers = {
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': referer or self.base_url,
        }
        try:
            src = referer or self.base_url
            u = urlparse(src)
            headers['Origin'] = f"{u.scheme}://{u.netloc}"
        except Exception:
            pass
        return headers

    def _init_playwright(self) -> None:
        if not self.use_playwright or self.playwright is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
            self.playwright = sync_playwright().start()
            self.playwright_browser = self.playwright.chromium.launch(headless=True)
            self.playwright_context = self.playwright_browser.new_context(
                user_agent=self.session.headers.get('User-Agent')
            )
        except Exception as e:
            logger.warning(f"Playwright init failed, disabling: {e}")
            self.use_playwright = False

    def _sync_cookies_from_playwright(self, url: Optional[str] = None) -> None:
        if not self.playwright_context:
            return
        try:
            cookies = self.playwright_context.cookies(url) if url else self.playwright_context.cookies()
            for c in cookies:
                try:
                    self.session.cookies.set(
                        name=c.get('name'),
                        value=c.get('value'),
                        domain=c.get('domain').lstrip('.'),
                        path=c.get('path', '/'),
                    )
                except Exception:
                    continue
        except Exception:
            pass

    def _close_playwright(self) -> None:
        try:
            if self.playwright_context is not None:
                self.playwright_context.close()
            if self.playwright_browser is not None:
                self.playwright_browser.close()
            if self.playwright is not None:
                self.playwright.stop()
        except Exception:
            pass
        finally:
            self.playwright = None
            self.playwright_browser = None
            self.playwright_context = None

    # --------------- HTTP ---------------
    def get_soup(self, url: str) -> Optional[BeautifulSoup]:
        # Try Playwright first if enabled
        if self.use_playwright:
            try:
                self._init_playwright()
                if self.playwright_context is not None:
                    page = self.playwright_context.new_page()
                    page.goto(url, wait_until='load', timeout=45000)
                    if self.playwright_wait > 0:
                        time.sleep(self.playwright_wait)
                    html = page.content()
                    page.close()
                    # sync cookies for subsequent requests
                    self._sync_cookies_from_playwright(url)
                    return BeautifulSoup(html, 'html.parser')
            except Exception as e:
                logger.warning(f"Playwright fetch failed for {url}, falling back to requests: {e}")
        try:
            resp = self.session.get(url, timeout=30, headers={'Referer': self.base_url})
            resp.raise_for_status()
            return BeautifulSoup(resp.content, 'html.parser')
        except Exception as e:
            logger.error(f"GET failed {url}: {e}")
            return None

    def get_page_text(self, url: str) -> Optional[str]:
        # Similar to get_soup, but return HTML text
        if self.use_playwright:
            try:
                self._init_playwright()
                if self.playwright_context is not None:
                    page = self.playwright_context.new_page()
                    page.goto(url, wait_until='load', timeout=45000)
                    if self.playwright_wait > 0:
                        time.sleep(self.playwright_wait)
                    html = page.content()
                    page.close()
                    self._sync_cookies_from_playwright(url)
                    return html
            except Exception as e:
                logger.warning(f"Playwright fetch failed for {url}, falling back to requests: {e}")
        try:
            resp = self.session.get(url, timeout=30, headers={'Referer': self.base_url})
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.error(f"GET failed {url}: {e}")
            return None

    def _ajax_post_playwright(self, url: str, data: Dict[str, str], headers: Dict[str, str]) -> Optional[str]:
        try:
            if not self.playwright_context:
                return None
            resp = self.playwright_context.request.post(url, data=data, headers=headers, timeout=30000)
            try:
                ok = resp.ok
            except Exception:
                ok = False
            if not ok:
                return None
            return resp.text()
        except Exception as e:
            logger.debug(f"Playwright POST failed {url}: {e}")
            return None

    def _get_manga_id_from_series_page(self, series_url: str) -> Optional[str]:
        html = self.get_page_text(series_url)
        if not html:
            return None
        # Try several patterns
        patterns = [
            r'"manga_id"\s*:\s*"(\d+)"',
            r"data-id=\"(\d+)\"",
            r"data-postid=\"(\d+)\"",
        ]
        for pat in patterns:
            m = re.search(pat, html, flags=re.I)
            if m:
                mid = m.group(1)
                logger.debug(f"Extracted manga_id via pattern: {mid}")
                return mid
        # Try body class like postid-8832
        m = re.search(r'postid-(\d+)', html)
        if m:
            mid = m.group(1)
            logger.debug(f"Extracted manga_id via postid class: {mid}")
            return mid
        return None

    def _fetch_chapters_via_ajax(self, series_url: str) -> List[Dict[str, str]]:
        chapters: List[Dict[str, str]] = []
        manga_id = self._get_manga_id_from_series_page(series_url)
        if not manga_id:
            logger.debug("Could not extract manga_id from series page")
            return chapters
        logger.debug(f"Using manga_id={manga_id} for AJAX chapter fetch")
        ajax_url = urljoin(self.base_url + '/', 'wp-admin/admin-ajax.php')
        payload = {
            'action': 'manga_get_chapters',
            'manga': manga_id,
        }
        headers = {
            'Referer': series_url,
            'Origin': self.base_url,
            'X-Requested-With': 'XMLHttpRequest',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        }
        text = None
        try:
            r = self.session.post(ajax_url, data=payload, headers=headers, timeout=30)
            if r.status_code == 200:
                text = r.text
        except Exception as e:
            logger.debug(f"AJAX POST failed via requests: {e}")
        if text is None and self.use_playwright and self.playwright_context is not None:
            text = self._ajax_post_playwright(ajax_url, payload, headers)
        if not text:
            return chapters
        # Parse returned HTML
        soup = BeautifulSoup(text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/webtoon/' in href and '/chapter-' in href:
                title = a.get_text(strip=True)
                full = href if href.startswith('http') else urljoin(self.base_url, href)
                m = re.search(r'chapter-([\w-]+)', href)
                num_key = m.group(1) if m else title
                chapters.append({'number': num_key, 'title': title, 'url': full})
        # Deduplicate preserve order
        seen = set()
        uniq = []
        for ch in chapters:
            if ch['url'] not in seen:
                seen.add(ch['url'])
                uniq.append(ch)
        return uniq

    def _fetch_chapters_via_playwright_dom(self, series_url: str) -> List[Dict[str, str]]:
        if not self.use_playwright:
            return []
        try:
            self._init_playwright()
            if not self.playwright_context:
                return []
            page = self.playwright_context.new_page()
            page.goto(series_url, wait_until='domcontentloaded', timeout=45000)
            try:
                page.wait_for_load_state('networkidle', timeout=10000)
            except Exception:
                pass
            # Try small waits and scans with scroll to bottom to trigger lazy loads
            total_wait = max(1.0, self.playwright_wait)
            page.wait_for_timeout(int(total_wait * 1000))
            # Attempt clicking any 'Show more' buttons
            try:
                buttons = page.locator("button, a").filter(has_text=re.compile(r"show more|more|expand|load more", re.I))
                count = buttons.count()
                for i in range(min(3, count)):
                    try:
                        buttons.nth(i).click(timeout=1000)
                        page.wait_for_timeout(500)
                    except Exception:
                        continue
            except Exception:
                pass
            # Scroll down a few times
            for _ in range(5):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(400)

            # Wait for chapter anchors if they exist
            try:
                page.wait_for_selector("a[href*='/webtoon/'][href*='/chapter-']", timeout=15000)
            except Exception:
                pass
            # Extract chapter anchors from DOM
            anchors = page.eval_on_selector_all(
                "a[href*='/webtoon/'][href*='/chapter-']",
                "els => els.map(a => ({href: a.getAttribute('href'), text: a.textContent.trim()}))"
            )
            page.close()
            chapters: List[Dict[str, str]] = []
            if anchors:
                for a in anchors:
                    href = a.get('href') or ''
                    if not href:
                        continue
                    title = a.get('text', '').strip()
                    full = href if href.startswith('http') else urljoin(self.base_url, href)
                    m = re.search(r"chapter-([\w-]+)", href)
                    num_key = m.group(1) if m else title
                    chapters.append({'number': num_key, 'title': title, 'url': full})
            # Dedup preserve order
            seen = set()
            uniq = []
            for ch in chapters:
                if ch['url'] not in seen:
                    seen.add(ch['url'])
                    uniq.append(ch)
            return uniq
        except Exception as e:
            logger.debug(f"Playwright DOM extraction failed: {e}")
            return []

    def _fallback_chapters_from_first_last(self, series_url: str) -> List[Dict[str, str]]:
        if not self.use_playwright:
            return []
        try:
            self._init_playwright()
            if not self.playwright_context:
                return []
            page = self.playwright_context.new_page()
            page.goto(series_url, wait_until='domcontentloaded', timeout=45000)
            try:
                page.wait_for_load_state('networkidle', timeout=8000)
            except Exception:
                pass
            # Try to find Read Last link
            last_href = None
            try:
                last_href = page.eval_on_selector("a:has-text('Read Last')", "a => a && a.getAttribute('href')")
            except Exception:
                pass
            if not last_href:
                # Try alternative text casing or language neutral approach
                try:
                    last_href = page.eval_on_selector("a[href*='/chapter-']:nth-of-type(1)", "a => a && a.getAttribute('href')")
                except Exception:
                    pass
            page.close()
            if not last_href:
                return []
            m = re.search(r"chapter-(\d+)", last_href)
            if not m:
                return []
            last_num = int(m.group(1))
            base_series = series_url.rstrip('/') + '/'
            chapters: List[Dict[str, str]] = []
            for i in range(0, last_num + 1):
                ch_url = urljoin(base_series, f"chapter-{i}/")
                title = f"Chapter {i}"
                chapters.append({'number': str(i), 'title': title, 'url': ch_url})
            return chapters
        except Exception:
            return []

    # --------------- Extraction ---------------
    def extract_chapters(self, series_url: str) -> List[Dict[str, str]]:
        # First, try Playwright DOM extraction if enabled
        chapters = self._fetch_chapters_via_playwright_dom(series_url)
        if chapters:
            logger.info(f"Found {len(chapters)} chapters via DOM")
            # Sort chapters as before
            def sort_key_dom(c: Dict[str, str]):
                s = c['number'].lower()
                if 'prologue' in s:
                    return -1
                try:
                    m = re.search(r'(\d+)', s)
                    return int(m.group(1)) if m else 10**9
                except Exception:
                    return 10**9
            chapters = sorted(chapters, key=sort_key_dom)
            return chapters

        # Next, try deriving from "Read Last" link (range-based)
        chapters = self._fallback_chapters_from_first_last(series_url)
        if chapters:
            logger.info(f"Derived {len(chapters)} chapters from 'Read Last' link")
            return chapters

        # Next, try Madara AJAX endpoint (most reliable when accessible)
        chapters = self._fetch_chapters_via_ajax(series_url)
        if chapters:
            # Sort chapters as before
            def sort_key(c: Dict[str, str]):
                s = c['number'].lower()
                if 'prologue' in s:
                    return -1
                try:
                    m = re.search(r'(\d+)', s)
                    return int(m.group(1)) if m else 10**9
                except Exception:
                    return 10**9
            chapters = sorted(chapters, key=sort_key)
            logger.info(f"Found {len(chapters)} chapters")
            return chapters

        # Fallback: parse directly from series page anchors
        soup = self.get_soup(series_url)
        if not soup:
            return []
        chapters = []
        # Madara theme: chapter list anchors contain '/chapter-'
        for a in soup.find_all('a', href=True):
            href = a['href']
            if re.search(r'/webtoon/[^/]+/chapter-[^/]+/?$', href):
                title = a.get_text(strip=True)
                full = href if href.startswith('http') else urljoin(self.base_url, href)
                # Try extract number for sorting
                m = re.search(r'chapter-([\w-]+)', href)
                num_key = m.group(1) if m else title
                chapters.append({'number': num_key, 'title': title, 'url': full})
        # Deduplicate by URL and keep order (the page lists recent first)
        seen = set()
        uniq = []
        for ch in chapters:
            if ch['url'] not in seen:
                seen.add(ch['url'])
                uniq.append(ch)
        # Sort by numeric when possible, else lexicographically, but ensure prologue (0) first
        def sort_key(c: Dict[str, str]):
            s = c['number'].lower()
            if 'prologue' in s:
                return -1
            try:
                # Take only leading integer if present
                m = re.search(r'(\d+)', s)
                return int(m.group(1)) if m else 10**9
            except Exception:
                return 10**9
        uniq_sorted = sorted(uniq, key=sort_key)
        logger.info(f"Found {len(uniq_sorted)} chapters")
        return uniq_sorted

    def _is_valid_image_url(self, url: str) -> bool:
        if not url or not isinstance(url, str):
            return False
        if url.startswith('blob:') or url.startswith('data:'):
            return False
        if not url.startswith('http'):
            return False
        url_l = url.lower()
        if not any(ext in url_l for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']):
            return False
        try:
            return bool(urlparse(url).netloc)
        except Exception:
            return False

    def extract_images_from_chapter(self, chapter_url: str) -> List[str]:
        soup = self.get_soup(chapter_url)
        if not soup:
            return []
        images: List[str] = []
        # Madara: images are usually within .reading-content img
        container = soup.find(class_=re.compile(r'reading-content|chapter-content', re.I))
        if not container:
            container = soup
        for img in container.find_all('img'):
            for attr in ['data-src', 'data-original', 'data-lazy-src', 'src']:
                src = img.get(attr)
                if src and self._is_valid_image_url(src):
                    images.append(src)
                    break
        # Fallback: scan scripts/HTML for image URLs
        page_text = str(soup)
        patterns = [
            r'https?://[^\s<>"\'{}|\\^`\[\]]*\.(?:jpg|jpeg|png|webp|gif)'
        ]
        for pat in patterns:
            for u in re.findall(pat, page_text, flags=re.I):
                if self._is_valid_image_url(u):
                    images.append(u)
        # Deduplicate while preserving order
        seen = set()
        ordered = []
        for u in images:
            if u not in seen:
                seen.add(u)
                ordered.append(u)
        # Limit
        if len(ordered) > 200:
            logger.warning(f"Found {len(ordered)} images, limiting to first 200")
            ordered = ordered[:200]
        logger.info(f"Found {len(ordered)} images in chapter")
        return ordered

    # --------------- Network helpers ---------------
    def test_image_url(self, url: str, referer: Optional[str]) -> bool:
        headers = self._build_img_headers(referer)
        head_resp = None
        try:
            head_resp = self.session.head(url, timeout=6, allow_redirects=True, headers=headers)
            if head_resp.status_code == 200:
                ctype = head_resp.headers.get('content-type', '').lower()
                if (not ctype) or ('image' in ctype):
                    return True
        except Exception:
            pass
        finally:
            try:
                if head_resp is not None:
                    head_resp.close()
            except Exception:
                pass
        # GET fallback
        try:
            with self.session.get(url, timeout=10, stream=True, headers=headers) as r:
                if r.status_code == 200:
                    ctype = r.headers.get('content-type', '').lower()
                    if (not ctype) or ('image' in ctype):
                        # read a tiny chunk
                        try:
                            next(r.iter_content(chunk_size=512))
                        except StopIteration:
                            pass
                        return True
        except Exception:
            pass
        return False

    def _download_with_playwright(self, url: str, filepath: Path, headers: Dict[str, str]) -> bool:
        try:
            if not self.playwright_context:
                return False
            resp = self.playwright_context.request.get(url, headers=headers, timeout=30000)
            try:
                ok = resp.ok
            except Exception:
                ok = False
            if not ok:
                return False
            content = resp.body()
            with open(filepath, 'wb') as f:
                f.write(content)
            return True
        except Exception:
            return False

    def download_image(self, url: str, filepath: Path, referer: Optional[str]) -> bool:
        headers = self._build_img_headers(referer)
        try:
            if self.use_playwright and self.playwright_context is not None:
                if self._download_with_playwright(url, filepath, headers):
                    return True
            with self.session.get(url, timeout=20, stream=True, headers=headers) as r:
                r.raise_for_status()
                with open(filepath, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            return True
        except Exception as e:
            logger.warning(f"Download failed {url}: {e}")
            return False

    def validate_urls_parallel(self, urls: List[str], referer: Optional[str]) -> List[str]:
        if not urls:
            return []
        def check(u: str):
            return u, self.test_image_url(u, referer)
        workers = max(1, min(self.max_workers, len(urls)))
        valid: List[str] = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(check, u) for u in urls]
            for fut in as_completed(futs):
                u, ok = fut.result()
                if ok:
                    valid.append(u)
        return valid

    # --------------- Download flow ---------------
    def download_chapter(self, series_title: str, chapter: Dict[str, str], delay: float) -> bool:
        title_safe = self.sanitize(chapter['title'] or f"Chapter_{chapter['number']}")
        chapter_dir = self.download_dir / self.sanitize(series_title) / f"{self.sanitize(chapter['number'])}_{title_safe}"
        self.mkdir(chapter_dir)
        logger.info(f"Downloading chapter {chapter['number']}: {chapter['title']}")
        imgs = self.extract_images_from_chapter(chapter['url'])
        if not imgs:
            logger.warning("No images found in chapter")
            return False
        if self.validate_urls:
            logger.info(f"Validating {len(imgs)} images...")
            imgs = self.validate_urls_parallel(imgs, referer=chapter['url'])
            if not imgs:
                logger.warning("No valid images after validation")
                return False
        # concurrency
        workers = max(1, min(self.max_workers, len(imgs)))
        if self.use_playwright:
            workers = min(workers, 2)
        logger.info(f"Downloading {len(imgs)} images with concurrency={workers}")
        success = 0
        def download_one(iu):
            i, u = iu
            fname = f"page_{i:03d}.jpg"
            fpath = chapter_dir / fname
            if fpath.exists():
                return True
            ok = self.download_image(u, fpath, referer=chapter['url'])
            if not ok:
                time.sleep(0.3)
                ok = self.download_image(u, fpath, referer=chapter['url'])
            return ok
        indices = list(enumerate(imgs, 1))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(download_one, iu) for iu in indices]
            for fut in as_completed(futs):
                if fut.result():
                    success += 1
        logger.info(f"Downloaded {success}/{len(imgs)} images for chapter {chapter['number']}")
        return success > 0

    def download_series(self, series_url: str, delay: float) -> None:
        # Figure out series title from page
        soup = self.get_soup(series_url)
        series_title = "Series"
        if soup:
            h1 = soup.find(['h1', 'h2'], class_=re.compile(r'title|name', re.I)) or soup.find(['h1', 'h2'])
            if h1:
                series_title = self.sanitize(h1.get_text(strip=True)) or series_title
        chapters = self.extract_chapters(series_url)
        if not chapters:
            logger.error("No chapters found")
            return
        for ch in chapters:
            try:
                self.download_chapter(series_title, ch, delay)
                time.sleep(delay)
            except Exception as e:
                logger.error(f"Error downloading chapter {ch['number']}: {e}")
        # Cleanup
        self._close_playwright()


def main() -> None:
    parser = argparse.ArgumentParser(description='Scrape a ToonGod series and download chapters')
    parser.add_argument('--url', required=True, help='Series URL, e.g. https://www.toongod.org/webtoon/magnetic-pull/')
    parser.add_argument('--download-dir', default='toongod_downloads', help='Download directory')
    parser.add_argument('--delay', type=float, default=0.5, help='Delay between chapters (seconds)')
    parser.add_argument('--use-playwright', action='store_true', help='Use Playwright to render pages (recommended)')
    parser.add_argument('--pw-wait', type=float, default=1.5, help='Wait time after Playwright load (seconds)')
    parser.add_argument('--validate-urls', action='store_true', help='Validate image URLs before downloading')
    parser.add_argument('--max-workers', type=int, default=6, help='Max concurrency for validation/downloads')
    args = parser.parse_args()

    scraper = ToonGodScraper(
        base_url='https://www.toongod.org',
        download_dir=args.download_dir,
        use_playwright=args.use_playwright,
        playwright_wait=args.pw_wait,
        validate_urls=args.validate_urls,
        max_workers=args.max_workers,
    )
    scraper.download_series(args.url, delay=args.delay)
    logger.info('Done!')


if __name__ == '__main__':
    main()
