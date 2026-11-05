"""Сборка всех компонентов."""
from __future__ import annotations

from .agent import HelpdeskAgent
from .config import Settings
from .engine import Engine, EscalationHook
from .guides import GuideLibrary
from .kb import build_kb
from .llm import ClaudeProvider, GeminiProvider, LLMProvider
from .tickets import build_ticket_system


def build_llm(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            raise SystemExit("LLM_PROVIDER=gemini, но GEMINI_API_KEY в .env не указан")
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model,
                              fallback_models=settings.gemini_fallback_models)
    return ClaudeProvider(model=settings.claude_model, effort=settings.claude_effort)


def build_engine(settings: Settings | None = None, on_escalation: EscalationHook | None = None,
                 llm: LLMProvider | None = None) -> Engine:
    settings = settings or Settings()
    guides = GuideLibrary(settings.guides_dir)
    agent = HelpdeskAgent(
        llm=llm or build_llm(settings),
        kb=build_kb(settings),
        tickets=build_ticket_system(settings),
        guides=guides,
    )
    return Engine(agent, guides, on_escalation=on_escalation)
