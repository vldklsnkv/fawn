"""English Space taxonomy without changing original source metadata."""
import unicodedata

from .taxonomy_data import CATEGORY_NAMES, TAG_NAMES


def _key(value):
    return unicodedata.normalize("NFKC", value).strip().lstrip("#").casefold()


_CATEGORIES = {_key(k): v for k, v in CATEGORY_NAMES.items()}
_CATEGORIES.update({_key(v): v for v in CATEGORY_NAMES.values()})
_TAGS = {_key(k): v for k, v in TAG_NAMES.items()}


def english_category(value):
    text = unicodedata.normalize("NFKC", value).strip()
    result = _CATEGORIES.get(_key(text), text)
    if not result or not result.isascii():
        raise ValueError("Category needs an English translation")
    return result


def english_tag(value):
    text = unicodedata.normalize("NFKC", value).strip().lstrip("#")
    result = _TAGS.get(_key(text), text)
    if not result or not result.isascii():
        raise ValueError("Tag needs an English translation")
    return result.casefold()
