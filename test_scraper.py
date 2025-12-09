#!/usr/bin/env python3
"""
Test script for Manhwa Scraper
"""

import sys
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from manhwa_scraper import ManhwaScraper

def test_scraper():
    """Test the scraper functionality"""
    print("Testing Manhwa Scraper...")

    # Create test instance
    scraper = ManhwaScraper(download_dir="test_downloads")

    # Test manhwa list (using one from the original list)
    test_manhwa = {
        "title": "Only You",
        "koreanTitle": "그저, 그녀",
        "mainUrl": "https://manhwaread.com/manhwa/only-you/"
    }

    print(f"Testing with: {test_manhwa['title']}")

    # Test chapter extraction
    print("Extracting chapters...")
    chapters = scraper.extract_chapters(test_manhwa['mainUrl'])

    if chapters:
        print(f"✓ Found {len(chapters)} chapters")
        print("First few chapters:")
        for chapter in chapters[:3]:
            print(f"  - Chapter {chapter['number']}: {chapter['title']}")
        if len(chapters) > 3:
            print(f"  ... and {len(chapters) - 3} more chapters")
    else:
        print("✗ No chapters found")
        return False

    # Test image extraction (only for first chapter)
    if chapters:
        print(f"\nTesting image extraction for Chapter {chapters[0]['number']}...")
        images = scraper.extract_images_from_chapter(chapters[0]['url'])

        if images:
            print(f"✓ Found {len(images)} images")
            print("First few image URLs:")
            for i, img_url in enumerate(images[:3], 1):
                print(f"  {i}. {img_url}")
            if len(images) > 3:
                print(f"  ... and {len(images) - 3} more images")
        else:
            print("✗ No images found")

    print("\nTest completed!")
    return True

if __name__ == "__main__":
    test_scraper()
