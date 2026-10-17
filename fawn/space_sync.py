"""Credential-free local planning for a Pages-connected agent.

Remote writes belong to the agent. Source notes and media are always read-only.
"""
import hashlib
import json
import re
import string
import unicodedata
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from .markdown import read as read_note
from .taxonomy import english_category, english_tag
from .utils import atomic_write, normalize_url


def content_key(url):
    normalized = normalize_url(url)
    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").removeprefix("www.")
    patterns = {"instagram.com": r"^/(?:p|reels?|tv)/([^/]+)", "behance.net": r"^/gallery/(\d+)",
                "dribbble.com": r"^/shots/(\d+)", "tiktok.com": r"/video/(\d+)", "x.com": r"/status/(\d+)"}
    for provider, pattern in patterns.items():
        if host == provider or host.endswith("." + provider):
            match = re.search(pattern, parsed.path)
            if match:
                return provider + ":" + match.group(1)
    if host in {"threads.net", "threads.com"}:
        match = re.fullmatch(r"/(?:t/|@[^/]+/post/)([A-Za-z0-9_-]+)/?", parsed.path)
        if match:
            return "threads:" + match.group(1)
    if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        video = ""
        if host == "youtu.be":
            video = parsed.path.strip("/")
        elif parsed.path == "/watch":
            values = parse_qs(parsed.query, keep_blank_values=True).get("v", [])
            if len(set(values)) == 1:
                video = values[0]
        else:
            match = re.fullmatch(r"/(?:shorts|embed|live)/([A-Za-z0-9_-]+)/?", parsed.path)
            if match:
                video = match.group(1)
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
            return "youtube:" + video
    if host in {"t.me", "telegram.me"}:
        match = re.fullmatch(r"/(?:s/)?([A-Za-z0-9_]+)/(\d+)/?", parsed.path)
        if match:
            comments = parse_qs(parsed.query, keep_blank_values=True).get("comment", [])
            if comments and (len(set(comments)) != 1 or not comments[0].isdigit()):
                return normalized
            suffix = ":comment:" + comments[0] if comments else ""
            return "telegram:" + match.group(1).casefold() + ":" + match.group(2) + suffix
    if host in {"notion.so", "notion.site"} or host.endswith(".notion.so") or host.endswith(".notion.site"):
        match = re.search(r"(?:^|-)([0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$",
                          parsed.path.rstrip("/").split("/")[-1])
        if match:
            query = parse_qs(parsed.query, keep_blank_values=True)
            children = query.get("p", [])
            child = ""
            if children:
                normalized_children = {value.replace("-", "").casefold() for value in children}
                if len(normalized_children) != 1 or not all(
                    re.fullmatch(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value)
                    for value in children
                ):
                    return normalized
                child = ":page:" + next(iter(normalized_children))
            views = query.get("v", [])
            if views and len(set(views)) != 1:
                return normalized
            suffix = ":view:" + views[0].replace("-", "").casefold() if views else ""
            return "notion:" + match.group(1).replace("-", "").casefold() + child + suffix
    return normalized


