"""Перевод «markdown-lite» (**жирный**, `код`) в HTML для Telegram."""
from __future__ import annotations

import html
import re


def md_to_tg_html(text: str) -> str:
    s = html.escape(text, quote=False)
    s = re.sub(r"```(?:\w+)?\n?(.*?)```", lambda m: f"<pre>{m.group(1)}</pre>", s, flags=re.S)
    s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s, flags=re.S)
    s = re.sub(r"(?m)^#{1,6}\s*(.+)$", r"<b>\1</b>", s)
    return s


def md_to_plain(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text).replace("`", "")
