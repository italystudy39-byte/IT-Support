"""База знаний: общий интерфейс и сборка из нескольких источников."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass
class KBArticle:
    title: str
    text: str
    source: str  # local / notion / confluence
    url: str = ""
    score: float = 0.0


class KnowledgeBase(Protocol):
    async def search(self, query: str, limit: int = 3) -> list[KBArticle]: ...


class CompositeKB:
    """Опрашивает все подключённые источники параллельно и сливает результаты."""

    def __init__(self, sources: list[KnowledgeBase]):
        self.sources = sources

    async def search(self, query: str, limit: int = 4) -> list[KBArticle]:
        results = await asyncio.gather(
            *(s.search(query, limit) for s in self.sources), return_exceptions=True
        )
        merged: list[KBArticle] = []
        for src, res in zip(self.sources, results):
            if isinstance(res, Exception):
                log.warning("KB source %s failed: %s", type(src).__name__, res)
                continue
            merged.extend(res)
        merged.sort(key=lambda a: a.score, reverse=True)
        return merged[:limit]


def build_kb(settings) -> CompositeKB:
    from .confluence import ConfluenceKB
    from .local import LocalMarkdownKB
    from .notion import NotionKB

    sources: list[KnowledgeBase] = []
    for name in settings.kb_sources:
        if name == "local":
            sources.append(LocalMarkdownKB(settings.kb_local_dir))
        elif name == "notion" and settings.notion_token:
            sources.append(NotionKB(settings.notion_token))
        elif name == "confluence" and settings.confluence_base_url and settings.confluence_api_token:
            sources.append(
                ConfluenceKB(
                    settings.confluence_base_url,
                    settings.confluence_email,
                    settings.confluence_api_token,
                    settings.confluence_space_key,
                )
            )
        else:
            log.warning("KB source %r skipped (unknown or not configured)", name)
    return CompositeKB(sources)
