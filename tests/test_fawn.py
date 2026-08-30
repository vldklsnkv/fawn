import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch
from urllib.error import HTTPError

from fawn.extractor import PageData, download_image
from fawn.cli import main, resolve_library_root
from fawn.markdown import read as read_note
from fawn.markdown import render
from fawn.model import Item
from fawn.store import Store
from fawn.telegram import TelegramClient, process_one_update, set_token
from fawn.utils import ensure_public_http_url, extract_urls, normalize_url, platform_for, without_urls
from fawn.video import cleanup as cleanup_video
from fawn.video import MAX_AUDIO_BYTES
from fawn.video import prepare as prepare_video


class UtilsTests(unittest.TestCase):
    def test_library_root_precedence_is_cli_then_environment_then_default(self):
        home = Path("/safe-home")
        self.assertEqual(
            resolve_library_root("/explicit", {"FAWN_LIBRARY": "/environment"}, home),
            Path("/explicit"),
        )
        self.assertEqual(resolve_library_root(None, {"FAWN_LIBRARY": "/environment"}, home), Path("/environment"))
        self.assertEqual(
            resolve_library_root(None, {}, home),
            Path("/safe-home/Documents/Fawn Library"),
        )

    @patch("fawn.video.prepare", return_value=Path("/external-library/System/runtime/id/source.m4a"))
    @patch("fawn.cli.Store")
    def test_cli_passes_resolved_library_to_video(self, store_mock, _):
        main(["--library", "/external-library", "video", "prepare", "id"])
        store_mock.assert_called_once_with(Path("/external-library"))

    def test_normalize_removes_tracking_and_fragment(self):
        value = normalize_url("https://Example.com/item?utm_source=tg&id=7#details")
        self.assertEqual(value, "https://example.com/item?id=7")

    def test_normalize_rejects_markdown_breakout(self):
        with self.assertRaises(ValueError):
            normalize_url("https://example.com/> bad")

    def test_normalize_deduplicates_twitter_and_x_hosts(self):
        self.assertEqual(
            normalize_url("http://www.twitter.com/user/status/7?utm_source=share"),
            "https://x.com/user/status/7",
        )

    def test_extract_urls_and_comment(self):
        text = "Вишлист\n\nhttps://example.com/a?utm_source=tg\nи https://x.com/a/status/1"
        self.assertEqual(len(extract_urls(text)), 2)
        self.assertEqual(without_urls(text), "Вишлист\n\n\nи")

    def test_platform_detection(self):
        self.assertEqual(platform_for("https://www.instagram.com/p/abc/"), "instagram")
        self.assertEqual(platform_for("https://t.me/channel/12"), "telegram")
        self.assertEqual(platform_for("https://example.com/"), "web")

    @patch("fawn.utils.socket.getaddrinfo")
    def test_private_network_is_blocked(self, address_mock):
        address_mock.return_value = [(2, 1, 6, "", ("127.0.0.1", 80))]
        with self.assertRaisesRegex(ValueError, "Приватные"):
            ensure_public_http_url("http://internal.example/")


