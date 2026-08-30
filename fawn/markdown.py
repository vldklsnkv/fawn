import html
import json
import string
from pathlib import Path
from typing import Dict, Tuple

from .model import Item
from .utils import atomic_write


FIELDS = (
    "id",
    "url",
    "platform",
    "status",
    "created_at",
    "updated_at",
    "title",
    "author",
    "user_comment",
    "summary",
    "tags",
    "categories",
    "media_type",
    "assets",
)


def _yaml_value(value):
    return json.dumps(value, ensure_ascii=False)


def _plain_markdown(value: str) -> str:
    return "".join(
        f"&#{ord(character)};" if character in string.punctuation else character
        for character in value
    )


def render(item: Item) -> str:
    values = {field: getattr(item, field) for field in FIELDS}
    frontmatter = ["---"]
    frontmatter.extend(f"{key}: {_yaml_value(values[key])}" for key in FIELDS)
    frontmatter.append("---")
    title = _plain_markdown(item.title.strip()) or f"Материал из {item.platform}"
    comment = item.user_comment.strip() or "_Без комментария._"
    summary = _plain_markdown(item.summary.strip()) or "_Ожидает обработки._"
    transcript = (
        f"<pre>\n{html.escape(item.transcript, quote=False)}\n</pre>"
        if item.transcript
        else "_Транскрипта пока нет._"
    )
    assets = "\n".join(f"![[{asset}]]" for asset in item.assets) or "_Изображений нет._"
    return "\n".join(
        frontmatter
        + [
            "",
            f"# {title}",
            "",
            "> [!info] Источник",
            f"> [Открыть оригинал](<{item.url}>)",
            "",
            "## Мой комментарий",
            "",
            comment,
            "",
            "## Описание",
            "",
            summary,
            "",
            "## Транскрипт",
            "",
            transcript,
            "",
            "## Изображения",
            "",
            assets,
            "",
        ]
    )


def write(path: Path, item: Item) -> None:
    atomic_write(path, render(item))


def _sections(body: str) -> Dict[str, str]:
    sections: Dict[str, list] = {}
    current = None
    in_pre = False
    for line in body.splitlines():
        if line == "<pre>":
            in_pre = True
        if line.startswith("## ") and not in_pre:
            current = line[3:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
        if line == "</pre>":
            in_pre = False
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def _parse_scalar(value: str):
    value = value.strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_parse_scalar(part) for part in inner.split(",")] if inner else []
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if value in {"null", "~"}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def _parse_header(header: str) -> Dict[str, object]:
    lines = header.splitlines()
    data: Dict[str, object] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line or line[0].isspace():
            index += 1
            continue
        key, separator, raw_value = line.partition(":")
        if not separator:
            index += 1
            continue
        key = key.strip()
        raw_value = raw_value.strip()
        if not raw_value:
            values = []
            cursor = index + 1
            while cursor < len(lines) and lines[cursor].startswith("  - "):
                values.append(_parse_scalar(lines[cursor][4:]))
                cursor += 1
            data[key] = values
            index = cursor
            continue
        if raw_value in {"|", "|-", "|+", ">", ">-", ">+"}:
            block = []
            cursor = index + 1
            while cursor < len(lines) and (not lines[cursor] or lines[cursor][0].isspace()):
                value = lines[cursor]
                block.append(value[2:] if value.startswith("  ") else value.lstrip())
                cursor += 1
            data[key] = (" " if raw_value.startswith(">") else "\n").join(block)
            index = cursor
            continue
        data[key] = _parse_scalar(raw_value)
        index += 1
    return data


def read(path: Path) -> Item:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise ValueError(f"Нет frontmatter: {path}")
    try:
        header, body = raw[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise ValueError(f"Повреждён frontmatter: {path}") from exc
    data = _parse_header(header)
    sections = _sections(body)
    transcript = sections.get("Транскрипт", "")
    if transcript == "_Транскрипта пока нет._":
        transcript = ""
    elif transcript.startswith("<pre>\n") and transcript.endswith("\n</pre>"):
        transcript = html.unescape(transcript[6:-7])
    return Item(
        id=str(data.get("id", "")),
        url=str(data.get("url", "")),
        platform=str(data.get("platform", "web")),
        status=str(data.get("status", "inbox")),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
        title=str(data.get("title", "")),
        author=str(data.get("author", "")),
        user_comment=str(data.get("user_comment", "")),
        summary=str(data.get("summary", "")),
        tags=list(data.get("tags", []) or []),
        categories=list(data.get("categories", []) or []),
        media_type=str(data.get("media_type", "link")),
        assets=list(data.get("assets", []) or []),
        transcript=transcript,
    )
