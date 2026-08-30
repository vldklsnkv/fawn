# Fawn

Fawn — локальная переносимая библиотека ссылок и медиа. Код и личная библиотека намеренно разделены: checkout можно публиковать, а данные остаются в выбранной внешней папке.

## Что уже работает

- добавление ссылок с личным комментарием;
- Obsidian-заметка на каждую ссылку;
- локальный SQLite FTS5-индекс, который можно полностью пересоздать;
- извлечение заголовка, описания и доступной обложки страницы;
- полнотекстовый поиск по русскому и английскому тексту;
- открытый JSONL-экспорт поверх Markdown-источника;
- безопасный Telegram long-polling без сторонних Python API и облачных AI API.

Видео постоянно не хранится. Для локальной транскрипции Fawn временно извлекает одну аудиодорожку через `yt-dlp`; после успешной записи транскрипта временный файл можно удалить. В библиотеке остаются URL, транскрипт, таймкоды и описание.

## Быстрый старт

```sh
python3 -m fawn --library "~/Documents/Fawn Library" add "https://example.com" --comment "Пример для вишлиста"
python3 -m fawn --library "~/Documents/Fawn Library" process
python3 -m fawn --library "~/Documents/Fawn Library" pending
python3 -m fawn --library "~/Documents/Fawn Library" search "вишлист"
```

`--library PATH` имеет приоритет над `FAWN_LIBRARY`. Если не задано ни одно из них, используется `~/Documents/Fawn Library`. Открой выбранную папку как отдельный vault в Obsidian; данные не хранятся в checkout.

## Telegram

Токен нельзя отправлять в чат или записывать в `.env`. Сохрани его через скрытый ввод в macOS Keychain:

```sh
python3 -m fawn --library "~/Documents/Fawn Library" telegram set-token
python3 -m fawn --library "~/Documents/Fawn Library" telegram pair
python3 -m fawn --library "~/Documents/Fawn Library" telegram run
```

`pair` разрешает доступ только выбранному Telegram-чату. После проверки бот принимает ссылку с подписью или комментарий-ответ на сообщение со ссылкой.

## Видео

```sh
python3 -m fawn --library "~/Documents/Fawn Library" video prepare ITEM_ID
# Codex транскрибирует полученный файл локально
python3 -m fawn --library "~/Documents/Fawn Library" enrich ITEM_ID --transcript-file /local/transcript.txt
python3 -m fawn --library "~/Documents/Fawn Library" video cleanup ITEM_ID
```

Fawn не обходит DRM, закрытые аккаунты или авторизацию. `cleanup` откажется удалять временное аудио, пока транскрипт не записан в готовую заметку.

## Перенос

Для переезда скопируй внешнюю папку библиотеки (например, `Inbox/`, `Library/`, `Assets/` и `Fawn.base`). Затем выполни:

```sh
python3 -m fawn --library "~/Documents/Fawn Library" rebuild
```

Файл `System/fawn.sqlite` переносить необязательно. Команда `python3 -m fawn --library "~/Documents/Fawn Library" export` дополнительно создаёт переносимый `Exports/fawn.jsonl`.
