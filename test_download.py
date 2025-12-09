#!/usr/bin/env python3
"""
Test script for Manhwa Scraper - Downloads just first chapter
"""

import sys
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from manhwa_scraper import ManhwaScraper

def test_download():
    """Test downloading just the first chapter"""
    print("Testing Manhwa Scraper Download...")

    # Create test instance
    scraper = ManhwaScraper(download_dir="test_downloads")

    # Test with just one chapter from Only You
    test_manhwa = {
        "title": "Only You",
        "koreanTitle": "그저, 그녀",
        "mainUrl": "https://manhwaread.com/manhwa/only-you/"
    }

    print(f"Testing download for: {test_manhwa['title']}")

    # Get chapters
    chapters = scraper.extract_chapters(test_manhwa['mainUrl'])
    if not chapters:
        print("No chapters found!")
        return

    print(f"Found {len(chapters)} chapters")

    # Download only the first chapter
    first_chapter = chapters[0]
    print(f"Downloading chapter {first_chapter['number']}: {first_chapter['title']}")

    title = "Only_You_Test"
    success = scraper.download_chapter(title, first_chapter, delay=0.5)

    if success:
        print("✅ Chapter download completed successfully!")
    else:
        print("❌ Chapter download failed")

if __name__ == "__main__":
    test_download()
