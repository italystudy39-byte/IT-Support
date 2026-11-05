"""Локальная база знаний: папка с .md файлами.

Каждый файл режется на фрагменты по заголовкам "## ". Поиск — простой
лексический (пересечение основ слов, BM25-подобный вес). Для старта этого
достаточно; позже можно заменить на векторный поиск, не меняя интерфейс.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from . import KBArticle

_WORD = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]+")
_STOP = {
    "как", "что", "где", "или", "для", "это", "мне", "меня", "мой", "моя", "на", "не", "по",
    "в", "и", "с", "у", "к", "о", "а", "я", "the", "a", "to", "of", "is", "как-то", "нужно",
    "можно", "есть", "нет", "уже", "все", "его", "она", "они",
}


def _stems(text: str) -> list[str]:
    words = [w.lower().replace("ё", "е") for w in _WORD.findall(text)]
    # грубый стемминг: первые 5 символов хватает для русских словоформ
    return [w[:5] for w in words if w not in _STOP and len(w) > 1]


class LocalMarkdownKB:
    def __init__(self, directory: str):
        self.chunks: list[tuple[str, str, str, Counter]] = []  # (title, text, path, stems)
        self._load(Path(directory))
        n = max(len(self.chunks), 1)
        df: Counter = Counter()
        for *_, stems in self.chunks:
            df.update(set(stems))
        self.idf = {t: math.log(1 + n / c) for t, c in df.items()}

    def _load(self, root: Path) -> None:
        if not root.exists():
            return
        for path in sorted(root.rglob("*.md")):
            raw = path.read_text(encoding="utf-8")
            doc_title = path.stem
            m = re.search(r"^# (.+)$", raw, re.M)
            if m:
                doc_title = m.group(1).strip()
            parts = re.split(r"^## ", raw, flags=re.M)
            intro = parts[0]
            for part in parts[1:] or [intro]:
                head, _, body = part.partition("\n")
                title = f"{doc_title} — {head.strip()}" if parts[1:] else doc_title
                text = body.strip() if parts[1:] else intro.strip()
                stems = Counter(_stems(title + " " + title + " " + text))
                self.chunks.append((title, text, str(path), stems))

    async def search(self, query: str, limit: int = 3) -> list[KBArticle]:
        q = set(_stems(query))
        if not q:
            return []
        scored = []
        for title, text, path, stems in self.chunks:
            length = sum(stems.values()) or 1
            score = sum(
                self.idf.get(t, 0) * stems[t] * 2.2 / (stems[t] + 1.2 * (0.25 + 0.75 * length / 120))
                for t in q
                if t in stems
            )
            if score > 0:
                scored.append(KBArticle(title=title, text=text, source="local", url=path, score=score))
        scored.sort(key=lambda a: a.score, reverse=True)
        return scored[:limit]
