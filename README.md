# trawl

Unified CLI scraper for Islamic digital libraries.

Search, read, and download books from [ABLibrary](https://ablibrary.net) and [Eshia](https://lib.eshia.ir).

## Install

```bash
pip install trawl
```

## Usage

```bash
# Search across both libraries
trawl search --source both "sahih"

# Get book details
trawl book --source ablib 12345

# Read book pages
trawl read --source eshia 6789 1-10

# Show table of contents
trawl toc --source ablib 12345

# Download book images (eshia)
trawl download 6789 --volume 1 --pages 1-20

# Look up a hadith narrator
trawl narrator "Bukhari"

# Output as JSON
trawl --json search "fiqh"
```

## License

MIT