class TelegramTests(unittest.TestCase):
    @patch("fawn.telegram.pair")
    def test_cli_passes_resolved_library_to_telegram(self, pair_mock):
        main(["--library", "/external-library", "telegram", "pair"])
        pair_mock.assert_called_once_with(Path("/external-library"))

    @patch("fawn.telegram.subprocess.run")
    @patch("fawn.telegram.getpass.getpass", return_value="123456:SUPER_SECRET")
    def test_keychain_command_does_not_contain_token(self, _, run_mock):
        set_token()
        command = run_mock.call_args.args[0]
        self.assertNotIn("123456:SUPER_SECRET", command)
        self.assertEqual(run_mock.call_args.kwargs["input"], "123456:SUPER_SECRET\n")

    @patch("fawn.telegram.urlopen")
    def test_network_error_does_not_reveal_token(self, open_mock):
        token = "123456:SUPER_SECRET"
        open_mock.side_effect = HTTPError(
            f"https://api.telegram.org/bot{token}/getUpdates",
            500,
            "failure",
            {},
            None,
        )
        with self.assertRaises(RuntimeError) as raised:
            TelegramClient(token).call("getUpdates")
        self.assertNotIn(token, str(raised.exception))

    @patch("fawn.store.ensure_public_http_url")
    def test_edited_message_is_saved_and_checkpointed(self, _):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            store = Store(root)

            class RecordingClient:
                def __init__(self):
                    self.sent = []

                def send(self, chat_id, text):
                    self.sent.append((chat_id, text))

            client = RecordingClient()
            update = {
                "update_id": 43,
                "edited_message": {
                    "chat": {"id": 7},
                    "text": "https://example.com/edited\nКомментарий после редактирования",
                },
            }
            state_path = root / "System" / "telegram-state.json"
            try:
                offset = process_one_update(client, store, {7}, update, 0, state_path)
                self.assertEqual(offset, 44)
                self.assertEqual(store.count(), 1)
                self.assertEqual(json.loads(state_path.read_text())["offset"], 44)
                self.assertEqual(client.sent[0][0], 7)
                self.assertIn("Сохранено: 1", client.sent[0][1])
            finally:
                store.close()

    @patch("fawn.store.ensure_public_http_url")
    def test_failed_confirmation_does_not_checkpoint_update(self, _):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            store = Store(root)

            class FailingClient:
                def send(self, chat_id, text):
                    raise RuntimeError("offline")

            update = {
                "update_id": 42,
                "message": {
                    "chat": {"id": 7},
                    "text": "https://example.com/item\nМой комментарий",
                },
            }
            state_path = root / "System" / "telegram-state.json"
            try:
                with self.assertRaisesRegex(RuntimeError, "offline"):
                    process_one_update(FailingClient(), store, {7}, update, 0, state_path)
                self.assertFalse(state_path.exists())
                self.assertEqual(store.count(), 1)
            finally:
                store.close()


