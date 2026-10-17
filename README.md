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

## ChatGPT Space synchronization

The `space` commands prepare incremental changes for a local Codex automation
with the Pages plugin connected. They do not call an undocumented API, store
credentials, or edit the source vault. The automation uploads media, applies
guarded Page edits, verifies the saved records, and then acknowledges its plan.

Space dates use compact Russian labels such as `15 окт. 26`. Categories and tags
use English labels and stable hashtags (`#ai_and_technology`, `#ai_coding`). The
agent can store `space_sync.classification(record)` as `fawn_classification`
block metadata to retain the original labels in `original_categories` and
`original_tags`, together with ISO dates for future filters. Unknown non-English
labels require translation before publication.
Duplicate matching also recognizes stable Notion page, YouTube video, Telegram
post, and Threads post IDs across URL variants. Existing URL checkpoints remain
compatible; conflicting saved identities require review before publishing.

Keep the private checkpoint, plans, upload receipts, and the Space-specific
runbook outside this public checkout. The checkpoint maps source fingerprints,
canonical IDs (including merged duplicates), topic Pages, and confirmed images.

```sh
fawn --library /path/to/library space --state /private/runtime/state.json scan \
  --limit 20 --out /private/runtime/plan.json
# The Pages-connected agent applies and reads back the remote changes.
fawn --library /path/to/library space --state /private/runtime/state.json ack \
  --plan /private/runtime/plan.json --receipt /private/runtime/receipt.json
```

An empty scan does not update the checkpoint or the Page's update date. An
acknowledgement requires verified records, all image references, and the root's
counts and date. Changes arriving after a plan was made remain pending for the
next run. Source removals are reported without deleting cloud records. Retain
upload-owning Pages: removing them can make their images inaccessible elsewhere.

Local scheduled runs require the computer and desktop app to be running.

### Cloud receiver

`cloud/webhook.ts` provides a separate Supabase Telegram inbox. It verifies the
webhook secret and paired private chat, persists original updates before
acknowledging them, and suppresses duplicate deliveries. RLS and revoked client
grants keep the queue private. The Telegram bot token stays in Keychain; webhook
responses provide acknowledgements without deploying that token.

`python3 -m fawn.cloud --help` exposes one-time setup, receiver verification,
webhook status, and cutover commands. Keep its private `--runtime` outside this
checkout and the original library. Verify the receiver before switching, stop
the local Telegram service, and retain pending updates during cutover.

A hosted Page controller can process this queue without a Mac. Its persistent
instructions, baseline catalog, code package, processing ledger, and activation
gate live in the destination Space. Configure and verify that controller
separately; deploying the receiver alone does not activate synchronization.
Confirm the external provider before redirecting personal messages. Keep the
original catalog in the Space rather than copying it into the queue database.
Cloud additions do not automatically create local Markdown copies. Inaccessible
source images or transcripts must remain pending with an explicit reason.

Receiver checks: `deno test cloud/webhook_test.ts`.

## Development

```sh
python3 -m unittest discover -s tests -v
```

Fawn is released under the MIT License. See [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md).
