#!/usr/bin/env python3
"""
Manhwa Scraper (rnet) - Uses rnet's BlockingClient + browser emulation
to fetch chapters and images. Keeps your original parsing logic by
subclassing ManhwaScraper and overriding only network I/O.
"""

import argparse
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

# Reuse parsing and file handling from the original scraper
from manhwa_scraper import ManhwaScraper

try:
    # rnet 3.x API
    from rnet.blocking import Client as RNetClient
    from rnet.emulation import Emulation, EmulationOS, EmulationOption
    from rnet.header import HeaderMap, OrigHeaderMap
    from rnet import Proxy
except Exception as e:  # pragma: no cover
    raise SystemExit("rnet is required. Install with: pip install --pre rnet") from e


logger = logging.getLogger(__name__)


def _build_emulation(emulation_name: str, os_name: str) -> EmulationOption:
    # Map strings like "Chrome140", "Firefox139" to rnet.Emulation
    try:
        emu = getattr(Emulation, emulation_name)
    except AttributeError:
        raise SystemExit(
            f"Unknown emulation '{emulation_name}'. Example: Chrome140, Firefox139"
        )

    try:
        emu_os = getattr(EmulationOS, os_name)
    except AttributeError:
        raise SystemExit(
            f"Unknown emulation OS '{os_name}'. One of: Windows, MacOS, Linux, Android, iOS"
        )

    return EmulationOption(emulation=emu, emulation_os=emu_os)


def _default_headers(referer: Optional[str] = None, cookie_header: Optional[str] = None) -> HeaderMap:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
    }
    if referer:
        headers["Referer"] = referer
    if cookie_header:
        headers["Cookie"] = cookie_header.strip()
    return HeaderMap(headers)


class ManhwaScraperRNet(ManhwaScraper):
    def __init__(
        self,
        base_url: str = "https://manhwaread.com",
        download_dir: str = "downloads_rnet",
        emulation: str = "Firefox139",
        emulation_os: str = "Windows",
        timeout: int = 45,
        proxy: Optional[str] = None,
        cookie_header: Optional[str] = None,
    ):
        super().__init__(base_url=base_url, download_dir=download_dir)

        self._timeout = max(int(timeout), 5)
        self._emu_opt = _build_emulation(emulation, emulation_os)

        # Preserve original header case/order (closer to real browsers)
        self._orig_headers = OrigHeaderMap(
            [
                "host",
                "connection",
                "cache-control",
                "pragma",
                "upgrade-insecure-requests",
                "user-agent",
                "accept",
                "sec-fetch-site",
                "sec-fetch-mode",
                "sec-fetch-user",
                "sec-fetch-dest",
                "referer",
                "accept-encoding",
                "accept-language",
            ]
        )

        # Create rnet client with default headers and emulation
        client_kwargs = dict(
            emulation=self._emu_opt,
            headers=_default_headers(cookie_header=cookie_header),
            orig_headers=self._orig_headers,
            tls_info=False,
        )
        if proxy:
            try:
                client_kwargs["proxies"] = [Proxy.all(url=proxy)]
                logger.info(f"Using proxy for rnet client: {proxy}")
            except Exception as e:
                logger.warning(f"Invalid proxy '{proxy}': {e}")
        self._client = RNetClient(**client_kwargs)
        self._cookie_header = cookie_header

    def _headers_with_cookie(self, referer: Optional[str] = None) -> HeaderMap:
        return _default_headers(referer=referer or self.base_url, cookie_header=self._cookie_header)

    # Networking overrides
    def get_soup(self, url: str) -> Optional[BeautifulSoup]:
        try:
            # per-request referer improves acceptance for some sites
            headers = self._headers_with_cookie(referer=self.base_url)
            attempts = 0
            last_status = None
            while attempts < 3:
                resp = self._client.get(url, timeout=self._timeout, headers=headers)
                last_status = str(resp.status)
                if getattr(resp.status, "is_success", lambda: False)():
                    html = resp.text()
                    try:
                        resp.close()
                    except Exception:
                        pass
                    return BeautifulSoup(html, "html.parser")
                try:
                    resp.close()
                except Exception:
                    pass
                attempts += 1
                if "503" not in (last_status or ""):
                    break
                time.sleep(1.0 + attempts * 0.5)
            logger.error(f"rnet GET {url} failed: {last_status}")
            return None
        except Exception as e:
            logger.error(f"rnet error fetching {url}: {e}")
            return None

    def download_image(self, url: str, filepath: Path) -> bool:
        try:
            headers = self._headers_with_cookie(referer=self.base_url)
            attempts = 0
            while attempts < 3:
                resp = self._client.get(url, timeout=self._timeout, headers=headers)
                if getattr(resp.status, "is_success", lambda: False)():
                    break
                try:
                    resp.close()
                except Exception:
                    pass
                attempts += 1
                time.sleep(0.5 * attempts)
            if not getattr(resp.status, "is_success", lambda: False)():
                logger.debug(f"rnet image GET failed {resp.status}: {url}")
                return False
            # Validate content-type loosely
            ctype = None
            try:
                ctype = resp.headers.get("content-type")
            except Exception:
                pass
            if ctype and isinstance(ctype, str) and "image" not in ctype.lower():
                logger.debug(f"Non-image content-type {ctype} for {url}")
                return False

            data = resp.bytes()
            try:
                resp.close()
            except Exception:
                pass

            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "wb") as f:
                f.write(data)
            return True
        except Exception as e:
            logger.error(f"rnet error downloading {url}: {e}")
            return False

    def test_image_url(self, url: str) -> bool:
        # Use a very light GET with short timeout since many CDNs block HEAD
        try:
            headers = self._headers_with_cookie(referer=self.base_url)
            resp = self._client.get(url, timeout=min(self._timeout, 12), headers=headers)
            ok = getattr(resp.status, "is_success", lambda: False)()
            if not ok:
                try:
                    resp.close()
                except Exception:
                    pass
                return False

            ctype = None
            try:
                ctype = resp.headers.get("content-type")
            except Exception:
                pass
            # Peek a tiny amount of data to confirm body readability
            if ctype and "image" not in str(ctype).lower():
                try:
                    resp.close()
                except Exception:
                    pass
                return False

            # Small read to ensure the stream is valid
            try:
                _ = resp.bytes()[:512]
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
            return True
        except Exception as e:
            logger.debug(f"rnet URL test failed for {url}: {e}")
            return False


