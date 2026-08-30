import hashlib
import http.client
import socket
import ssl
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict
from urllib.parse import urljoin, urlsplit, urlunsplit

from .utils import external_plain_text, resolve_public_http_url


USER_AGENT = "Fawn/0.1 (+local personal archive)"
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
SAFE_RASTER_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def safe_raster_extension(content_type: str):
    return SAFE_RASTER_IMAGE_TYPES.get(content_type.lower())


@dataclass
class PageData:
    title: str = ""
    description: str = ""
    author: str = ""
    image_url: str = ""
    media_type: str = "link"


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, pinned_ip: str, port: int, timeout: int):
        super().__init__(host, port=port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, pinned_ip: str, port: int, timeout: int):
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_ip = pinned_ip

    def connect(self):
        raw_socket = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metadata: Dict[str, str] = {}
        self.title_parts = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = {str(key).lower(): value for key, value in attrs if key}
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() != "meta":
            return
        key = (attributes.get("property") or attributes.get("name") or "").lower()
        value = attributes.get("content") or ""
        if key and value and key not in self.metadata:
            self.metadata[key] = value.strip()

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self):
        return " ".join("".join(self.title_parts).split())


def _read_limited(response, maximum: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > maximum:
        raise ValueError("Ответ сайта слишком большой")
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Ответ сайта слишком большой")
    return data


def _request_bytes(
    url: str,
    accept: str,
    page_maximum: int,
    image_maximum: int,
    redirects_left: int = 5,
):
    parts = urlsplit(url)
    addresses = resolve_public_http_url(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    connection_class = _PinnedHTTPSConnection if parts.scheme == "https" else _PinnedHTTPConnection
    connection = connection_class(parts.hostname, addresses[0], port, 25)
    path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    try:
        connection.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": accept})
        response = connection.getresponse()
        if response.status in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            if not location or redirects_left <= 0:
                raise ValueError("Слишком много или некорректная цепочка перенаправлений")
            redirected = urljoin(url, location)
            return _request_bytes(
                redirected,
                accept,
                page_maximum,
                image_maximum,
                redirects_left - 1,
            )
        if response.status >= 400:
            raise ValueError(f"Сайт вернул HTTP {response.status}")
        content_type = response.headers.get_content_type().lower()
        charset = response.headers.get_content_charset() or "utf-8"
        if content_type.startswith("video/"):
            body = b""
        else:
            maximum = image_maximum if content_type.startswith("image/") else page_maximum
            body = _read_limited(response, maximum)
        return url, content_type, charset, body
    finally:
        connection.close()


def fetch(url: str) -> PageData:
    final_url, content_type, charset, body = _request_bytes(
        url,
        "text/html,image/*;q=0.8,*/*;q=0.1",
        MAX_PAGE_BYTES,
        MAX_IMAGE_BYTES,
    )
    if safe_raster_extension(content_type):
        return PageData(
            title=Path(urlsplit(final_url).path).name or "Изображение",
            image_url=final_url,
            media_type="image",
        )
    if content_type.startswith("video/"):
        return PageData(title="Видео", media_type="video")
    if content_type not in {"text/html", "application/xhtml+xml"}:
        return PageData(title=Path(urlsplit(final_url).path).name, media_type="link")
    html = body.decode(charset, errors="replace")
    parser = MetadataParser()
    parser.feed(html)
    meta = parser.metadata
    image = meta.get("og:image") or meta.get("twitter:image") or meta.get("twitter:image:src") or ""
    if image:
        image = urljoin(final_url, image)
    content_hint = (meta.get("og:type") or "").lower()
    media_type = "video" if "video" in content_hint else "link"
    return PageData(
        title=external_plain_text(meta.get("og:title") or meta.get("twitter:title") or parser.title, 500),
        description=external_plain_text(
            meta.get("og:description") or meta.get("twitter:description") or meta.get("description") or "",
            5000,
        ),
        author=external_plain_text(meta.get("author") or meta.get("article:author") or "", 500),
        image_url=image,
        media_type=media_type,
    )


def download_image(url: str, assets_dir: Path, item_id: str) -> str:
    _, content_type, _, data = _request_bytes(
        url,
        "image/*",
        MAX_IMAGE_BYTES,
        MAX_IMAGE_BYTES,
    )
    extension = safe_raster_extension(content_type)
    if extension is None:
        raise ValueError("Обложка должна быть безопасным raster-изображением")
    digest = hashlib.sha256(data).hexdigest()[:12]
    folder = assets_dir / item_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"cover-{digest}{extension}"
    if not path.exists():
        path.write_bytes(data)
    return path.relative_to(assets_dir.parent).as_posix()
