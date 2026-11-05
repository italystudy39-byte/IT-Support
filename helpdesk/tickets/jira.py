"""Jira Service Management (Service Desk) через REST API.

POST /rest/servicedeskapi/request — создаёт заявку от имени сотрудника
(raiseOnBehalfOf), если у нас есть его email; иначе от имени сервисной учётки.
Полная переписка дополнительно кладётся внутренним комментарием, чтобы
сотрудник не видел служебных пометок.
"""
from __future__ import annotations

import logging

import httpx

from . import Ticket, TicketDraft, render_description

log = logging.getLogger(__name__)

PRIORITY_MAP = {"low": "Low", "medium": "Medium", "high": "High", "critical": "Highest"}


class JiraServiceDesk:
    def __init__(self, base_url: str, email: str, api_token: str, service_desk_id: str, request_type_id: str):
        self.base = base_url.rstrip("/")
        self.auth = (email, api_token)
        self.service_desk_id = service_desk_id
        self.request_type_id = request_type_id

    async def create(self, draft: TicketDraft) -> Ticket:
        payload: dict = {
            "serviceDeskId": self.service_desk_id,
            "requestTypeId": self.request_type_id,
            "requestFieldValues": {
                "summary": draft.summary[:250],
                "description": render_description(draft),
            },
        }
        if "@" in draft.reporter_contact and not draft.reporter_contact.startswith("@"):
            payload["raiseOnBehalfOf"] = draft.reporter_contact
        async with httpx.AsyncClient(auth=self.auth, timeout=20, headers={"Accept": "application/json"}) as http:
            r = await http.post(f"{self.base}/rest/servicedeskapi/request", json=payload)
            if r.status_code == 400 and "raiseOnBehalfOf" in payload:
                # сотрудника нет в Service Desk — создаём от сервисной учётки
                payload.pop("raiseOnBehalfOf")
                r = await http.post(f"{self.base}/rest/servicedeskapi/request", json=payload)
            r.raise_for_status()
            data = r.json()
            key = data["issueKey"]
            # Приоритет в JSM обычно задаётся через обычный Jira API
            try:
                await http.put(
                    f"{self.base}/rest/api/2/issue/{key}",
                    json={"fields": {"priority": {"name": PRIORITY_MAP.get(draft.priority, "Medium")}}},
                )
            except httpx.HTTPError as e:  # не критично
                log.info("priority not set for %s: %s", key, e)
            url = data.get("_links", {}).get("web") or f"{self.base}/browse/{key}"
            return Ticket(key=key, url=url)