class MarkdownTests(unittest.TestCase):
    def test_external_metadata_cannot_create_remote_embed(self):
        item = Item(
            id="20260829-test",
            url="https://example.com/",
            platform="web",
            status="extracted",
            created_at="2026-08-29T00:00:00+04:00",
            updated_at="2026-08-29T00:00:00+04:00",
            title="![remote](https://evil.example/title.png)",
            summary="<iframe src=https://evil.example></iframe>",
        )
        note = render(item)
        body = note.split("\n---\n", 1)[1]
        self.assertNotIn("![remote]", body)
        self.assertNotIn("<iframe", body)

    def test_external_metadata_neutralizes_all_markdown_constructs(self):
        item = Item(
            id="20260829-test",
            url="https://example.com/",
            platform="web",
            status="extracted",
            created_at="2026-08-29T00:00:00+04:00",
            updated_at="2026-08-29T00:00:00+04:00",
            title="# Heading `code` **bold** [link](https://evil.example)",
            summary="```md --- #tag > quote ![[Private]] _italic_",
        )
        body = render(item).split("\n---\n", 1)[1]
        for active_syntax in ("# Heading", "`code`", "**bold**", "[link](", "```", "#tag", "![[", "_italic_"):
            self.assertNotIn(active_syntax, body)
        self.assertIn("&#35; Heading", body)

    def test_transcript_is_safe_and_round_trips(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "note.md"
            item = Item(
                id="20260829-test",
                url="https://example.com/",
                platform="web",
                status="ready",
                created_at="2026-08-29T00:00:00+04:00",
                updated_at="2026-08-29T00:00:00+04:00",
                transcript="<iframe src=https://evil.example></iframe>\n![[Private note]]",
            )
            path.write_text(render(item), encoding="utf-8")
            body = path.read_text(encoding="utf-8").split("\n---\n", 1)[1]
            self.assertNotIn("<iframe src=", body)
            self.assertEqual(read_note(path).transcript, item.transcript)

    def test_transcript_headings_do_not_break_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "note.md"
            item = Item(
                id="20260829-test",
                url="https://example.com/",
                platform="web",
                status="ready",
                created_at="2026-08-29T00:00:00+04:00",
                updated_at="2026-08-29T00:00:00+04:00",
                transcript="Вступление\n## Глава\nПродолжение",
            )
            path.write_text(render(item), encoding="utf-8")
            self.assertEqual(read_note(path).transcript, item.transcript)

    def test_obsidian_block_lists_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "note.md"
            path.write_text(
                """---
id: 20260829-test
url: https://example.com/
platform: web
status: ready
created_at: 2026-08-29T00:00:00+04:00
updated_at: 2026-08-29T00:00:00+04:00
title: Example
author: Studio
user_comment: My note
summary: Summary
tags:
  - design
  - reference
categories:
  - Wishlist
media_type: link
assets:
  - Assets/20260829-test/cover.jpg
---

# Example

## Транскрипт

_Транскрипта пока нет._
""",
                encoding="utf-8",
            )
            item = read_note(path)
            self.assertEqual(item.tags, ["design", "reference"])
            self.assertEqual(item.categories, ["Wishlist"])
            self.assertEqual(item.assets, ["Assets/20260829-test/cover.jpg"])
            path.write_text(render(item), encoding="utf-8")
            self.assertEqual(read_note(path).tags, item.tags)

    def test_obsidian_multiline_comment_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "note.md"
            path.write_text(
                """---
id: 20260829-test
url: https://example.com/
platform: web
status: ready
created_at: 2026-08-29T00:00:00+04:00
updated_at: 2026-08-29T00:00:00+04:00
title: Example
author: ""
user_comment: |-
  Первая строка

  Вторая строка
summary: ""
tags: []
categories: []
media_type: link
assets: []
---

# Example

## Транскрипт

_Транскрипта пока нет._
""",
                encoding="utf-8",
            )
            self.assertEqual(read_note(path).user_comment, "Первая строка\n\nВторая строка")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.public_url_patch = patch("fawn.store.ensure_public_http_url")
        self.public_url_patch.start()
        self.store = Store(self.root)

    def tearDown(self):
        self.store.close()
        self.public_url_patch.stop()
        self.temp.cleanup()

    def test_add_duplicate_appends_comment(self):
        item, path, created = self.store.add("https://example.com/product", "Вишлист")
        self.assertTrue(created)
        self.assertTrue(path.exists())
        _, second_path, created_again = self.store.add("https://example.com/product", "Для кухни")
        self.assertFalse(created_again)
        self.assertEqual(path, second_path)
        loaded = read_note(path)
        self.assertEqual(loaded.user_comment, "Вишлист\n\nДля кухни")

    def test_comment_internal_whitespace_is_verbatim(self):
        comment = "Первая строка\n\n  Вторая строка  "
        _, path, _ = self.store.add("https://example.com/note", comment)
        self.assertEqual(read_note(path).user_comment, comment)

    def test_search_and_rebuild_from_markdown(self):
        _, path, _ = self.store.add("https://example.com/product", "Скандинавская лампа в вишлист")
        found = self.store.search("лампа")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["path"], path.relative_to(self.root).as_posix())
        self.assertEqual(self.store.rebuild(), 1)
        self.assertEqual(len(self.store.search("вишлист")), 1)

    @patch("fawn.store.fetch")
    def test_process_and_enrich(self, fetch_mock):
        fetch_mock.return_value = PageData(
            title="Nordic Lamp",
            description="A warm table lamp",
            author="Studio",
        )
        item, _, _ = self.store.add("https://example.com/lamp", "Вишлист")
        results = self.store.process()
        self.assertEqual(len(results), 1)
        processed, path, error = results[0]
        self.assertIsNone(error)
        self.assertEqual(processed.status, "extracted")
        self.assertEqual(path.parent, self.root / "Library")
        self.assertEqual(len(self.store.pending()), 1)
        enriched, enriched_path = self.store.enrich(
            item.id,
            title="Скандинавская настольная лампа",
            summary="Тёплая настольная лампа",
            tags=["свет"],
            categories=["Wishlist"],
        )
        self.assertEqual(enriched.status, "ready")
        self.assertEqual(enriched.title, "Скандинавская настольная лампа")
        self.assertEqual(
            enriched_path.name,
            f"{item.id} - Скандинавская настольная лампа.md",
        )
        self.assertFalse(path.exists())
        self.assertEqual(len(self.store.search("настольная")), 1)

    def test_export_jsonl(self):
        self.store.add("https://example.com/a", "Сохранить")
        path, count = self.store.export_jsonl()
        self.assertEqual(count, 1)
        self.assertTrue(path.exists())
        self.assertIn('"user_comment": "Сохранить"', path.read_text(encoding="utf-8"))

    @patch("fawn.video.subprocess.run")
    @patch("fawn.video.ensure_public_http_url")
    def test_video_is_kept_until_transcript_exists(self, _, run_mock):
        item, _, _ = self.store.add("https://youtube.com/watch?v=abc", "Посмотреть")

        def create_audio(command, **kwargs):
            self.assertIn("--ignore-config", command)
            self.assertEqual(
                command[command.index("--match-filter") + 1],
                "duration<=?1800 & !is_live",
            )
            output = Path(command[command.index("--output") + 1].replace("%(ext)s", "m4a"))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"audio")
            return CompletedProcess(command, 0, "", "")

        run_mock.side_effect = create_audio
        audio = prepare_video(self.store, item.id)
        self.assertTrue(audio.exists())
        with self.assertRaisesRegex(ValueError, "транскрипта"):
            cleanup_video(self.store, item.id)
        enriched, _ = self.store.enrich(item.id, transcript="Короткий транскрипт")
        self.assertEqual(enriched.media_type, "video")
        self.assertTrue(cleanup_video(self.store, item.id))
        self.assertFalse(audio.exists())

    @patch("fawn.video.subprocess.run")
    @patch("fawn.video.ensure_public_http_url")
    def test_oversized_extracted_audio_is_rejected(self, _, run_mock):
        item, _, _ = self.store.add("https://youtube.com/watch?v=large", "")

        def create_oversized_audio(command, **kwargs):
            output = Path(command[command.index("--output") + 1].replace("%(ext)s", "m4a"))
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("wb") as stream:
                stream.truncate(MAX_AUDIO_BYTES + 1)
            return CompletedProcess(command, 0, "", "")

        run_mock.side_effect = create_oversized_audio
        with self.assertRaisesRegex(RuntimeError, "единственный аудиофайл"):
            prepare_video(self.store, item.id)
        self.assertFalse((self.root / "System" / "runtime" / item.id).exists())

    @patch("fawn.video.subprocess.run")
    @patch("fawn.video.ensure_public_http_url")
    def test_video_recomputes_platform_from_url(self, _, run_mock):
        item, path, _ = self.store.add("https://example.com/not-video", "")
        item.platform = "youtube"
        path.write_text(render(item), encoding="utf-8")
        self.store.rebuild()
        with self.assertRaisesRegex(ValueError, "видеоплатформ"):
            prepare_video(self.store, item.id)
        run_mock.assert_not_called()


