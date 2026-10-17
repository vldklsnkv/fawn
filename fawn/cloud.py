"""One-time cloud receiver setup; never prints or deploys the Telegram token."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .telegram import TelegramClient, get_token


def endpoint(project: str) -> str:
    if not re.fullmatch(r"[a-z]{20}", project):
        raise ValueError("Invalid Supabase project reference")
    return f"https://{project}.supabase.co/functions/v1/fawn-telegram-inbox"


def webhook_secret(runtime: Path, library: Path) -> str:
    runtime = runtime.resolve()
    for source in (Path(__file__).resolve().parents[1], library.resolve()):
        if runtime == source or source in runtime.parents:
            raise ValueError("Private runtime must be outside the project and source library")
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = runtime / "webhook.secret"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_hex(32))
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("Unsafe webhook secret permissions")
    value = path.read_text().strip()
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Invalid webhook secret")
    return value


def request(project: str, payload: dict, secret: str = ""):
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Telegram-Bot-Api-Secret-Token"] = secret
    req = Request(endpoint(project), data=json.dumps(payload).encode(), headers=headers)
    try:
        with urlopen(req, timeout=35) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, {"error": "HTTP failure"}
    except (URLError, TimeoutError, json.JSONDecodeError):
        raise RuntimeError("Cloud receiver request failed") from None


def save_private(path: Path, value: dict):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def verify(project: str, secret: str, chat_id: int) -> dict:
    diagnostic_id = -int(time.time() * 1000)
    update = {"update_id": diagnostic_id, "message": {"message_id": -1, "date": int(time.time()),
        "chat": {"id": chat_id, "type": "private"}, "text": "https://example.com/fawn-cloud-diagnostic\nCloud verification, not library content"}}
    unauth, _ = request(project, update)
    wrong_chat = {**update, "message": {**update["message"], "chat": {"id": 0, "type": "private"}}}
    ignored_status, ignored = request(project, wrong_chat, secret)
    status, saved = request(project, update, secret)
    duplicate_status, duplicate = request(project, update, secret)
    if not (unauth == 401 and ignored_status == 200 and ignored == {"ok": True}
            and status == 200 and saved == {"ok": True, "inserted": True}
            and duplicate_status == 200 and duplicate == {"ok": True, "inserted": False}):
        raise RuntimeError("Cloud receiver verification failed")
    return {"project": project, "secret_sha256": hashlib.sha256(secret.encode()).hexdigest(),
        "diagnostic_update_id": diagnostic_id, "verified_at": int(time.time()),
        "checks": ["secret_authentication", "paired_chat_allowlist", "durable_insert", "duplicate_suppression"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "verify", "status", "connect"])
    parser.add_argument("--project", required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args()
    endpoint(args.project)
    if args.command == "status":
        info = TelegramClient(get_token()).call("getWebhookInfo")
        print(json.dumps({key: info.get(key) for key in ("url", "pending_update_count", "last_error_date", "last_error_message")}))
        return
    secret = webhook_secret(args.runtime, args.library)
    config = json.loads((args.library / "System" / "config.json").read_text())
    allowed = [str(int(value)) for value in config["allowed_chat_ids"]]
    if not allowed:
        raise ValueError("No paired Telegram chat")
    if args.command == "init":
        print(json.dumps({"secret_sha256": hashlib.sha256(secret.encode()).hexdigest(), "allowed_chat_ids": allowed}))
    elif args.command == "verify":
        result = verify(args.project, secret, int(allowed[0]))
        save_private(args.runtime / "receiver-verified.json", result)
        print(json.dumps(result))
    elif args.command == "connect":
        verified = json.loads((args.runtime / "receiver-verified.json").read_text())
        if (verified.get("project") != args.project or verified.get("secret_sha256") != hashlib.sha256(secret.encode()).hexdigest()
                or time.time() - verified.get("verified_at", 0) > 3600):
            raise ValueError("A fresh receiver verification is required")
        client = TelegramClient(get_token())
        identity = client.call("getMe")
        before = client.call("getWebhookInfo")
        target = endpoint(args.project)
        if before.get("url") and before["url"] != target:
            raise ValueError("Another webhook already owns this bot")
        client.call("setWebhook", {"url": target, "secret_token": secret,
            "allowed_updates": '["message","edited_message"]', "drop_pending_updates": "false", "max_connections": 1})
        after = client.call("getWebhookInfo")
        if after.get("url") != target:
            raise RuntimeError("Cloud webhook not confirmed")
        result = {"bot_id": identity["id"], "bot_username": identity.get("username"), "url": after["url"],
            "pending_update_count": after.get("pending_update_count"), "last_error_date": after.get("last_error_date"),
            "last_error_message": after.get("last_error_message"), "connected_at": int(time.time()), "dropped_pending_updates": False}
        save_private(args.runtime / "webhook-connected.json", result)
        print(json.dumps(result))


if __name__ == "__main__":
    main()
