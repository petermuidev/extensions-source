#!/usr/bin/env python3
"""
Manhwa Scraper - Downloads all chapters and images from manhwa series
"""

import signal
import sys
import json
import os
import re
import time
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import logging
from pathlib import Path
from typing import List, Dict, Optional
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('manhwa_scraper.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ManhwaScraper:
    def __init__(self, base_url: str = "https://manhwaread.com", download_dir: str = "downloads", use_playwright: bool = False, playwright_wait: float = 3.0, validate_urls: bool = False, max_workers: int = 6):
        self.base_url = base_url
        self.download_dir = Path(download_dir)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9'
        })
        # Add retry policy to handle transient network/CDN hiccups
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.interrupted = False
        self.use_playwright = use_playwright
        self.playwright_wait = max(playwright_wait, 0.0)
        self.validate_urls = validate_urls
        self.max_workers = max(1, int(max_workers))
        self.playwright = None
        self.playwright_browser = None
        self.playwright_context = None

    def signal_handler(self, signum, frame):
        """Handle interrupt signal"""
        logger.warning("Interrupt received. Finishing current chapter and exiting...")
        self.interrupted = True

    def sanitize_filename(self, filename: str) -> str:
        """Sanitize filename to be safe for filesystem"""
        return re.sub(r'[<>:"/\\|?*]', '_', filename)

    def create_directory(self, path: Path) -> None:
        """Create directory if it doesn't exist"""
        path.mkdir(parents=True, exist_ok=True)

    def _build_headers_for_image(self, referer: Optional[str] = None) -> Dict[str, str]:
        """Build headers suitable for image requests (handle hotlink protection)."""
        headers = {
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        # Prefer chapter page as referer, otherwise site base
        headers['Referer'] = referer or self.base_url
        try:
            origin = None
            if referer:
                p = urlparse(referer)
                origin = f"{p.scheme}://{p.netloc}"
            else:
                p = urlparse(self.base_url)
                origin = f"{p.scheme}://{p.netloc}"
            headers['Origin'] = origin
        except Exception:
            pass
        return headers

    def _init_playwright(self) -> None:
        """Lazily initialize Playwright bits if enabled."""
        if not self.use_playwright or self.playwright is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
            self.playwright = sync_playwright().start()
            self.playwright_browser = self.playwright.chromium.launch(headless=True)
            self.playwright_context = self.playwright_browser.new_context(user_agent=self.session.headers.get('User-Agent'))
        except Exception as e:
            logger.warning(f"Failed to initialize Playwright, falling back to requests only: {e}")
            self.use_playwright = False

    def _sync_cookies_from_playwright(self, url: Optional[str] = None) -> None:
        """Copy cookies from Playwright context into requests Session to satisfy CDNs that require tokens."""
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
        except Exception as e:
            logger.debug(f"Failed to sync cookies from Playwright: {e}")

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

    def get_soup(self, url: str) -> BeautifulSoup:
        """Get BeautifulSoup object from URL (Playwright fallback if enabled)."""
        # Try Playwright rendering for dynamic pages
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
                    # Sync cookies from Playwright to requests for subsequent image downloads
                    self._sync_cookies_from_playwright(url)
                    return BeautifulSoup(html, 'html.parser')
            except Exception as e:
                logger.warning(f"Playwright fetch failed for {url}, falling back to requests: {e}")

        # Fallback to requests
        try:
            response = self.session.get(url, timeout=30, headers={'Referer': self.base_url})
            response.raise_for_status()
            return BeautifulSoup(response.content, 'html.parser')
        except requests.RequestException as e:
            logger.error(f"Error fetching {url}: {e}")
            return None

    def extract_chapters(self, manhwa_url: str) -> List[Dict[str, str]]:
        """Extract all chapters from a manhwa page"""
        soup = self.get_soup(manhwa_url)
        if not soup:
            return []

        chapters = []
        # Look for chapter links
        chapter_links = soup.find_all('a', href=re.compile(r'/manhwa/[^/]+/chapter-'))

        for link in chapter_links:
            href = link.get('href')
            if href:
                # Extract chapter number from URL
                chapter_match = re.search(r'chapter-(\d+)', href)
                if chapter_match:
                    chapter_num = chapter_match.group(1)
                    chapter_title = link.get_text(strip=True)
                    if not chapter_title:
                        chapter_title = f"Chapter {chapter_num}"

                    full_url = urljoin(self.base_url, href)
                    chapters.append({
                        'number': chapter_num,
                        'title': chapter_title,
                        'url': full_url
                    })

        # Remove duplicates and sort by chapter number
        seen = set()
        unique_chapters = []
        for chapter in sorted(chapters, key=lambda x: int(x['number'])):
            if chapter['url'] not in seen:
                seen.add(chapter['url'])
                unique_chapters.append(chapter)

        logger.info(f"Found {len(unique_chapters)} chapters")
        return unique_chapters

    def extract_images_from_chapter(self, chapter_url: str) -> List[str]:
        """Extract image URLs from a chapter page"""
        soup = self.get_soup(chapter_url)
        if not soup:
            return []

        images = []

        # Method 1: Look for img tags with various attributes
        img_tags = soup.find_all('img')
        for img in img_tags:
            # Try different attributes for image sources
            for attr in ['data-src', 'data-original', 'data-lazy-src', 'data-url', 'src']:
                src = img.get(attr)
                if src and self._is_valid_image_url(src):
                    if src not in images:
                        images.append(src)
                        break

        # Method 2: Look for script tags that might contain image URLs
        scripts = soup.find_all('script')
        for script in scripts:
            script_text = script.string or ''
            if script_text:
                # Look for image URLs in script content with various patterns
                patterns = [
                    r'["\']([^"\']*\.(?:jpg|jpeg|png|webp|gif))["\']',
                    r'["\']([^"\']*\.(?:jpg|jpeg|png|webp|gif)[^"\']*)["\']',
                    r'url["\']?\s*:\s*["\']([^"\']*\.(?:jpg|jpeg|png|webp|gif))["\']',
                    r'src["\']?\s*:\s*["\']([^"\']*\.(?:jpg|jpeg|png|webp|gif))["\']'
                ]

                for pattern in patterns:
                    img_urls = re.findall(pattern, script_text, re.IGNORECASE)
                    for url in img_urls:
                        if not url.startswith('http'):
                            url = urljoin(self.base_url, url)
                        if self._is_valid_image_url(url) and url not in images:
                            images.append(url)

        # Method 3: Look for common image hosting patterns and data attributes
        page_text = str(soup)
        # Look for image URLs in the page content with broader patterns
        img_patterns = [
            r'https?://[^\s<>"{}|\\^`\[\]]*\.(?:jpg|jpeg|png|webp|gif)',
            r'data-[a-zA-Z0-9-]*["\']?\s*:\s*["\']([^"\']*\.(?:jpg|jpeg|png|webp|gif))["\']',
            r'src["\']?\s*=\s*["\']([^"\']*\.(?:jpg|jpeg|png|webp|gif))["\']'
        ]

        for pattern in img_patterns:
            found_urls = re.findall(pattern, page_text, re.IGNORECASE)
            for url in found_urls:
                if not url.startswith('http'):
                    url = urljoin(self.base_url, url)
                if self._is_valid_image_url(url) and url not in images:
                    images.append(url)

        # Method 4: Try to find images by examining div containers that might hold chapters
        chapter_containers = soup.find_all(['div', 'section', 'article'], class_=re.compile(r'(chapter|content|page|image|img)', re.IGNORECASE))
        for container in chapter_containers:
            # Look for background images in styles
            style = container.get('style', '')
            bg_patterns = [
                r'background-image\s*:\s*url\(["\']?([^"\']*\.(?:jpg|jpeg|png|webp|gif))["\']?\)',
                r'background\s*:\s*[^;]*url\(["\']?([^"\']*\.(?:jpg|jpeg|png|webp|gif))["\']?\)'
            ]

            for pattern in bg_patterns:
                bg_urls = re.findall(pattern, style, re.IGNORECASE)
                for url in bg_urls:
                    if not url.startswith('http'):
                        url = urljoin(self.base_url, url)
                    if self._is_valid_image_url(url) and url not in images:
                        images.append(url)

        # Method 5: Try to construct image URLs based on common patterns
        constructed_images = self._construct_image_urls(chapter_url)
        for url in constructed_images:
            if url not in images:
                images.append(url)

        # Remove duplicates and filter out non-image URLs
        filtered_images = []
        for url in images:
            if self._is_valid_image_url(url):
                filtered_images.append(url)

        # Limit to prevent hanging on too many URLs
        max_images = 100  # Reasonable limit for a chapter
        if len(filtered_images) > max_images:
            logger.warning(f"Found {len(filtered_images)} images, limiting to {max_images} most likely candidates")
            # Keep first few and some from the middle to get a good sample
            filtered_images = filtered_images[:50] + filtered_images[-50:]

        logger.info(f"Found {len(filtered_images)} valid images in chapter")
        return filtered_images

    def _is_valid_image_url(self, url: str) -> bool:
        """Check if URL is a valid image URL"""
        if not url or not isinstance(url, str):
            return False

        # Skip blob URLs and data URLs
        if url.startswith('blob:') or url.startswith('data:'):
            return False

        # Check for image extensions
        image_extensions = ['.jpg', '.jpeg', '.png', '.webp', '.gif']
        url_lower = url.lower()

        # Must have image extension
        has_extension = any(ext in url_lower for ext in image_extensions)
        if not has_extension:
            return False

        # Must be HTTP/HTTPS
        if not url.startswith('http'):
            return False

        # Relax domain filtering: accept any http(s) domain to avoid missing valid CDNs
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if not domain:
                return False
        except Exception as e:
            logger.debug(f"Error parsing URL {url}: {e}")
            return False

        return True

    def _construct_image_urls(self, chapter_url: str) -> List[str]:
        """Try to construct image URLs based on common patterns"""
        urls = []

        try:
            # Parse the chapter URL to understand the structure
            parsed = urlparse(chapter_url)
            base = f"{parsed.scheme}://{parsed.netloc}"

            # Common patterns for manhwa image hosting
            patterns = [
                # Direct image patterns
                chapter_url.replace('/chapter-', '/images/'),
                chapter_url + '/images/',
                # CDN patterns
                chapter_url.replace('manhwaread.com', 'mancover.xyz'),
                # API-like patterns
                chapter_url + '/api/images',
                chapter_url + '/assets/images',
            ]

            for pattern in patterns:
                # Try to add image numbers - but limit to reasonable range
                for i in range(1, 30):  # Reduced from 50 to 30
                    img_patterns = [
                        f"{pattern}/page_{i:03d}.jpg",
                        f"{pattern}/{i:03d}.jpg",
                        f"{pattern}/img_{i:03d}.jpg",
                        f"{pattern}/image_{i:03d}.jpg",
                        f"{pattern}/page_{i}.jpg",
                    ]

                    for img_url in img_patterns:
                        if img_url not in urls:
                            urls.append(img_url)

        except Exception as e:
            logger.debug(f"Error constructing URLs: {e}")

        return urls

    def _download_with_playwright_request(self, url: str, filepath: Path, headers: Dict[str, str]) -> bool:
        """Try downloading via Playwright's authenticated request context to reuse cookies."""
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
        except Exception as e:
            logger.debug(f"Playwright request download failed for {url}: {e}")
            return False

    def download_image(self, url: str, filepath: Path, referer: Optional[str] = None) -> bool:
        """Download a single image with proper headers (handles hotlink protection)."""
        headers = self._build_headers_for_image(referer)
        try:
            # If using Playwright, try its request context first (better for cookie-protected CDNs)
            if self.use_playwright and self.playwright_context is not None:
                ok = self._download_with_playwright_request(url, filepath, headers)
                if ok:
                    logger.debug(f"Downloaded via Playwright: {filepath}")
                    return True
            # Fallback to requests
            with self.session.get(url, timeout=15, stream=True, headers=headers) as response:
                response.raise_for_status()
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            logger.debug(f"Downloaded: {filepath}")
            return True
        except Exception as e:
            logger.error(f"Error downloading {url}: {e}")
            return False

    def test_image_url(self, url: str, referer: Optional[str] = None) -> bool:
        """Test if an image URL is accessible. Uses HEAD first, with a lightweight GET fallback.
        Adds Referer/Accept headers to bypass hotlink protection and uses short timeouts to avoid stalls.
        """
        headers = self._build_headers_for_image(referer)
        head_response = None
        try:
            head_response = self.session.head(url, timeout=6, allow_redirects=True, headers=headers)
            if head_response.status_code == 200:
                content_type = head_response.headers.get('content-type', '').lower()
                if not content_type or 'image' in content_type:
                    return True
        except requests.RequestException as e:
            logger.debug(f"HEAD request failed for {url}: {e}")
        except Exception as e:
            logger.debug(f"Unexpected error during HEAD request for {url}: {e}")
        finally:
            if head_response is not None:
                head_response.close()

        # Some CDNs block HEAD requests, so fall back to a light GET check before downloading.
        try:
            with self.session.get(url, timeout=8, stream=True, headers=headers) as get_response:
                if get_response.status_code == 200:
                    content_type = get_response.headers.get('content-type', '').lower()
                    if not content_type or 'image' in content_type:
                        try:
                            next(get_response.iter_content(chunk_size=512))
                        except StopIteration:
                            pass
                        return True
        except requests.RequestException as e:
            logger.debug(f"GET request failed for {url}: {e}")
        except Exception as e:
            logger.debug(f"Unexpected error during GET request for {url}: {e}")
        return False

    def validate_image_urls(self, urls: List[str], referer: Optional[str] = None, max_workers: Optional[int] = None) -> List[str]:
        """Validate image URLs in parallel to avoid long sequential waits."""
        valid: List[str] = []
        if not urls:
            return valid

        def check(u: str):
            return u, self.test_image_url(u, referer=referer)

        if max_workers is None:
            max_workers = self.max_workers
        workers = max(1, min(max_workers, len(urls)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(check, u) for u in urls]
            completed = 0
            for fut in as_completed(futures):
                url, ok = fut.result()
                completed += 1
                if ok:
                    valid.append(url)
                if completed % 10 == 0:
                    logger.debug(f"Validated {completed}/{len(urls)} URLs...")
                if self.interrupted:
                    logger.info("Validation interrupted by user")
                    break
        return valid

    def _download_one(self, idx_and_url, chapter_dir: Path, referer: Optional[str]) -> bool:
        i, img_url = idx_and_url
        filename = f"page_{i:03d}.jpg"
        filepath = chapter_dir / filename
        if filepath.exists():
            logger.debug(f"Skipping existing file: {filepath}")
            return True
        ok = self.download_image(img_url, filepath, referer=referer)
        if not ok:
            logger.warning(f"Failed to download page {i}: {img_url}")
        return ok

    def download_chapter(self, manhwa_title: str, chapter: Dict[str, str], delay: float = 1.0) -> bool:
        """Download all images from a chapter"""
        chapter_title = self.sanitize_filename(chapter['title'])
        chapter_dir = self.download_dir / manhwa_title / f"Chapter_{chapter['number']}_{chapter_title}"
        self.create_directory(chapter_dir)

        logger.info(f"Downloading chapter {chapter['number']}: {chapter['title']}")

        images = self.extract_images_from_chapter(chapter['url'])
        if not images:
            logger.warning(f"No images found for chapter {chapter['number']}")
            return False

        # Filter out URLs that don't exist (optional)
        if self.validate_urls:
            logger.info(f"Validating {len(images)} potential image URLs...")
            valid_images = self.validate_image_urls(images, referer=chapter['url'], max_workers=10)
        else:
            logger.info(f"Skipping URL validation; attempting downloads for {len(images)} images")
            valid_images = images

        if not valid_images:
            logger.warning(f"No valid images found for chapter {chapter['number']}")
            return False

        logger.info(f"Found {len(valid_images)} valid images for chapter {chapter['number']}")

        # Download images concurrently
        logger.info(f"Downloading {len(valid_images)} images with concurrency={self.max_workers}")
        indices_and_urls = list(enumerate(valid_images, 1))
        success_count = 0
        workers = max(1, min(self.max_workers, len(indices_and_urls)))
        if self.use_playwright:
            workers = min(workers, 2)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(self._download_one, iu, chapter_dir, chapter['url']) for iu in indices_and_urls]
            for fut in as_completed(futures):
                if fut.result():
                    success_count += 1
                if self.interrupted:
                    logger.info(f"Download interrupted after {success_count} images")
                    break

        logger.info(f"Downloaded {success_count}/{len(valid_images)} images for chapter {chapter['number']}")
        return success_count > 0

    def download_manhwa(self, manhwa_info: Dict[str, str], delay: float = 1.0) -> bool:
        """Download all chapters of a manhwa"""
        title = self.sanitize_filename(manhwa_info['title'])
        logger.info(f"Starting download for: {title}")

        chapters = self.extract_chapters(manhwa_info['mainUrl'])
        if not chapters:
            logger.error(f"No chapters found for {title}")
            return False

        success_count = 0
        for chapter in chapters:
            if self.download_chapter(title, chapter, delay):
                success_count += 1

            # Add delay between chapters
            time.sleep(delay)

        logger.info(f"Completed {title}: {success_count}/{len(chapters)} chapters downloaded")
        return success_count > 0

    def download_all(self, manhwa_list: List[Dict[str, str]], delay: float = 1.0) -> None:
        """Download all manhwa in the list"""
        logger.info(f"Starting download of {len(manhwa_list)} manhwa series")

        # Set up signal handler for graceful interruption
        signal.signal(signal.SIGINT, self.signal_handler)

        for manhwa_info in manhwa_list:
            if self.interrupted:
                logger.info("Download interrupted by user")
                break

            try:
                self.download_manhwa(manhwa_info, delay)
                logger.info(f"Completed: {manhwa_info['title']}")
            except Exception as e:
                logger.error(f"Error downloading {manhwa_info['title']}: {e}")
        # Clean up Playwright if it was used
        self._close_playwright()

def main():
    # Manhwa list from user
    manhwa_list = [
        {
            "title": "Only You",
            "koreanTitle": "그저, 그녀",
            "mainUrl": "https://manhwaread.com/manhwa/only-you/"
        },
        {
            "title": "Magnetic Pull",
            "koreanTitle": "여자 사람 친구",
            "mainUrl": "https://manhwaread.com/manhwa/magnetic-pull/"
        },
        {
            "title": "CREAMPIE",
            "koreanTitle": "크림파이",
            "mainUrl": "https://manhwaread.com/manhwa/creampie/"
        },
        {
            "title": "Attraction Eventualis",
            "koreanTitle": "미필적 꼴림",
            "mainUrl": "https://manhwaread.com/manhwa/attraction-eventualis/"
        }
    ]

    parser = argparse.ArgumentParser(description='Download manhwa chapters and images')
    parser.add_argument('--delay', type=float, default=1.0, help='Delay between downloads (seconds)')
    parser.add_argument('--download-dir', default='manhwa_downloads', help='Download directory')
    parser.add_argument('--list-only', action='store_true', help='Only list chapters, don\'t download')
    parser.add_argument('--use-playwright', action='store_true', help='Use Playwright (headless browser) to render pages for image extraction')
    parser.add_argument('--pw-wait', type=float, default=3.0, help='Extra wait in seconds after Playwright loads a page')
    parser.add_argument('--validate-urls', action='store_true', help='Validate image URLs (HEAD/GET) before downloading. May be slow; off by default')
    parser.add_argument('--max-workers', type=int, default=6, help='Max concurrent workers for validation and downloads')

    args = parser.parse_args()

    scraper = ManhwaScraper(download_dir=args.download_dir, use_playwright=args.use_playwright, playwright_wait=args.pw_wait, validate_urls=args.validate_urls, max_workers=args.max_workers)

    if args.list_only:
        # Just list chapters for each manhwa
        for manhwa_info in manhwa_list:
            title = manhwa_info['title']
            logger.info(f"=== {title} ===")
            chapters = scraper.extract_chapters(manhwa_info['mainUrl'])
            for chapter in chapters:
                logger.info(f"  {chapter['number']}: {chapter['title']}")
            logger.info(f"Total: {len(chapters)} chapters\n")
    else:
        # Download all
        scraper.download_all(manhwa_list, delay=args.delay)
        logger.info("Download completed!")

if __name__ == "__main__":
    main()
