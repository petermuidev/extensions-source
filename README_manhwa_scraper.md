# Manhwa Scraper

A Python script to download all chapters and images from manhwa series on ManhwaRead.

## Features

- Downloads all chapters from multiple manhwa series
- Extracts and downloads all images from each chapter
- Organizes downloads in structured folders
- Handles errors and retries gracefully
- Configurable download delays to be respectful to servers

## Installation

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Download all manhwa
```bash
python manhwa_scraper.py
```

### List chapters only (without downloading)
```bash
python manhwa_scraper.py --list-only
```

### Custom download directory and delay
```bash
python manhwa_scraper.py --download-dir my_manhwa --delay 2.0
```

## Command Line Options

- `--delay`: Delay between downloads in seconds (default: 1.0)
- `--download-dir`: Directory to save downloads (default: manhwa_downloads)
- `--list-only`: Only list chapters, don't download images

## Output Structure

```
manhwa_downloads/
├── Only_You/
│   ├── Chapter_00_Prologue/
│   │   ├── page_001.jpg
│   │   ├── page_002.jpg
│   │   └── ...
│   ├── Chapter_01/
│   │   ├── page_001.jpg
│   │   └── ...
│   └── ...
├── Magnetic_Pull/
│   └── ...
└── ...
```

## Manhwa List

The script currently downloads these manhwa:

1. **Only You** (그저, 그녀)
2. **Magnetic Pull** (여자 사람 친구)
3. **CREAMPIE** (크림파이)
4. **Attraction Eventualis** (미필적 꼴림)

## Notes

- The script respects server limits by adding delays between requests
- Images are downloaded in JPG format with zero-padded numbering
- Existing files are skipped to avoid re-downloading
- A log file (`manhwa_scraper.log`) is created with detailed progress information

## Troubleshooting

- If images aren't downloading, the site might be using dynamic loading or blob URLs
- Check the log file for detailed error messages
- Some manhwa might have different page structures that require script updates