class StoreSecurityTests(unittest.TestCase):
    @patch("fawn.utils.socket.getaddrinfo")
    def test_store_rejects_private_destination(self, address_mock):
        address_mock.return_value = [(2, 1, 6, "", ("10.0.0.2", 443))]
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary).resolve())
            try:
                with self.assertRaisesRegex(ValueError, "Приватные"):
                    store.add("https://private.example/item", "")
                self.assertEqual(store.count(), 0)
            finally:
                store.close()


class ImageSecurityTests(unittest.TestCase):
    @patch("fawn.extractor._request_bytes")
    def test_svg_cover_is_rejected_before_asset_write(self, request_mock):
        request_mock.return_value = (
            "https://example.com/cover.jpg",
            "image/svg+xml",
            "utf-8",
            b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        )
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary) / "Assets"
            with self.assertRaisesRegex(ValueError, "безопасным raster"):
                download_image("https://example.com/cover.jpg", assets, "item")
            self.assertFalse(assets.exists())

    @patch("fawn.extractor._request_bytes")
    def test_cover_extension_uses_validated_raster_mime_not_url_suffix(self, request_mock):
        request_mock.return_value = (
            "https://example.com/cover.svg",
            "image/png",
            "utf-8",
            b"\x89PNG\r\n\x1a\nimage-data",
        )
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary) / "Assets"
            stored = download_image("https://example.com/cover.svg", assets, "item")
            self.assertTrue(stored.endswith(".png"))
            self.assertTrue((assets.parent / stored).is_file())


if __name__ == "__main__":
    unittest.main()