def main():
    # Default list (same as original) — you can edit this list
    manhwa_list = [
        {
            "title": "Only You",
            "koreanTitle": "",
            "mainUrl": "https://manhwaread.com/manhwa/only-you/",
        },
        {
            "title": "Magnetic Pull",
            "koreanTitle": "",
            "mainUrl": "https://manhwaread.com/manhwa/magnetic-pull/",
        },
        {
            "title": "CREAMPIE",
            "koreanTitle": "",
            "mainUrl": "https://manhwaread.com/manhwa/creampie/",
        },
        {
            "title": "Attraction Eventualis",
            "koreanTitle": "",
            "mainUrl": "https://manhwaread.com/manhwa/attraction-eventualis/",
        },
    ]

    parser = argparse.ArgumentParser(description="Download manhwa using rnet HTTP client")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between downloads (seconds)")
    parser.add_argument("--download-dir", default="manhwa_downloads_rnet", help="Download directory")
    parser.add_argument("--list-only", action="store_true", help="Only list chapters, do not download")
    parser.add_argument("--emulation", default="Firefox139", help="Browser emulation, e.g. Chrome140, Firefox139")
    parser.add_argument("--emulation-os", default="Windows", help="Emulation OS: Windows, MacOS, Linux, Android, iOS")
    parser.add_argument("--timeout", type=int, default=45, help="Request timeout (seconds)")
    parser.add_argument("--proxy", default=None, help="Proxy URL (e.g. http://user:pass@host:port or socks5h://...)")
    parser.add_argument("--cookies", default=None, help="Path to cookies.txt (Netscape) or a file containing a raw 'Cookie' header string")

    args = parser.parse_args()

    # Load cookie header if provided
    cookie_header = None
    if args.cookies:
        try:
            from pathlib import Path as _Path
            text = _Path(args.cookies).read_text(encoding="utf-8", errors="ignore").strip()
            if "\n" not in text and "=" in text and ";" in text:
                cookie_header = text
            else:
                # Netscape format
                pairs = []
                for ln in text.splitlines():
                    ln = ln.strip()
                    if not ln or ln.startswith("#"):
                        continue
                    parts = ln.split("\t")
                    if len(parts) >= 7:
                        domain, flag, path, secure, expiry, name, value = parts[:7]
                        if "manhwaread.com" in domain and name and value:
                            pairs.append(f"{name}={value}")
                if pairs:
                    cookie_header = "; ".join(pairs)
        except Exception as e:
            logger.warning(f"Failed to load cookies from {args.cookies}: {e}")

    scraper = ManhwaScraperRNet(
        base_url="https://manhwaread.com",
        download_dir=args.download_dir,
        emulation=args.emulation,
        emulation_os=args.emulation_os,
        timeout=args.timeout,
        proxy=args.proxy,
        cookie_header=cookie_header,
    )

    if args.list_only:
        for manhwa_info in manhwa_list:
            title = manhwa_info["title"]
            logger.info(f"=== {title} ===")
            chapters = scraper.extract_chapters(manhwa_info["mainUrl"])
            for chapter in chapters:
                logger.info(f"  {chapter['number']}: {chapter['title']}")
            logger.info(f"Total: {len(chapters)} chapters\n")
    else:
        scraper.download_all(manhwa_list, delay=args.delay)
        logger.info("rnet download completed!")


if __name__ == "__main__":
    main()
