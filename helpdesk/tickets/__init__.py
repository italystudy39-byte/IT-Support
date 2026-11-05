"""Тикет-системы: общий интерфейс + фабрика."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class TicketDraft:
    summary: str
    description: str  # структурированное описание от агента
    category: str = "other"
    priority: str = "medium"  # low / medium / high / critical
    reporter_name: str = ""
    reporter_contact: str = ""  # email или @telegram
    environment: dict = field(default_factory=dict)  # os, office, device...
    steps_tried: list[str] = field(default_factory=list)
    transcript: str = ""  # вся переписка


@dataclass
class Ticket:
    key: str
    url: str = ""


class TicketSystem(Protocol):
    async def create(self, draft: TicketDraft) -> Ticket: ...


def render_description(d: TicketDraft) -> str:
    """Единое текстовое тело тикета: то, что увидит админ."""
    env = "\n".join(f"- {k}: {v}" for k, v in d.environment.items() if v) or "- не указано"
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(d.steps_tried, 1)) or "— (сотрудник ничего не пробовал)"
    return (
        f"{d.description}\n\n"
        f"*Сотрудник:* {d.reporter_name or '—'} ({d.reporter_contact or '—'})\n"
        f"*Категория:* {d.category} | *Приоритет:* {d.priority}\n\n"
        f"*Окружение:*\n{env}\n\n"
        f"*Что уже попробовали с ботом:*\n{steps}\n\n"
        f"*История переписки:*\n{{noformat}}\n{d.transcript}\n{{noformat}}"
    )


def build_ticket_system(settings) -> TicketSystem:
    if settings.tickets_backend == "jira" and settings.jira_api_token:
        from .jira import JiraServiceDesk

        return JiraServiceDesk(
            settings.jira_base_url,
            settings.jira_email,
            settings.jira_api_token,
            settings.jira_service_desk_id,
            settings.jira_request_type_id,
        )
    from .mock import MockTickets

    return MockTickets(settings.mock_tickets_file)
