import shutil
import subprocess
from pathlib import Path

from .store import Store
from .utils import ensure_public_http_url, platform_for


MAX_DURATION_SECONDS = 30 * 60
MAX_AUDIO_BYTES = 250 * 1024 * 1024
SUPPORTED_PLATFORMS = {"instagram", "telegram", "threads", "tiktok", "x", "youtube"}


def _runtime_dir(store: Store, item_id: str) -> Path:
    runtime_root = (store.system / "runtime").resolve()
    target = (runtime_root / item_id).resolve()
    if target.parent != runtime_root:
        raise ValueError("Некорректный ID материала")
    return target


def prepare(store: Store, item_id: str) -> Path:
    item, _ = store.get(item_id)
    actual_platform = platform_for(item.url)
    if actual_platform not in SUPPORTED_PLATFORMS:
        raise ValueError("Временная загрузка разрешена только для поддерживаемых видеоплатформ")
    ensure_public_http_url(item.url)
    target = _runtime_dir(store, item_id)
    if target.exists():
        existing = sorted(path for path in target.glob("source.*") if _valid_audio(path, target))
        if len(existing) == 1:
            return existing[0]
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    output_template = str(target / "source.%(ext)s")
    command = [
        "yt-dlp",
        "--ignore-config",
        "--no-playlist",
        "--max-downloads",
        "1",
        "--match-filter",
        f"duration<=?{MAX_DURATION_SECONDS} & !is_live",
        "--max-filesize",
        "250M",
        "--socket-timeout",
        "30",
        "--no-progress",
        "--extract-audio",
        "--audio-format",
        "m4a",
        "--output",
        output_template,
        "--",
        item.url,
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=15 * 60)
    except (OSError, subprocess.TimeoutExpired) as error:
        shutil.rmtree(target, ignore_errors=True)
        raise RuntimeError(f"Не удалось запустить yt-dlp ({type(error).__name__})") from None
    if result.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        last_line = (result.stderr or "").strip().splitlines()
        detail = last_line[-1][:300] if last_line else "неизвестная ошибка"
        raise RuntimeError(f"yt-dlp: {detail}")
    files = sorted(path for path in target.glob("source.*") if _valid_audio(path, target))
    if len(files) != 1:
        shutil.rmtree(target, ignore_errors=True)
        raise RuntimeError("yt-dlp не создал единственный аудиофайл")
    return files[0]


def _valid_audio(path: Path, target: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        resolved = path.resolve(strict=True)
        size = resolved.stat().st_size
    except OSError:
        return False
    return resolved.parent == target and 0 < size <= MAX_AUDIO_BYTES


def cleanup(store: Store, item_id: str) -> bool:
    item, _ = store.get(item_id)
    if item.status != "ready" or not item.transcript.strip():
        raise ValueError("Временное аудио можно удалить только после сохранения транскрипта")
    target = _runtime_dir(store, item_id)
    if not target.exists():
        return False
    shutil.rmtree(target)
    return True
