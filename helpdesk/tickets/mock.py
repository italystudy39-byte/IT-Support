"""Заглушка тикет-системы: пишет заявки в JSONL-файл. Удобно для демо и тестов."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path

from . import Ticket, TicketDraft, render_description


class MockTickets:
    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = asyncio.Lock()

    async def create(self, draft: TicketDraft) -> Ticket:
        async with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            n = 1
            if self.path.exists():
                n = sum(1 for _ in self.path.open(encoding="utf-8")) + 1
            key = f"HELP-{n}"
            record = {"key": key, "created": time.strftime("%Y-%m-%d %H:%M:%S"), **asdict(draft),
                      "rendered": render_description(draft)}
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return Ticket(key=key, url=f"file://{self.path.resolve()}#{key}")
