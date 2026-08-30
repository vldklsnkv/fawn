# Fawn

Fawn is a local-first, portable library for saved links, images, personal notes, and transcripts. It turns each saved item into an Obsidian-compatible Markdown note while keeping a disposable SQLite index for fast search.

The code repository and the personal library are intentionally separate. The checkout can remain public and easy to update, while the user's links, comments, media, and transcripts stay in an external folder chosen at runtime.

## Features

- Save a URL together with the user's original comment.
- Create one readable Markdown note per item.
- Extract page titles, descriptions, and safe raster cover images.
- Search Russian and English content through a local SQLite FTS5 index.
- Rebuild the complete index from Markdown at any time.
- Export the library as portable JSONL.
- Receive links through a paired Telegram chat without storing the bot token in the repository.
- Prepare temporary audio from supported videos for local transcription.

Markdown notes and files in `Assets/` are the source of truth. `System/fawn.sqlite` is only a derived search index and does not need to be backed up.

## Requirements

Fawn requires Python 3.9 or newer and has no mandatory third-party Python dependencies. The Telegram token workflow uses macOS Keychain. Video preparation additionally requires `yt-dlp` and `ffmpeg` to be available on the machine.

Install the CLI from a checkout:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
fawn --help
```

## Quick start

```sh
fawn --library "$HOME/Documents/Fawn Library" add \
  "https://example.com" --comment "Reference for a future project"
fawn --library "$HOME/Documents/Fawn Library" process
fawn --library "$HOME/Documents/Fawn Library" pending
fawn --library "$HOME/Documents/Fawn Library" search "future project"
```

Library location is resolved in this order:

1. the global `--library PATH` option;
2. the `FAWN_LIBRARY` environment variable;
3. `~/Documents/Fawn Library`.

Open that external folder as an Obsidian vault if desired. Runtime data is never expected to live inside the source checkout.

## Telegram inbox

Store the Telegram bot token through hidden input in macOS Keychain rather than an `.env` file:

```sh
fawn --library "$HOME/Documents/Fawn Library" telegram set-token
fawn --library "$HOME/Documents/Fawn Library" telegram pair
fawn --library "$HOME/Documents/Fawn Library" telegram run
```

Pairing restricts ingestion to one approved chat. The bot accepts a link with an optional caption, or a comment sent as a reply to the original link message. Network and provider errors are redacted so the token is not exposed in logs.

## Video and transcripts

Fawn does not permanently keep source video. It can extract one temporary audio track for local transcription, then attach the completed transcript to the item's Markdown note:

```sh
fawn --library "$HOME/Documents/Fawn Library" video prepare ITEM_ID
# Transcribe the returned file locally.
fawn --library "$HOME/Documents/Fawn Library" enrich ITEM_ID \
  --transcript-file /absolute/path/transcript.txt
fawn --library "$HOME/Documents/Fawn Library" video cleanup ITEM_ID
```

Fawn does not bypass DRM, private accounts, or authentication. Cleanup refuses to remove temporary audio until a transcript has been written successfully.

## Portability and safety

To move a library, copy its external folder and rebuild the index:

```sh
fawn --library "$HOME/Documents/Fawn Library" rebuild
fawn --library "$HOME/Documents/Fawn Library" export
```

The exporter writes `Exports/fawn.jsonl`. Personal comments are preserved verbatim and remain separate from generated summaries, categories, and tags. Saved pages and transcripts are treated as untrusted content, and private-network URLs are rejected.

## Development

```sh
python3 -m unittest discover -s tests -v
```

Fawn is released under the MIT License. See [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md).
