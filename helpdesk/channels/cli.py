"""Консольный режим для проверки без Telegram: python -m helpdesk.channels.cli

Кнопки показываются цифрами: введите номер, чтобы «нажать».
"""
from __future__ import annotations

import asyncio
import logging

from ..app import build_engine
from ..config import Settings
from ..engine import BTN_HUMAN, Out
from .formatting import md_to_plain


def _print(outs: list[Out]) -> list[str]:
    actions: list[str] = []
    for out in outs:
        print("\n🤖 " + md_to_plain(out.text))
        for row in out.buttons:
            for b in row:
                actions.append(b.data)
                print(f"   [{len(actions)}] {b.label}")
    return actions


async def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    engine = build_engine(Settings())
    s = engine.session("cli-user", "Тестовый Сотрудник", "test@example.com")
    print("IT-Helpdesk (CLI). Пишите вопрос; цифра — нажать кнопку; /human — специалист; /quit — выход.")
    actions: list[str] = []
    while True:
        try:
            line = (await asyncio.to_thread(input, "\n👤 ")).strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not line:
            continue
        if line == "/quit":
            return
        if line == "/human":
            outs = await engine.on_button(s, BTN_HUMAN)
        elif line.isdigit() and actions and 1 <= int(line) <= len(actions):
            outs = await engine.on_button(s, actions[int(line) - 1])
        else:
            outs = await engine.on_text(s, line)
        if any(o.text.startswith("Начнём заново") for o in outs):
            s = engine.session("cli-user", "Тестовый Сотрудник", "test@example.com")
        actions = _print(outs)


if __name__ == "__main__":
    asyncio.run(main())
