import getpass
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .store import Store
from .utils import atomic_write, extract_urls, without_urls


KEYCHAIN_SERVICE = "Fawn Telegram Bot"


def _account() -> str:
    return getpass.getuser()


def set_token() -> None:
    token = getpass.getpass("Telegram bot token (ввод скрыт): ").strip()
    if not token or ":" not in token:
        raise ValueError("Токен выглядит некорректно")
    subprocess.run(
        ["security", "add-generic-password", "-U", "-a", _account(), "-s", KEYCHAIN_SERVICE, "-w"],
        check=True,
        input=token + "\n",
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print("Токен сохранён в macOS Keychain.")


def get_token() -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-a", _account(), "-s", KEYCHAIN_SERVICE, "-w"],
        check=False,
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        raise RuntimeError("Сначала выполни: python3 -m fawn telegram set-token")
    return token


class TelegramClient:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}"

    def call(self, method: str, params: Optional[Dict[str, object]] = None):
        encoded = urlencode({key: str(value) for key, value in (params or {}).items()}).encode("utf-8")
        request = Request(f"{self.base}/{method}", data=encoded)
        try:
            with urlopen(request, timeout=70) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Telegram request failed ({type(error).__name__})") from None
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API: {payload.get('description', 'unknown error')}")
        return payload.get("result")

    def updates(self, offset: int, timeout: int = 50):
        return self.call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": '["message","edited_message"]',
            },
        )

    def send(self, chat_id: int, text: str):
        return self.call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"})


def _config_path(root: Path) -> Path:
    return root / "System" / "config.json"


def _state_path(root: Path) -> Path:
    return root / "System" / "telegram-state.json"


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, value) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def pair(root: Path) -> None:
    client = TelegramClient(get_token())
    print("Отправь /start своему боту. Ожидаю сообщение…")
    offset = 0
    while True:
        for update in client.updates(offset):
            offset = max(offset, int(update["update_id"]) + 1)
            message = update.get("message") or {}
            if str(message.get("text", "")).strip() != "/start":
                continue
            chat = message.get("chat") or {}
            chat_id = int(chat["id"])
            label = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) or chat.get("username") or str(chat_id)
            answer = input(f"Разрешить чат {label} (id {chat_id})? [y/N] ").strip().lower()
            if answer not in {"y", "yes", "д", "да"}:
                print("Отменено.")
                return
            config = _read_json(_config_path(root), {"allowed_chat_ids": []})
            allowed = {int(value) for value in config.get("allowed_chat_ids", [])}
            allowed.add(chat_id)
            config["allowed_chat_ids"] = sorted(allowed)
            _write_json(_config_path(root), config)
            client.send(chat_id, "Fawn подключён. Пришли ссылку с комментарием.")
            print("Чат подключён.")
            return


def _message_text(message: Dict) -> str:
    return str(message.get("text") or message.get("caption") or "")


def _source_text(message: Dict) -> str:
    text = _message_text(message)
    if extract_urls(text):
        return text
    reply = message.get("reply_to_message") or {}
    return "\n".join(filter(None, [_message_text(reply), text]))


def _handle_update(client: TelegramClient, store: Store, allowed, update: Dict) -> None:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = int(chat.get("id", 0))
    if chat_id not in allowed:
        return
    source = _source_text(message)
    urls = extract_urls(source)
    if not urls:
        client.send(
            chat_id,
            "Не нашёл ссылку. Пришли её с подписью или ответь комментарием на сообщение со ссылкой.",
        )
        return
    current_text = _message_text(message)
    comment = without_urls(current_text)
    if current_text.strip().startswith("/start"):
        comment = ""
    created = 0
    updated = 0
    for url in urls:
        _, _, is_new = store.add(url, comment)
        created += int(is_new)
        updated += int(not is_new)
    client.send(
        chat_id,
        f"Сохранено: {created}. Уже было/обновлено: {updated}. Обработаю по расписанию.",
    )


def process_one_update(
    client: TelegramClient,
    store: Store,
    allowed,
    update: Dict,
    offset: int,
    state_path: Path,
) -> int:
    next_offset = max(offset, int(update["update_id"]) + 1)
    _handle_update(client, store, allowed, update)
    _write_json(state_path, {"offset": next_offset})
    return next_offset


def run(root: Path) -> None:
    client = TelegramClient(get_token())
    config = _read_json(_config_path(root), {"allowed_chat_ids": []})
    allowed = {int(value) for value in config.get("allowed_chat_ids", [])}
    if not allowed:
        raise RuntimeError("Сначала выполни: python3 -m fawn telegram pair")
    state_path = _state_path(root)
    state = _read_json(state_path, {"offset": 0})
    offset = int(state.get("offset", 0))
    store = Store(root)
    print("Fawn Telegram работает. Ctrl+C — остановить.")
    try:
        while True:
            try:
                updates = client.updates(offset)
                for update in updates:
                    offset = process_one_update(client, store, allowed, update, offset, state_path)
            except KeyboardInterrupt:
                raise
            except Exception as error:
                print(f"Telegram временно недоступен: {error}", file=sys.stderr)
                time.sleep(5)
    finally:
        store.close()
