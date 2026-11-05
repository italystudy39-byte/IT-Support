"""Настройки из переменных окружения (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv необязателен
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    # claude | gemini | auto (gemini, если указан GEMINI_API_KEY, иначе claude)
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "auto").lower())
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-flash-lite-latest"))
    gemini_fallback_models: list[str] = field(default_factory=lambda: [
        m.strip() for m in _env("GEMINI_FALLBACK_MODELS", "gemini-flash-latest").split(",")
        if m.strip()])
    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-opus-5-5"))
    claude_effort: str = field(default_factory=lambda: _env("CLAUDE_EFFORT", "low"))

    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    it_support_chat_id: str = field(default_factory=lambda: _env("IT_SUPPORT_CHAT_ID"))

    kb_sources: list[str] = field(
        default_factory=lambda: [s.strip() for s in _env("KB_SOURCES", "local").split(",") if s.strip()]
    )
    kb_local_dir: str = field(default_factory=lambda: _env("KB_LOCAL_DIR", "data/kb"))
    notion_token: str = field(default_factory=lambda: _env("NOTION_TOKEN"))
    confluence_base_url: str = field(default_factory=lambda: _env("CONFLUENCE_BASE_URL"))
    confluence_email: str = field(default_factory=lambda: _env("CONFLUENCE_EMAIL"))
    confluence_api_token: str = field(default_factory=lambda: _env("CONFLUENCE_API_TOKEN"))
    confluence_space_key: str = field(default_factory=lambda: _env("CONFLUENCE_SPACE_KEY"))

    tickets_backend: str = field(default_factory=lambda: _env("TICKETS_BACKEND", "mock"))
    jira_base_url: str = field(default_factory=lambda: _env("JIRA_BASE_URL"))
    jira_email: str = field(default_factory=lambda: _env("JIRA_EMAIL"))
    jira_api_token: str = field(default_factory=lambda: _env("JIRA_API_TOKEN"))
    jira_service_desk_id: str = field(default_factory=lambda: _env("JIRA_SERVICE_DESK_ID", "1"))
    jira_request_type_id: str = field(default_factory=lambda: _env("JIRA_REQUEST_TYPE_ID", "10"))
    mock_tickets_file: str = field(default_factory=lambda: _env("MOCK_TICKETS_FILE", "data/tickets.jsonl"))

    def __post_init__(self) -> None:
        if self.llm_provider == "auto":
            self.llm_provider = "gemini" if self.gemini_api_key else "claude"

    guides_dir: str = field(default_factory=lambda: _env("GUIDES_DIR", "data/guides"))
