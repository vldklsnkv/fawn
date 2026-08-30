import hashlib
import ipaddress
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "si",
}

CANONICAL_HOSTS = {
    "www.x.com": "x.com",
    "twitter.com": "x.com",
    "www.twitter.com": "x.com",
    "www.instagram.com": "instagram.com",
    "www.threads.net": "threads.net",
    "www.youtube.com": "youtube.com",
    "m.youtube.com": "youtube.com",
}

URL_RE = re.compile(r"https?://[^\s<>\]\[(){}]+", re.IGNORECASE)
FILENAME_FORBIDDEN_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_url(value: str) -> str:
    raw = value.strip().rstrip(".,;:!?\"'")
    if any(character.isspace() or ord(character) < 32 for character in raw):
        raise ValueError("Ссылка содержит недопустимые символы")
    if "<" in raw or ">" in raw:
        raise ValueError("Ссылка содержит недопустимые символы")
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("Поддерживаются только полные http/https-ссылки")
    host = CANONICAL_HOSTS.get(parts.hostname.lower(), parts.hostname.lower())
    port = parts.port
    netloc = host if port is None else f"{host}:{port}"
    query = []
    for key, val in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower().startswith("utm_") or key.lower() in TRACKING_KEYS:
            continue
        query.append((key, val))
    path = parts.path or "/"
    scheme = "https" if host in set(CANONICAL_HOSTS.values()) else parts.scheme.lower()
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def extract_urls(text: str) -> List[str]:
    found = []
    for match in URL_RE.findall(text or ""):
        try:
            url = normalize_url(match)
        except ValueError:
            continue
        if url not in found:
            found.append(url)
    return found


def without_urls(text: str) -> str:
    def preserve_trailing_punctuation(match):
        value = match.group(0)
        trimmed = value.rstrip(".,;:!?\"'")
        return value[len(trimmed) :]

    return URL_RE.sub(preserve_trailing_punctuation, text or "").strip(" \t\r\n")


def platform_for(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host in {"t.me", "telegram.me"} or host.endswith(".t.me"):
        return "telegram"
    if host in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}:
        return "x"
    if host == "threads.net" or host.endswith(".threads.net"):
        return "threads"
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return "instagram"
    if host in {"youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com"}:
        return "youtube"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok"
    return "web"


def stable_id(url: str, created_at: str) -> str:
    date = created_at[:10].replace("-", "")
    digest = hashlib.sha256(f"{url}\n{created_at}".encode("utf-8")).hexdigest()[:10]
    return f"{date}-{digest}"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


def unique_strings(values: Iterable[str]) -> List[str]:
    result = []
    seen = set()
    for value in values:
        cleaned = str(value).strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def safe_filename_component(value: str, fallback: str, max_bytes: int = 120) -> str:
    cleaned = FILENAME_FORBIDDEN_RE.sub(" ", str(value))
    cleaned = " ".join(cleaned.split()).strip(" .") or fallback
    result = ""
    for character in cleaned:
        candidate = result + character
        if len(candidate.encode("utf-8")) > max_bytes:
            break
        result = candidate
    return result.rstrip(" .") or fallback


def external_plain_text(value: str, limit: int) -> str:
    cleaned = "".join(character for character in str(value) if character in "\n\t" or ord(character) >= 32)
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit].strip()


def resolve_public_http_url(url: str) -> List[str]:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Небезопасная ссылка")
    host = parts.hostname.lower()
    if host == "localhost" or host.endswith(".local"):
        raise ValueError("Локальные адреса запрещены")
    try:
        infos = socket.getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Не удалось определить адрес сайта") from exc
    addresses = []
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ValueError("Приватные и локальные адреса запрещены")
        rendered = str(address)
        if rendered not in addresses:
            addresses.append(rendered)
    if not addresses:
        raise ValueError("Не удалось определить публичный адрес сайта")
    return addresses


def ensure_public_http_url(url: str) -> None:
    resolve_public_http_url(url)