def source_fingerprint(record, assets):
    data = {"record": record, "assets": [{"path": a["path"], "sha256": a["sha256"]} for a in assets]}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def snapshot(root):
    root = Path(root).resolve()
    if not root.is_dir() or not (root / "Library").is_dir():
        raise ValueError("Source library is unavailable")
    items, errors, conflicted = {}, [], set()
    for folder in ("Library", "Inbox"):
        for path in sorted((root / folder).glob("*.md")):
            try:
                path.resolve().relative_to(root)
                record = asdict(read_note(path))
                if not record["id"] or not record["url"]:
                    raise ValueError("Missing source ID or URL")
                if not re.fullmatch(r"[A-Za-z0-9_-]+", record["id"]):
                    raise ValueError("Invalid source ID")
                if record["id"] in conflicted:
                    raise ValueError("Conflicting notes with the same source ID")
                assets = []
                for name in record["assets"]:
                    relative = Path(name)
                    if relative.is_absolute() or not relative.parts or relative.parts[0] != "Assets":
                        raise ValueError("Asset must be relative to Assets")
                    asset = (root / relative).resolve()
                    asset.relative_to(root / "Assets")
                    data = asset.read_bytes()
                    assets.append({"path": name, "file": str(asset), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
                row = {"record": record, "note_path": path.relative_to(root).as_posix(), "assets": assets,
                       "fingerprint": source_fingerprint(record, assets), "content_key": content_key(record["url"])}
                previous = items.get(record["id"])
                if previous and previous["fingerprint"] != row["fingerprint"]:
                    items.pop(record["id"])
                    conflicted.add(record["id"])
                    raise ValueError("Conflicting notes with the same source ID")
                items[record["id"]] = row
            except (ValueError, OSError, IndexError, TypeError) as error:
                errors.append({"note_path": path.relative_to(root).as_posix(), "error": str(error)})
    return items, errors


def load_state(path, root):
    if Path(path).resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("Keep sync state outside the source library")
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    if state.get("version") != 1 or Path(state["library"]).resolve() != Path(root).resolve():
        raise ValueError("Sync state does not match this library")
    return state


def plain(value):
    return "".join("\\" + c if c in string.punctuation else c for c in value)


def fenced(value):
    fence = "`" * max(3, 1 + max((len(x) for x in re.findall(r"`+", value)), default=0))
    return fence + "text\n" + value + "\n" + fence


def display_date(value):
    """Russian display date; source timestamps remain ISO and timezone-aware."""
    months = ("янв.", "февр.", "мар.", "апр.", "мая", "июн.",
              "июл.", "авг.", "сент.", "окт.", "нояб.", "дек.")
    return f"{value.day} {months[value.month - 1]} {value.year % 100:02d}"


def hashtag(value):
    """Stable searchable label, keeping the original value separate."""
    value = unicodedata.normalize("NFKC", value).casefold().strip().lstrip("#")
    value = value.replace("+", "_plus_").replace("#", "_sharp_")
    value = "".join(c if c.isalnum() else "_" for c in value)
    value = re.sub(r"_+", "_", value).strip("_")
    return "#" + value if value else ""


def classification(record):
    """Machine-readable fields for future filters, separate from visible labels."""
    added = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
    if added.tzinfo is None:
        raise ValueError("Source date must include a timezone")
    categories = list(dict.fromkeys(map(english_category, record["categories"])))
    tags = list(dict.fromkeys(map(english_tag, record["tags"])))
    return {"version": 2, "language": "en", "fawn_id": record["id"], "categories": categories, "tags": tags,
            "original_categories": list(record.get("original_categories", record["categories"])),
            "original_tags": list(record.get("original_tags", record["tags"])),
            "category_hashtags": list(dict.fromkeys(filter(None, map(hashtag, categories or ["Uncategorized"])))),
            "tag_hashtags": list(dict.fromkeys(filter(None, map(hashtag, tags)))),
            "added_at": record["created_at"],
            "added_date": added.astimezone(ZoneInfo("Asia/Manila")).date().isoformat()}


def render_entry(record, images=None, timezone="Asia/Manila"):
    added = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
    if added.tzinfo is None:
        raise ValueError("Source date must include a timezone")
    date = display_date(added.astimezone(ZoneInfo(timezone)))
    blocks = ["## " + plain(record["title"] or record["url"])]
    if record["summary"]:
        blocks.append(plain(record["summary"]))
    labels = classification(record)
    blocks += ["Категории: " + " ".join(labels["category_hashtags"]) + " · Теги: " + (" ".join(labels["tag_hashtags"]) or "—") + " · Добавлено: **" + date + "**",
               "[Открыть оригинал](<" + normalize_url(record["url"]) + ">) · Fawn ID: `" + record["id"] + "`"]
    for label, value in (("Мой комментарий", record["user_comment"]), ("Транскрипт", record["transcript"])):
        if value:
            blocks += ["### " + label, fenced(value)]
    return "\n\n".join(blocks + (images or []))


def scan(root, state_path, limit=20):
    state = load_state(state_path, root)
    content_index = dict(state.get("content_index", {}))
    for key, canonical_id in list(content_index.items()):
        if key.startswith(("http://", "https://")):
            alias = content_key(key)
            if alias in content_index and content_index[alias] != canonical_id:
                raise ValueError("Saved content identity is ambiguous; review duplicates first")
            content_index[alias] = canonical_id
    current, errors = snapshot(root)
    changed = []
    for item_id, row in current.items():
        before = state["items"].get(item_id)
        if before and before["fingerprint"] == row["fingerprint"]:
            continue
        target = before.get("canonical_id") if before else content_index.get(row["content_key"])
        assets = [{**a, "known_upload": state.get("images", {}).get(a["sha256"])} for a in row["assets"]]
        try:
            text = render_entry(row["record"], timezone=state.get("timezone", "Asia/Manila"))
        except (ValueError, TypeError) as error:
            errors.append({"note_path": row["note_path"], "error": str(error)})
            continue
        changed.append({**row, "id": item_id, "canonical_id": target or item_id,
                        "previous_record": before.get("source_record") if before else None,
                        "action": "update" if before else ("merge" if target else "insert"), "assets": assets,
                        "text_markdown": text})
    changed.sort(key=lambda row: (row["record"]["created_at"], row["id"]), reverse=True)
    return {"version": 1, "state_revision": state.get("revision", 0), "root_page_id": state["root_page_id"],
            "space_id": state["space_id"], "changes": changed[:limit], "total_changes": len(changed),
            "remaining": max(0, len(changed) - limit), "errors": errors,
            "missing_source_ids": sorted(set(state["items"]) - set(current))}


def acknowledge(root, state_path, plan_path, receipt_path):
    import fcntl

    if Path(state_path).resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("Keep sync state outside the source library")
    with Path(str(state_path) + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _acknowledge(root, state_path, plan_path, receipt_path)


def _acknowledge(root, state_path, plan_path, receipt_path):
    state_path = Path(state_path)
    original = state_path.read_bytes()
    state = load_state(state_path, root)
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    if plan["state_revision"] != state.get("revision", 0) or plan["root_page_id"] != state["root_page_id"]:
        raise ValueError("Stale sync plan; reconcile cloud state before retrying")
    if receipt.get("root_verified") is not True or not receipt.get("synced_at"):
        raise ValueError("Root counts and update date must be verified before acknowledging")
    candidates = {row["id"]: row for row in plan["changes"]}
    if not receipt.get("items"):
        raise ValueError("No confirmed items")
    images = dict(state.get("images", {}))
    for upload in receipt.get("uploads", []):
        if upload.get("file_access_confirmed") is not True or not upload.get("reference", "").startswith("library-file:"):
            raise ValueError("Unconfirmed native image upload")
        if upload["source_sha256"] != upload["native_sha256"] and upload.get("original_archived") is not True:
            raise ValueError("Derived image requires preservation of its full original")
        images[upload["source_sha256"]] = upload
    for result in receipt["items"]:
        row = candidates[result["id"]]
        if result.get("verified") is not True or result.get("fingerprint") != row["fingerprint"]:
            raise ValueError("Item was not verified against its source snapshot")
        if any(asset["sha256"] not in images for asset in row["assets"]):
            raise ValueError("Not all source images have confirmed native references")
        if not result.get("page_id", "").startswith("page_") or not result.get("canonical_id"):
            raise ValueError("Missing canonical destination")
        state["items"][result["id"]] = {"fingerprint": row["fingerprint"], "canonical_id": result["canonical_id"],
                                          "topic": result["topic"], "page_id": result["page_id"], "source_record": row["record"]}
        for known in state["items"].values():
            if known["canonical_id"] == result["canonical_id"]:
                known.update(topic=result["topic"], page_id=result["page_id"])
        state.setdefault("content_index", {})[row["content_key"]] = result["canonical_id"]
        state.setdefault("topics", {}).setdefault(result["topic"], {"page_id": result["page_id"]})
    state["images"] = images
    state["revision"] = state.get("revision", 0) + 1
    state["last_successful_sync"] = receipt["synced_at"]
    if state_path.read_bytes() != original:
        raise ValueError("Sync state changed concurrently")
    atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    return len(receipt["items"])
