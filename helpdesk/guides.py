"""Интерактивные гайды: сценарии по шагам, описанные в YAML.

Гайд ведётся детерминированно (без LLM): бот показывает один короткий шаг и
кнопки. LLM решает только, *какой* гайд запустить и с какими параметрами, и что
делать по итогам (закрыть вопрос или создать тикет). Так инструкции
предсказуемы и их легко правит IT-отдел, не трогая код.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Guide:
    id: str
    title: str
    description: str
    keywords: list[str]
    needs: list[str]  # параметры, которые нужно уточнить: os, office ...
    variants: dict[str, list[str]]  # windows/macos/any -> шаги
    category: str = "other"
    priority: str = "medium"

    def steps_for(self, os_name: str | None) -> list[str]:
        key = (os_name or "").lower()
        return self.variants.get(key) or self.variants.get("any") or next(iter(self.variants.values()))


class GuideLibrary:
    def __init__(self, directory: str):
        self.guides: dict[str, Guide] = {}
        root = Path(directory)
        for path in sorted(root.glob("*.y*ml")) if root.exists() else []:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            g = Guide(
                id=raw["id"],
                title=raw["title"],
                description=raw.get("description", ""),
                keywords=raw.get("keywords", []),
                needs=raw.get("needs", []),
                variants={k.lower(): v for k, v in raw["variants"].items()},
                category=raw.get("category", "other"),
                priority=raw.get("priority", "medium"),
            )
            self.guides[g.id] = g

    def get(self, guide_id: str) -> Guide | None:
        return self.guides.get(guide_id)

    def catalog(self) -> str:
        """Каталог для системного промпта (стабильный порядок — ради кеша)."""
        lines = []
        for g in sorted(self.guides.values(), key=lambda g: g.id):
            variants = ", ".join(sorted(g.variants))
            needs = ", ".join(g.needs) or "—"
            lines.append(
                f"- id=`{g.id}` — {g.title}. {g.description} Нужно уточнить: {needs}. Варианты: {variants}."
            )
        return "\n".join(lines) or "(гайдов нет)"


@dataclass
class GuideRun:
    """Состояние прохождения гайда конкретным сотрудником."""

    guide: Guide
    variant: str
    steps: list[str]
    index: int = 0
    log: list[str] = field(default_factory=list)

    @property
    def current(self) -> str:
        return self.steps[self.index]

    @property
    def is_last(self) -> bool:
        return self.index >= len(self.steps) - 1

    def header(self) -> str:
        return f"Шаг {self.index + 1} из {len(self.steps)}"

    def mark_done(self) -> None:
        self.log.append(f"[{self.guide.title}] {self.header()}: {first_line(self.current)} — выполнено")
        self.index += 1

    def summary(self, outcome: str) -> dict:
        return {
            "guide_id": self.guide.id,
            "variant": self.variant,
            "outcome": outcome,
            "steps_total": len(self.steps),
            "stopped_at_step": self.index + 1,
            "steps_done": self.log,
            "suggested_category": self.guide.category,
            "suggested_priority": self.guide.priority,
        }


def first_line(s: str) -> str:
    return s.strip().splitlines()[0][:120]
