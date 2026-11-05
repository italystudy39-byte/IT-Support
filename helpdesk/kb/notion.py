"""Адаптер Notion: поиск страниц через /v1/search и чтение их текста.

Интеграции Notion нужно выдать доступ к нужным страницам/базам
(Share -> Connections -> ваша интеграция).
"""
from __future__ import annotations

import httpx

from . import KBArticle

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"


def _rich_text(rt: list[dict]) -> str:
    return "".join(x.get("plain_text", "") for x in rt or [])


class NotionKB:
    def __init__(self, token: str, timeout: float = 15.0):
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        }
        self.timeout = timeout

    async def search(self, query: str, limit: int = 3) -> list[KBArticle]:
        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as http:
            r = await http.post(
                f"{API}/search",
                json={"query": query, "filter": {"property": "object", "value": "page"}, "page_size": limit},
            )
            r.raise_for_status()
            out = []
            for i, page in enumerate(r.json().get("results", [])[:limit]):
                title = "Без названия"
                for prop in page.get("properties", {}).values():
                    if prop.get("type") == "title":
                        title = _rich_text(prop["title"]) or title
                text = await self._page_text(http, page["id"])
                out.append(
                    KBArticle(title=title, text=text[:4000], source="notion", url=page.get("url", ""), score=10 - i)
                )
            return out

    async def _page_text(self, http: httpx.AsyncClient, page_id: str) -> str:
        r = await http.get(f"{API}/blocks/{page_id}/children", params={"page_size": 100})
        r.raise_for_status()
        lines = []
        for b in r.json().get("results", []):
            payload = b.get(b.get("type", ""), {})
            if isinstance(payload, dict) and "rich_text" in payload:
                lines.append(_rich_text(payload["rich_text"]))
        return "\n".join(l for l in lines if l)
