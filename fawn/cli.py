import argparse
import os
import sys
from pathlib import Path

from .store import Store


DEFAULT_LIBRARY = Path("~/Documents/Fawn Library")


def resolve_library_root(cli_value=None, environment=None, home=None) -> Path:
    """Resolve the external Fawn library location.

    An explicit command-line location takes precedence over FAWN_LIBRARY; the
    default intentionally stays home-relative so a code checkout contains no
    user data.
    """
    env = os.environ if environment is None else environment
    selected = cli_value or env.get("FAWN_LIBRARY") or DEFAULT_LIBRARY
    selected_text = str(selected)
    if selected_text == "~" or selected_text.startswith("~/"):
        selected_path = (Path.home() if home is None else Path(home)).expanduser() / selected_text[2:]
    else:
        selected_path = Path(selected_text).expanduser()
    return selected_path.resolve()


def _print_rows(rows, root: Path):
    if not rows:
        print("Ничего не найдено.")
        return
    for row in rows:
        title = row["title"] or f"Материал из {row['platform']}"
        comment = row["user_comment"].replace("\n", " ").strip()
        print(f"{title}\n  ID: {row['id']}\n  {row['url']}\n  {root / row['path']}")
        if comment:
            print(f"  Комментарий: {comment}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fawn", description="Локальная библиотека ссылок Fawn")
    parser.add_argument(
        "--library",
        type=Path,
        help="Внешняя папка библиотеки (приоритетнее FAWN_LIBRARY)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Добавить ссылку")
    add.add_argument("url")
    add.add_argument("--comment", "-c", default="")

    process = sub.add_parser("process", help="Обработать входящие ссылки")
    process.add_argument("--limit", type=int, default=100)

    search = sub.add_parser("search", help="Найти материалы")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)

    recent = sub.add_parser("recent", help="Показать последние материалы")
    recent.add_argument("--limit", type=int, default=20)

    pending = sub.add_parser("pending", help="Показать материалы для обработки Codex")
    pending.add_argument("--limit", type=int, default=100)

    enrich = sub.add_parser("enrich", help="Добавить результат обработки Codex")
    enrich.add_argument("item_id")
    enrich.add_argument("--title")
    enrich.add_argument("--summary")
    enrich.add_argument("--tag", action="append", default=[])
    enrich.add_argument("--category", action="append", default=[])
    enrich.add_argument("--transcript-file", type=Path)

    sub.add_parser("rebuild", help="Пересоздать SQLite-индекс из Markdown")
    sub.add_parser("export", help="Экспортировать библиотеку в JSONL")
    sub.add_parser("stats", help="Показать статистику")

    telegram = sub.add_parser("telegram", help="Настроить или запустить Telegram")
    telegram_sub = telegram.add_subparsers(dest="telegram_command", required=True)
    telegram_sub.add_parser("set-token", help="Сохранить токен в macOS Keychain")
    telegram_sub.add_parser("pair", help="Разрешить личный Telegram-чат")
    telegram_sub.add_parser("run", help="Запустить Telegram long-polling")

    video = sub.add_parser("video", help="Подготовить временное аудио для локальной транскрипции")
    video_sub = video.add_subparsers(dest="video_command", required=True)
    prepare = video_sub.add_parser("prepare", help="Временно скачать аудиодорожку")
    prepare.add_argument("item_id")
    cleanup = video_sub.add_parser("cleanup", help="Удалить аудио после сохранения транскрипта")
    cleanup.add_argument("item_id")
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    root = resolve_library_root(args.library)
    if args.command == "telegram":
        from . import telegram

        if args.telegram_command == "set-token":
            telegram.set_token()
        elif args.telegram_command == "pair":
            telegram.pair(root)
        else:
            telegram.run(root)
        return

    if args.command == "video":
        from . import video

        store = Store(root)
        try:
            if args.video_command == "prepare":
                print(video.prepare(store, args.item_id))
            else:
                removed = video.cleanup(store, args.item_id)
                print("Временное аудио удалено." if removed else "Временного аудио уже нет.")
        finally:
            store.close()
        return

    store = Store(root)
    try:
        if args.command == "add":
            item, path, created = store.add(args.url, args.comment)
            action = "Добавлено" if created else "Уже было, комментарий обновлён"
            print(f"{action}: {path}")
        elif args.command == "process":
            results = store.process(max(1, args.limit))
            if not results:
                print("Входящих ссылок нет.")
            for item, path, error in results:
                if error:
                    if item.status == "extracted":
                        print(f"Обработано с предупреждением: {path} ({error})", file=sys.stderr)
                    else:
                        print(f"Не обработано {item.url}: {error}", file=sys.stderr)
                else:
                    print(f"Обработано: {path}")
        elif args.command == "search":
            _print_rows(store.search(args.query, max(1, args.limit)), root)
        elif args.command == "recent":
            _print_rows(store.recent(max(1, args.limit)), root)
        elif args.command == "pending":
            _print_rows(store.pending(max(1, args.limit)), root)
        elif args.command == "enrich":
            transcript = None
            if args.transcript_file is not None:
                transcript = args.transcript_file.expanduser().resolve().read_text(encoding="utf-8")
            _, path = store.enrich(
                args.item_id,
                title=args.title,
                summary=args.summary,
                tags=args.tag,
                categories=args.category,
                transcript=transcript,
            )
            print(f"Готово: {path}")
        elif args.command == "rebuild":
            print(f"Индекс пересоздан. Материалов: {store.rebuild()}")
        elif args.command == "export":
            path, count = store.export_jsonl()
            print(f"Экспортировано: {count}. Файл: {path}")
        elif args.command == "stats":
            print(f"Материалов: {store.count()}")
    finally:
        store.close()
