"""Адаптер Confluence Cloud: CQL-поиск по пространству IT."""
from __future__ import annotations

import html
import re

import httpx

from . import KBArticle

_TAGS = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    s = re.sub(r"</(p|li|h\d|tr|div)>", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(_TAGS.sub("", s))).strip()


class ConfluenceKB:
    def __init__(self, base_url: str, email: str, api_token: str, space_key: str = "", timeout: float = 15.0):
        self.base = base_url.rstrip("/")
        self.auth = (email, api_token)
        self.space = space_key
        self.timeout = timeout

    async def search(self, query: str, limit: int = 3) -> list[KBArticle]:
        q = query.replace('"', " ")
        cql = f'type=page AND text ~ "{q}"'
        if self.space:
            cql += f' AND space="{self.space}"'
        async with httpx.AsyncClient(auth=self.auth, timeout=self.timeout) as http:
            r = await http.get(
                f"{self.base}/wiki/rest/api/content/search",
                params={"cql": cql, "limit": limit, "expand": "body.storage"},
            )
            r.raise_for_status()
            data = r.json()
            out = []
            for i, page in enumerate(data.get("results", [])):
                body = page.get("body", {}).get("storage", {}).get("value", "")
                link = self.base + "/wiki" + page.get("_links", {}).get("webui", "")
                out.append(
                    KBArticle(
                        title=page.get("title", ""),
                        text=_strip_html(body)[:4000],
                        source="confluence",
                        url=link,
                        score=10 - i,
                    )
                )
            return out
