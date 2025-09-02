# Bluesky Firehose Archiver

A Python library for collecting and archiving posts from the Bluesky social network using the [Jetstream API](https://github.com/bluesky-social/jetstream). This tool connects to Bluesky's firehose and saves posts in an organized file structure.

## Features

- Connects to Bluesky's Jetstream websocket API
- Three archiving modes:
  - Posts only (default)
  - All records (posts, likes, follows, etc.)
  - Non-posts only (everything except posts)
- Archives data in JSONL format, organized by date and hour
- Optional real-time post text streaming to stdout
- Automatic reconnection on connection loss
- Efficient batch processing and disk operations
- Debug mode for detailed logging
- Optional handle resolution (disabled by default)
- Playback support from specific timestamps

## Installation

### Manual Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/bluesky-firehose-archiver.git
```

2. Navigate to the project directory:
```bash
cd bluesky-firehose-archiver
```

3. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

4. Install dependencies:
```bash
pip install -r requirements.txt
```

### Docker Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/bluesky-firehose-archiver.git
cd bluesky-firehose-archiver
```

2. Build and run with Docker Compose:
```bash
# Build the image
docker-compose build

# Run bluesky-posts and bluesky-non-posts as separate containers
docker-compose up 

# Or run in the background
docker-compose up -d
```

## Usage

### Command Line Interface

The archiver supports three distinct modes of operation:

1. **Posts Only** (default):
```bash
python src/main.py
```
- Archives only posts (`app.bsky.feed.post` records)
- Saves to `data/` directory
- Files named `posts_YYYYMMDD_HH.jsonl`

2. **All Records**:
```bash
python src/main.py --archive-all
```
- Archives all record types (posts, likes, follows, etc.)
- Saves to `data_everything/` directory
- Files named `records_YYYYMMDD_HH.jsonl`
- Preserves complete record structure

3. **Non-Posts Only**:
```bash
python src/main.py --archive-non-posts
```
- Archives everything except posts
- Saves to `data_non_posts/` directory
- Files named `records_YYYYMMDD_HH.jsonl`
- Useful for collecting only interactions and profile updates

Note: The `--archive-all` and `--archive-non-posts` modes cannot be used simultaneously.

#### Additional Options

```bash
python src/main.py [options]

Options:
  --username         Bluesky username (optional)
  --password         Bluesky password (optional)
  --debug           Enable debug output
  --stream          Stream post text to stdout in real-time
  --measure-rate    Track and display posts per minute rate
  --get-handles     Resolve handles while archiving (not recommended)
  --cursor          Unix microseconds timestamp to start playback from
```

### Library Usage

You can use the archiver in your Python code:

```python
from archiver import BlueskyArchiver
import asyncio

async def main():
    # Initialize with desired options
    archiver = BlueskyArchiver(
        debug=True,           # Enable debug logging
        stream=True,          # Stream posts to stdout
        measure_rate=True,    # Show collection rate
        archive_all=False,    # Default: posts only
        get_handles=False     # Don't resolve handles
    )
    
    try:
        # Start archiving
        await archiver.archive_posts()
    finally:
        # Ensure clean shutdown
        archiver.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

## Data Storage

Records are saved in JSONL (JSON Lines) format, organized by date and hour in different directories based on the archiving mode:

```
data/                      # Posts only mode (default)
  └── YYYY-MM/
      └── DD/
          └── posts_YYYYMMDD_HH.jsonl

data_everything/          # Archive all mode
  └── YYYY-MM/
      └── DD/
          └── records_YYYYMMDD_HH.jsonl

data_non_posts/          # Non-posts mode
  └── YYYY-MM/
      └── DD/
          └── records_YYYYMMDD_HH.jsonl
```

### Record Format

1. **Posts Only Mode** (default):
```json
{
    "handle": "user.bsky.social",
    "record": {
        "text": "Post content",
        "createdAt": "2024-03-15T01:23:45.678Z",
        ...
    },
    "rkey": "unique-record-key",
    "did": "did:plc:abcd...",
    "time_us": 1234567890
}
```

2. **Archive All & Non-Posts Modes**:
```json
{
    "did": "did:plc:abcd...",
    "time_us": 1234567890,
    "kind": "commit",
    "commit": {
        "rev": "...",
        "operation": "create",
        "collection": "app.bsky.feed.like",  // or other collection types
        "rkey": "...",
        "record": { ... }
    }
}
```

### Playback Support

You can start archiving from a specific point in time using the cursor functionality:

```bash
python src/main.py --cursor 1725911162329308
```

The cursor should be a Unix timestamp in microseconds. Playback will start from the specified time and continue to real-time. You can find timestamps in the saved records' `time_us` field.

## Project Structure

```
├── src/
│   ├── main.py           # Entry point and CLI interface
│   └── archiver.py       # Core archiving logic
├── data/                 # Archived posts storage
├── requirements.txt      # Project dependencies
└── README.md            # This file
```

## License

MIT License
