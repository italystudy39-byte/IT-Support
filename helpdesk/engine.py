"""Движок диалога, не зависящий от мессенджера.

Получает текст или нажатие кнопки, вызывает агента / ведёт гайд и
возвращает список исходящих сообщений с кнопками. Telegram, Slack, Teams
или CLI только отрисовывают результат.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .agent import AskUser, Event, HelpdeskAgent, Session, StartGuide, TextOut, TicketCreated
from .guides import GuideLibrary, GuideRun, first_line
from .tickets import TicketDraft, render_description

log = logging.getLogger(__name__)

# callback_data кнопок
BTN_NEXT = "g:next"
BTN_OK = "g:ok"
BTN_FAIL = "g:fail"
BTN_HUMAN = "human"
BTN_NEW = "new"
OPT_PREFIX = "opt:"


@dataclass
class Button:
    label: str
    data: str


@dataclass
class Out:
    text: str
    buttons: list[list[Button]] = field(default_factory=list)  # ряды кнопок


EscalationHook = Callable[[Session, TicketCreated], Awaitable[None]]


class Engine:
    def __init__(self, agent: HelpdeskAgent, guides: GuideLibrary, on_escalation: EscalationHook | None = None):
        self.agent = agent
        self.guides = guides
        self.on_escalation = on_escalation
        self.sessions: dict[str, Session] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    def session(self, user_id: str, name: str = "", contact: str = "") -> Session:
        s = self.sessions.get(user_id)
        if s is None:
            s = self.sessions[user_id] = Session(user_id=user_id, name=name, contact=contact)
        return s

    def lock(self, user_id: str) -> asyncio.Lock:
        return self.locks.setdefault(user_id, asyncio.Lock())

    def reset(self, user_id: str) -> None:
        self.sessions.pop(user_id, None)

    # ---------------------------------------------------------------------

    async def on_text(self, s: Session, text: str) -> list[Out]:
        if s.profile.pop("escalate_next", False):  # нажали «Позвать специалиста» до описания проблемы
            s.note("Сотрудник", text)
            return await self._escalate(s, noted=True, problem=text)
        if s.guide_run:  # человек написал текст посреди гайда — прерываем гайд и отдаём агенту
            run: GuideRun = s.guide_run
            s.guide_run = None
            s.note("Сотрудник", text)
            s.steps_tried.extend(run.log)
            summary = run.summary("interrupted")
            summary["user_message"] = text
            return await self._render(s, await self.agent.resolve_pending(s, summary))
        return await self._render(s, await self.agent.user_message(s, text))

    async def on_button(self, s: Session, data: str) -> list[Out]:
        if data == BTN_NEW:
            self.reset(s.user_id)
            return [Out("Начнём заново. Опишите, что случилось 🙂")]
        if data == BTN_HUMAN:
            return await self._escalate(s)
        if data.startswith(OPT_PREFIX):
            if not (s.pending and s.pending.name == "ask_user"):
                return [Out("Этот вопрос уже неактуален — напишите, пожалуйста, текстом.")]
            opts = s.pending.data.get("options", [])
            i = int(data[len(OPT_PREFIX):])
            answer = opts[i] if 0 <= i < len(opts) else data
            return await self._render(s, await self.agent.resolve_pending(s, {"answer": answer}))
        if data in (BTN_NEXT, BTN_OK, BTN_FAIL):
            return await self._guide_button(s, data)
        return []

    # ---------------------------------------------------------------------

    async def _guide_button(self, s: Session, data: str) -> list[Out]:
        run: GuideRun | None = s.guide_run
        if run is None:
            return [Out("Гайд уже завершён. Если вопрос остался — напишите его.")]
        if data == BTN_NEXT:
            s.note("Сотрудник", f"[{run.header()}] Готово")
            run.mark_done()
            if run.index < len(run.steps):
                return [self._guide_step(run)]
            data = BTN_FAIL  # шаги кончились, а «заработало» не нажали
        s.guide_run = None
        outcome = "resolved" if data == BTN_OK else "not_resolved"
        s.note("Сотрудник", f"[{run.header() if run.index < len(run.steps) else 'конец гайда'}] "
                             f"{'Заработало' if outcome == 'resolved' else 'Не помогло'}")
        s.steps_tried.extend(run.log)
        if outcome == "not_resolved" and run.index < len(run.steps):
            s.steps_tried.append(f"[{run.guide.title}] {run.header()}: {first_line(run.current)} — не получилось")
        return await self._render(s, await self.agent.resolve_pending(s, run.summary(outcome)))

    def _guide_step(self, run: GuideRun) -> Out:
        text = f"**{run.guide.title}** · {run.header()}\n\n{run.current.strip()}"
        if run.is_last:
            rows = [[Button("🎉 Заработало", BTN_OK), Button("❌ Не помогло", BTN_FAIL)]]
        else:
            rows = [
                [Button("✅ Сделал, что дальше?", BTN_NEXT)],
                [Button("🎉 Уже заработало", BTN_OK), Button("❌ Не помогло", BTN_FAIL)],
            ]
        rows.append([Button("🆘 Позвать специалиста", BTN_HUMAN)])
        return Out(text, rows)

    async def _escalate(self, s: Session, noted: bool = False, problem: str = "") -> list[Out]:
        """Кнопка «Позвать специалиста» — работает в любой момент."""
        if not any(l.startswith("Сотрудник: ") for l in s.transcript):
            s.profile["escalate_next"] = True
            return [Out("Коротко опишите, что случилось, и я сразу передам заявку специалисту 👇")]
        if s.guide_run:
            run: GuideRun = s.guide_run
            s.guide_run = None
            s.steps_tried.extend(run.log)
            result = run.summary("escalated")
        else:
            result = None
        if not noted:
            s.note("Сотрудник", "Нажал «Позвать специалиста»")
        request = {"answer": "Сотрудник нажал «Позвать специалиста». Сразу создай заявку create_ticket по всему диалогу."}
        if problem:
            request["answer"] += f" Описание проблемы от сотрудника: {problem}"
        if result:
            result.update(request)
        if s.pending:
            events = await self.agent.resolve_pending(s, result or request, already_noted=True)
        else:
            s.history.append({"role": "user", "content": request["answer"]})
            events = await self.agent._loop(s)  # noqa: SLF001
        if not any(isinstance(e, TicketCreated) for e in events):
            events.append(await self._direct_ticket(s))  # страховка: заявка создаётся в любом случае
        return await self._render(s, events)

    async def _direct_ticket(self, s: Session) -> Event:
        first = next((l.split(": ", 1)[1] for l in s.transcript if l.startswith("Сотрудник: ")), "Обращение в IT")
        draft = TicketDraft(
            summary=first[:100],
            description="Сотрудник запросил помощь специалиста через бота.",
            reporter_name=s.name,
            reporter_contact=s.contact,
            environment={"ОС": s.profile.get("os", ""), "Офис": s.profile.get("office", "")},
            steps_tried=list(s.steps_tried),
            transcript="\n".join(s.transcript),
        )
        try:
            ticket = await self.agent.tickets.create(draft)
        except Exception:
            log.exception("direct ticket failed")
            return TextOut("Не получилось создать заявку автоматически. Позвоните, пожалуйста, в IT-отдел.")
        s.last_ticket = ticket.key
        return TicketCreated(ticket, draft)

    async def _render(self, s: Session, events: list[Event]) -> list[Out]:
        outs: list[Out] = []
        for e in events:
            if isinstance(e, TextOut):
                outs.append(Out(e.text))
            elif isinstance(e, AskUser):
                rows = [[Button(o, f"{OPT_PREFIX}{i}")] for i, o in enumerate(e.options)]
                rows.append([Button("🆘 Позвать специалиста", BTN_HUMAN)])
                outs.append(Out(e.question, rows))
            elif isinstance(e, StartGuide):
                guide = self.guides.get(e.guide_id)
                variant = e.os if e.os in guide.variants else ("any" if "any" in guide.variants else next(iter(guide.variants)))
                run = GuideRun(guide=guide, variant=variant, steps=guide.steps_for(variant))
                s.guide_run = run
                s.note("Бот", f"Запущен гайд «{guide.title}» ({variant})")
                outs.append(self._guide_step(run))
            elif isinstance(e, TicketCreated):
                link = e.ticket.url if e.ticket.url.startswith("http") else ""
                outs.append(
                    Out(
                        f"📝 Заявка **{e.ticket.key}** создана и передана специалисту"
                        + (f": {link}" if link else ".")
                        + "\nВся переписка и выполненные шаги уже приложены — повторять ничего не нужно.",
                        [[Button("Новый вопрос", BTN_NEW)]],
                    )
                )
                if self.on_escalation:
                    try:
                        await self.on_escalation(s, e)
                    except Exception:
                        log.exception("escalation hook failed")
        return outs


def escalation_text(s: Session, e: TicketCreated) -> str:
    """Текст уведомления для чата IT-отдела."""
    return f"🆘 Новая заявка {e.ticket.key} от {s.name or s.user_id} ({s.contact})\n\n" + render_description(e.draft)[:3500]
