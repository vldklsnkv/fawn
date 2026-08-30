import json
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional, Tuple

from . import db
from .extractor import download_image, fetch
from .markdown import read as read_note
from .markdown import write as write_note
from .model import Item
from .utils import (
    atomic_write,
    ensure_public_http_url,
    normalize_url,
    now_iso,
    platform_for,
    safe_filename_component,
    stable_id,
    unique_strings,
)


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.inbox = self.root / "Inbox"
        self.library = self.root / "Library"
        self.assets = self.root / "Assets"
        self.system = self.root / "System"
        for folder in (self.inbox, self.library, self.assets, self.system):
            folder.mkdir(parents=True, exist_ok=True)
        self.connection = db.connect(self.system / "fawn.sqlite")
        if db.count(self.connection) == 0 and any(
            any(folder.glob("*.md")) for folder in (self.inbox, self.library)
        ):
            self.rebuild()

    def close(self):
        self.connection.close()

    def _path_for(self, item: Item, folder: Optional[Path] = None) -> Path:
        target = folder or self.inbox
        label = item.title if item.status == "ready" and item.title else item.platform
        filename = safe_filename_component(label, item.platform)
        return target / f"{item.id} - {filename}.md"

    def add(self, url: str, comment: str = "") -> Tuple[Item, Path, bool]:
        normalized = normalize_url(url)
        ensure_public_http_url(normalized)
        existing = db.find_by_url(self.connection, normalized)
        if existing:
            path = self.root / existing["path"]
            item = read_note(path)
            duplicate_comment = comment == item.user_comment or item.user_comment.endswith("\n\n" + comment)
            if comment.strip() and not duplicate_comment:
                item.user_comment = "\n\n".join(filter(None, [item.user_comment, comment]))
                item.updated_at = now_iso()
                write_note(path, item)
                db.upsert(self.connection, item, path, self.root)
            return item, path, False
        created = now_iso()
        item = Item(
            id=stable_id(normalized, created),
            url=normalized,
            platform=platform_for(normalized),
            status="inbox",
            created_at=created,
            updated_at=created,
            user_comment=comment,
        )
        path = self._path_for(item)
        write_note(path, item)
        db.upsert(self.connection, item, path, self.root)
        return item, path, True

    def process(self, limit: int = 100) -> List[Tuple[Item, Path, Optional[str]]]:
        results = []
        for row in db.by_status(self.connection, "inbox", limit):
            path = self.root / row["path"]
            try:
                item = read_note(path)
                page = fetch(item.url)
                item.title = page.title.strip() or item.title
                item.author = page.author.strip() or item.author
                item.summary = page.description.strip() or item.summary
                item.media_type = page.media_type or item.media_type
                warning = None
                if page.image_url:
                    try:
                        asset = download_image(page.image_url, self.assets, item.id)
                        item.assets = unique_strings([*item.assets, asset])
                    except Exception as image_error:
                        warning = f"Обложка: {image_error}"
                item.status = "extracted"
                item.updated_at = now_iso()
                new_path = self._path_for(item, self.library)
                write_note(new_path, item)
                if new_path != path and path.exists():
                    path.unlink()
                db.upsert(self.connection, item, new_path, self.root)
                results.append((item, new_path, warning))
            except Exception as error:
                results.append((read_note(path), path, str(error)))
        return results

    def rebuild(self) -> int:
        db.clear(self.connection)
        count = 0
        for folder in (self.inbox, self.library):
            for path in sorted(folder.glob("*.md")):
                try:
                    item = read_note(path)
                except ValueError:
                    continue
                if not item.id or not item.url:
                    continue
                db.upsert(self.connection, item, path, self.root)
                count += 1
        return count

    def search(self, query: str, limit: int = 20):
        return db.search(self.connection, query, limit)

    def get(self, item_id: str) -> Tuple[Item, Path]:
        row = db.find_by_id(self.connection, item_id)
        if row is None:
            raise ValueError(f"Материал не найден: {item_id}")
        path = self.root / row["path"]
        return read_note(path), path

    def pending(self, limit: int = 100):
        return db.by_status(self.connection, "extracted", limit)

    def enrich(
        self,
        item_id: str,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        tags: Optional[List[str]] = None,
        categories: Optional[List[str]] = None,
        transcript: Optional[str] = None,
    ) -> Tuple[Item, Path]:
        item, path = self.get(item_id)
        if title is not None:
            processed_title = " ".join(title.split())
            if not processed_title:
                raise ValueError("Обработанный заголовок не может быть пустым")
            item.title = processed_title
        if summary is not None:
            item.summary = summary.strip()
        if tags:
            item.tags = unique_strings([*item.tags, *tags])
        if categories:
            item.categories = unique_strings([*item.categories, *categories])
        if transcript is not None:
            item.transcript = transcript.strip()
            if item.transcript:
                item.media_type = "video"
        item.status = "ready"
        item.updated_at = now_iso()
        new_path = self._path_for(item, self.library)
        write_note(new_path, item)
        if new_path != path and path.exists():
            path.unlink()
        db.upsert(self.connection, item, new_path, self.root)
        return item, new_path

    def recent(self, limit: int = 20):
        return db.recent(self.connection, limit)

    def count(self) -> int:
        return db.count(self.connection)

    def export_jsonl(self) -> Tuple[Path, int]:
        records = []
        for folder in (self.inbox, self.library):
            for path in sorted(folder.glob("*.md")):
                try:
                    item = read_note(path)
                except ValueError:
                    continue
                record = asdict(item)
                record["note_path"] = path.relative_to(self.root).as_posix()
                records.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
        export_path = self.root / "Exports" / "fawn.jsonl"
        atomic_write(export_path, "\n".join(records) + ("\n" if records else ""))
        return export_path, len(records)
