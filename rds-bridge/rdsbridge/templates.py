"""Text templates and RDS-safe text cleanup.

Templates use Python format syntax with a few safety tweaks:

    {title}            field value ('' if missing; names are case-insensitive)
    {title:.30}        at most 30 characters
    {artist!u}         UPPER CASE   (!l lower, !t Title Case)

A template *list* means "use the first template whose fields are all present",
e.g. ["{title} by {artist}", "{title}"] falls back when there is no artist.
"""

from __future__ import annotations

import re
import string
import unicodedata


class _Formatter(string.Formatter):
    def get_field(self, field_name, args, kwargs):
        # No attribute access / indexing: field names are plain keys.
        return self.get_value(field_name, args, kwargs), field_name

    def get_value(self, key, args, kwargs):
        if isinstance(key, int):
            return ""
        value = kwargs.get(str(key).lower(), "")
        return "" if value is None else value

    def convert_field(self, value, conversion):
        value = "" if value is None else str(value)
        if conversion == "u":
            return value.upper()
        if conversion == "l":
            return value.lower()
        if conversion == "t":
            return value.title()
        if conversion in (None, "s", "r", "a"):
            return value
        raise ValueError(f"unknown conversion !{conversion} (use !u, !l or !t)")

    def format_field(self, value, format_spec):
        return format("" if value is None else str(value), format_spec)


_fmt = _Formatter()


def fields_in(template: str) -> list:
    return [f.lower() for _, f, _, _ in string.Formatter().parse(template) if f]


def render(template: str, ctx: dict) -> str:
    return _fmt.vformat(template, (), ctx)


def render_first(templates, ctx: dict) -> str:
    if isinstance(templates, str):
        templates = [templates]
    for t in templates or []:
        if all(str(ctx.get(f, "") or "").strip() for f in fields_in(t)):
            return render(t, ctx)
    return ""


_PUNCT = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
    "…": "...", " ": " ", "«": '"', "»": '"',
    "ß": "ss", "Æ": "AE", "æ": "ae", "Œ": "OE", "œ": "oe",
    "Ø": "O", "ø": "o", "Ł": "L", "ł": "l", "Ð": "D", "ð": "d",
}
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SPACES = re.compile(r"\s+")


def clean(text: str, ascii_only: bool = True) -> str:
    """Single line, no control characters, optionally plain ASCII."""
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = "".join(_PUNCT.get(ch, ch) for ch in text)
    text = _CTRL.sub(" ", text)
    if ascii_only:
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _SPACES.sub(" ", text).strip()


def truncate(text: str, limit: int | None) -> str:
    if not limit or len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space >= limit * 0.6 and not text[limit:limit + 1].isspace():
        cut = cut[:space]
    return cut.rstrip(" -,;:/")
