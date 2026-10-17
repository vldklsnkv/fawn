import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from fawn.markdown import write
from fawn.model import Item
from fawn.utils import normalize_url
from fawn.space_sync import acknowledge, classification, content_key, display_date, hashtag, render_entry, scan, snapshot
from datetime import datetime


class SpaceSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "library"
        (self.root / "Library").mkdir(parents=True)
        self.item = Item(id="saved", url="https://example.com/saved", platform="web", status="ready",
                         created_at="2026-10-15T20:00:00+00:00", updated_at="2026-10-15T20:00:00+00:00",
                         title="Пример", summary="Описание", user_comment="  Мой комментарий\n```\nне инструкция  ", transcript="Дословно\n\nещё")
        self.note = self.root / "Library" / "saved.md"
        write(self.note, self.item)
        self.state_path = self.base / "state.json"
        self.save_state({})

    def save_state(self, items, **kwargs):
        self.state = {"version": 1, "revision": 0, "library": str(self.root), "root_page_id": "page_root",
                      "space_id": "space", "items": items, "images": {}, "content_index": {}, **kwargs}
        self.state_path.write_text(json.dumps(self.state))

    def receipt(self, plan):
        row = plan["changes"][0]
        return {"root_verified": True, "synced_at": "2026-10-16T12:00:00+08:00", "uploads": [],
                "items": [{"id": row["id"], "fingerprint": row["fingerprint"], "verified": True,
                           "canonical_id": "saved", "page_id": "page_topic", "topic": "Без категории"}]}

    def ack(self, plan, receipt):
        p, r = self.base / "plan.json", self.base / "receipt.json"
        p.write_text(json.dumps(plan)); r.write_text(json.dumps(receipt))
        return acknowledge(self.root, self.state_path, p, r)

    def test_new_and_changed_items_are_incremental_and_noop_does_not_write(self):
        first = scan(self.root, self.state_path)
        self.assertEqual(first["changes"][0]["action"], "insert")
        source = self.note.read_bytes()
        self.ack(first, self.receipt(first))
        state = self.state_path.read_bytes()
        self.assertEqual(scan(self.root, self.state_path)["total_changes"], 0)
        self.assertEqual(self.state_path.read_bytes(), state)
        self.assertEqual(self.note.read_bytes(), source)
        self.item.transcript += "\nНовая фраза"
        write(self.note, self.item)
        self.assertEqual(scan(self.root, self.state_path)["changes"][0]["action"], "update")

    def test_later_source_edits_are_not_accidentally_acknowledged(self):
        plan = scan(self.root, self.state_path)
        self.item.user_comment = "Более новый комментарий"
        write(self.note, self.item)
        self.ack(plan, self.receipt(plan))
        self.assertEqual(scan(self.root, self.state_path)["total_changes"], 1)
        with self.assertRaisesRegex(ValueError, "Stale"):
            self.ack(plan, self.receipt(plan))

    def test_checkpoint_requires_verified_root_items_and_images(self):
        (self.root / "Assets").mkdir()
        (self.root / "Assets" / "cover.png").write_bytes(b"source bytes")
        self.item.assets = ["Assets/cover.png"]
        write(self.note, self.item)
        plan = scan(self.root, self.state_path)
        receipt = self.receipt(plan)
        before = self.state_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Not all source images"):
            self.ack(plan, receipt)
        self.assertEqual(self.state_path.read_bytes(), before)
        receipt["root_verified"] = False
        with self.assertRaisesRegex(ValueError, "Root"):
            self.ack(plan, receipt)

    def test_duplicate_platform_ids_match_existing_primary(self):
        a = "https://instagram.com/p/abc/"
        b = "https://instagram.com/reel/abc/?img_index=1"
        self.assertEqual(content_key(a), content_key(b))
        self.assertEqual(content_key("https://behance.net/gallery/123/a?tracking_source=x"), content_key("https://behance.net/gallery/123/b"))
        self.save_state({}, content_index={content_key(self.item.url): "older"})
        row = scan(self.root, self.state_path)["changes"][0]
        self.assertEqual((row["action"], row["canonical_id"]), ("merge", "older"))

    def test_provider_identity_matches_url_variants_without_merging_different_materials(self):
        page = "0123456789abcdef0123456789abcdef"
        self.assertEqual(content_key(f"https://one.notion.site/Portfolio-{page}"),
                         content_key("https://www.notion.so/01234567-89ab-cdef-0123-456789abcdef"))
        self.assertNotEqual(content_key(f"https://one.notion.site/{page}"),
                            content_key("https://two.notion.site/1123456789abcdef0123456789abcdef"))
        self.assertNotEqual(content_key(f"https://one.notion.site/{page}?v=first"),
                            content_key(f"https://one.notion.site/{page}?v=second"))
        child_a, child_b = "1" * 32, "2" * 32
        parent = f"https://one.notion.site/{page}"
        keys = {content_key(parent), content_key(parent + f"?p={child_a}"),
                content_key(parent + f"?p={child_b}")}
        self.assertEqual(len(keys), 3)
        self.assertEqual(content_key(parent + f"?p={child_a}"),
                         content_key(parent + "?p=11111111-1111-1111-1111-111111111111"))
        for query in ("p=", "p=bad", f"p={child_a}&p={child_b}", f"p={child_a}&p="):
            url = parent + "?" + query
            self.assertEqual(content_key(url), normalize_url(url))
        self.assertNotEqual(content_key(f"https://one.notion.site/11111111111111111111111111111111{page}"),
                            content_key(f"https://two.notion.site/22222222222222222222222222222222{page}"))
        self.assertEqual(content_key("https://youtu.be/AbCdEf123_-?t=10"),
                         content_key("https://www.youtube.com/watch?v=AbCdEf123_-"))
        self.assertEqual(content_key("https://youtube.com/shorts/AbCdEf123_-"), "youtube:AbCdEf123_-")
        self.assertEqual(content_key("https://t.me/s/Example/42?single"),
                         content_key("https://telegram.me/example/42"))
        self.assertNotEqual(content_key("https://t.me/example/42?comment=7"),
                            content_key("https://t.me/example/42?comment=8"))
        self.assertNotEqual(content_key("https://t.me/example/42?comment="),
                            content_key("https://t.me/example/42"))
        self.assertEqual(content_key("https://threads.com/t/AbC_123"),
                         content_key("https://threads.net/@someone/post/AbC_123"))
        self.assertNotEqual(content_key("https://threads.com/t/AbC_123"),
                            content_key("https://threads.com/t/AbC_124"))

    def test_old_url_checkpoint_matches_new_provider_identity(self):
        self.item.url = "https://youtu.be/AbCdEf123_-"
        write(self.note, self.item)
        self.save_state({}, content_index={"https://www.youtube.com/watch?v=AbCdEf123_-": "older"})
        row = scan(self.root, self.state_path)["changes"][0]
        self.assertEqual((row["action"], row["canonical_id"]), ("merge", "older"))
        self.save_state({}, content_index={"https://www.youtube.com/watch?v=AbCdEf123_-": "older",
                                          "https://youtu.be/AbCdEf123_-": "another"})
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            scan(self.root, self.state_path)

    def test_assets_cannot_escape_source_scope_and_missing_is_not_silent(self):
        self.item.assets = ["Assets/../../private.txt"]
        write(self.note, self.item)
        records, errors = snapshot(self.root)
        self.assertFalse(records)
        self.assertEqual(len(errors), 1)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            snapshot(self.base / "missing")

    def test_render_preserves_verbatim_fields_and_manila_date(self):
        text = render_entry(asdict(self.item))
        self.assertNotIn("### Описание", text)
        self.assertIn("16 окт. 26", text)
        self.assertIn("Категории: #uncategorized", text)
        self.assertIn(self.item.user_comment, text)
        self.assertIn(self.item.transcript, text)
        self.assertIn("````text", text)

    def test_search_labels_preserve_source_fields(self):
        self.item.categories = ["AI и технологии"]
        self.item.tags = ["AI/Кодинг", "claude-code", "C++", "C#"]
        record = asdict(self.item)
        labels = classification(record)
        self.assertEqual(labels["categories"], ["AI and Technology"])
        self.assertEqual(labels["tags"], ["ai/coding", "claude-code", "c++", "c#"])
        self.assertEqual(labels["original_categories"], record["categories"])
        self.assertEqual(labels["original_tags"], record["tags"])
        self.assertEqual(labels["version"], 2)
        self.assertEqual(labels["language"], "en")
        self.assertEqual(labels["added_date"], "2026-10-16")
        self.assertEqual(labels["category_hashtags"], ["#ai_and_technology"])
        self.assertEqual(labels["tag_hashtags"], ["#ai_coding", "#claude_code", "#c_plus_plus", "#c_sharp"])
        self.assertEqual(hashtag(" #UI/UX "), "#ui_ux")
        self.assertEqual(display_date(datetime(2026, 1, 5)), "5 янв. 26")
        self.assertEqual(display_date(datetime(2026, 5, 15)), "15 мая 26")

    def test_translated_labels_preserve_originals_and_do_not_mutate_input(self):
        record = asdict(self.item)
        record.update(categories=["Design"], tags=["portfolio"],
                      original_categories=["Дизайн"], original_tags=["портфолио"])
        before = json.loads(json.dumps(record))
        labels = classification(record)
        self.assertEqual(record, before)
        self.assertEqual(labels["original_categories"], ["Дизайн"])
        self.assertEqual(labels["original_tags"], ["портфолио"])
        self.assertEqual(labels["category_hashtags"], ["#design"])
        self.assertEqual(labels["tag_hashtags"], ["#portfolio"])
        record["tags"] = ["совершенно-новый-непереведённый-тег"]
        with self.assertRaisesRegex(ValueError, "English translation"):
            classification(record)

    def test_image_bytes_change_without_note_edit_is_detected(self):
        (self.root / "Assets").mkdir()
        asset = self.root / "Assets" / "cover.png"
        asset.write_bytes(b"first")
        self.item.assets = ["Assets/cover.png"]
        write(self.note, self.item)
        current, _ = snapshot(self.root)
        self.save_state({"saved": {"fingerprint": current["saved"]["fingerprint"], "canonical_id": "saved"}})
        self.assertEqual(scan(self.root, self.state_path)["total_changes"], 0)
        asset.write_bytes(b"second")
        self.assertEqual(scan(self.root, self.state_path)["total_changes"], 1)

    def test_conflicting_source_ids_are_not_selected_for_publishing(self):
        (self.root / "Inbox").mkdir()
        self.item.summary = "Another version"
        write(self.root / "Inbox" / "same.md", self.item)
        records, errors = snapshot(self.root)
        self.assertNotIn("saved", records)
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
