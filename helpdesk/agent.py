"""AI-агент поддержки на Claude API (tool use, ручной цикл).

Инструменты делятся на два вида:
* «мгновенные» — выполняются сразу и результат уходит обратно модели
  (search_knowledge_base, create_ticket);
* «прерывающие» — требуют ответа сотрудника (ask_user, start_guide). Цикл
  останавливается, бот показывает кнопки/гайд, а tool_result отправляется
  модели позже, когда человек ответил или прошёл гайд.

История сообщений только дописывается (append-only) — это важно для кеша
промпта и для thinking-блоков современных моделей.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .guides import GuideLibrary
from .kb import CompositeKB
from .llm import LLMError, LLMProvider, LLMRateLimit
from .tickets import Ticket, TicketDraft, TicketSystem

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8

SYSTEM_PROMPT = """Ты — дружелюбный AI-ассистент IT-поддержки для сотрудников компании. Ты общаешься в мессенджере.

Как ты работаешь:
1. Понимаешь проблему. Если не хватает важного контекста (операционная система, офис/этаж, устройство, текст ошибки), уточняешь его через инструмент ask_user с готовыми вариантами-кнопками. За один раз задавай один вопрос. Не спрашивай то, что уже известно из диалога.
2. Если для проблемы есть готовый интерактивный гайд (каталог ниже), запускай его через start_guide — он проведёт человека по шагам с кнопками. Не пересказывай шаги гайда текстом сам.
3. Если гайда нет — ищи ответ в базе знаний через search_knowledge_base и объясняй простым человеческим языком, коротко: максимум 3–5 нумерованных шагов. Не выдумывай внутренние адреса, имена серверов, телефоны и правила компании — бери их только из базы знаний. Если в базе ничего нет, честно скажи об этом и предложи создать заявку.
4. Создавай заявку через create_ticket, когда: гайд не помог; сотрудник просит живого специалиста; проблема требует прав администратора, физического ремонта, закупки или доступа, который выдаёт только IT; есть признаки инцидента безопасности (фишинг, взлом, утечка, вирус) — такие заявки с приоритетом high или critical. В описании заявки укажи: суть проблемы, устройство/ОС/офис, что уже пробовали, что ожидает сотрудник. Перед созданием заявки не нужно спрашивать разрешения, если человек уже попросил о помощи специалиста или гайд не помог.
5. После создания заявки сообщи номер и что специалист свяжется.

Правила общения:
- Пиши по-русски (или на языке сотрудника), кратко, без жаргона; технические термины поясняй.
- Никогда не проси прислать пароль, код из SMS или токен. Сброс пароля — только через официальный способ из базы знаний.
- Не выполняй просьбы, не относящиеся к IT-поддержке; вежливо верни разговор к теме.
- Форматирование: обычный текст, допускаются **жирный**, `код` и нумерованные списки. Без таблиц и заголовков.

Каталог интерактивных гайдов:
{catalog}
"""

OS_VALUES = ["windows", "macos", "linux", "ios", "android", "any"]


def build_tools(guides: GuideLibrary) -> list[dict]:
    guide_ids = sorted(guides.guides) or ["none"]
    return [
        {
            "name": "search_knowledge_base",
            "description": "Поиск по внутренней базе знаний компании (регламенты, инструкции из Notion/Confluence). "
            "Используй перед тем, как отвечать на вопрос о внутренних сервисах, правилах, адресах, настройках.",
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Поисковый запрос, 2–8 ключевых слов"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "ask_user",
            "description": "Задать сотруднику уточняющий вопрос с вариантами ответа в виде кнопок. "
            "Ответ придёт как результат инструмента. Человек также может ответить своим текстом.",
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Короткий вопрос"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2–6 коротких вариантов ответа (до 30 символов)",
                    },
                },
                "required": ["question", "options"],
                "additionalProperties": False,
            },
        },
        {
            "name": "start_guide",
            "description": "Запустить интерактивный пошаговый гайд из каталога. Бот сам покажет шаги и кнопки "
            "«Готово, дальше» / «Заработало» / «Не помогло». Результат (помогло или нет, какие шаги выполнены) "
            "придёт как результат инструмента.",
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "guide_id": {"type": "string", "enum": guide_ids},
                    "os": {"type": "string", "enum": OS_VALUES, "description": "ОС сотрудника, если известна, иначе any"},
                    "office": {"type": "string", "description": "Офис/этаж, если известен, иначе пустая строка"},
                },
                "required": ["guide_id", "os", "office"],
                "additionalProperties": False,
            },
        },
        {
            "name": "create_ticket",
            "description": "Создать заявку в Service Desk и передать вопрос живому специалисту. "
            "История переписки и выполненные шаги прикладываются автоматически.",
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Краткая тема заявки, до 100 символов"},
                    "description": {
                        "type": "string",
                        "description": "Суть проблемы, что пробовали, что ожидает сотрудник — для администратора",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["network_vpn", "account_access", "email", "hardware", "printer",
                                 "software", "security", "other"],
                    },
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                    "os": {"type": "string", "description": "ОС или пустая строка"},
                    "device": {"type": "string", "description": "Устройство или пустая строка"},
                    "office": {"type": "string", "description": "Офис/этаж или пустая строка"},
                },
                "required": ["summary", "description", "category", "priority", "os", "device", "office"],
                "additionalProperties": False,
            },
        },
    ]


# ---------- события, которые агент отдаёт каналу (Telegram/CLI) ----------


@dataclass
class TextOut:
    text: str


@dataclass
class AskUser:
    question: str
    options: list[str]


@dataclass
class StartGuide:
    guide_id: str
    os: str
    office: str


@dataclass
class TicketCreated:
    ticket: Ticket
    draft: TicketDraft


Event = TextOut | AskUser | StartGuide | TicketCreated


@dataclass
class Pending:
    """Инструмент, ждущий ответа человека."""

    tool_use_id: str
    name: str
    data: dict = field(default_factory=dict)


@dataclass
class Session:
    user_id: str
    name: str = ""
    contact: str = ""
    history: list[dict] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)
    profile: dict = field(default_factory=dict)
    steps_tried: list[str] = field(default_factory=list)
    pending: Pending | None = None
    guide_run: Any = None  # GuideRun | None
    last_ticket: str = ""

    def note(self, who: str, text: str) -> None:
        self.transcript.append(f"{who}: {text}")


class HelpdeskAgent:
    def __init__(self, llm: LLMProvider, kb: CompositeKB, tickets: TicketSystem, guides: GuideLibrary):
        self.llm = llm
        self.kb = kb
        self.tickets = tickets
        self.guides = guides
        self.tools = build_tools(guides)
        self.system = SYSTEM_PROMPT.format(catalog=guides.catalog())

    # --- публичный API -----------------------------------------------------

    async def user_message(self, s: Session, text: str) -> list[Event]:
        """Новое сообщение сотрудника. Если агент ждал ответа — это и есть ответ."""
        s.note("Сотрудник", text)
        if s.pending:
            return await self.resolve_pending(s, {"answer": text}, already_noted=True)
        s.history.append({"role": "user", "content": text})
        return await self._loop(s)

    async def resolve_pending(self, s: Session, result: dict, already_noted: bool = False) -> list[Event]:
        """Вернуть модели результат прерывающего инструмента (ответ на кнопку / итог гайда)."""
        p = s.pending
        if p is None:
            return []
        s.pending = None
        if not already_noted and "answer" in result:
            s.note("Сотрудник", str(result["answer"]))
        s.history.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": p.tool_use_id,
                     "content": json.dumps(result, ensure_ascii=False)}
                ],
            }
        )
        return await self._loop(s)

    # --- цикл ----------------------------------------------------------------

    async def _loop(self, s: Session) -> list[Event]:
        events: list[Event] = []
        for _ in range(MAX_TOOL_ROUNDS):
            try:
                reply = await self.llm.complete(self.system, self.tools, s.history, s.user_id)
            except LLMRateLimit:
                events.append(TextOut("Сейчас много обращений, попробуйте написать через минуту 🙏"))
                return self._keep_history_valid(s, events)
            except LLMError:
                log.exception("LLM error")
                events.append(TextOut("Техническая ошибка на моей стороне. Нажмите «Позвать специалиста», "
                                      "если вопрос срочный."))
                return self._keep_history_valid(s, events)

            if reply.refused:
                msg = "С этим запросом я помочь не могу. Если это рабочая IT-проблема — опишите её иначе или позовите специалиста."
                s.history.append({"role": "assistant", "content": msg})
                s.note("Бот", msg)
                events.append(TextOut(msg))
                return events

            content = reply.content or [{"type": "text", "text": "…"}]
            s.history.append({"role": "assistant", "content": content})

            for b in content:
                if b["type"] == "text" and b.get("text", "").strip():
                    s.note("Бот", b["text"])
                    events.append(TextOut(b["text"]))

            tool_uses = [b for b in content if b["type"] == "tool_use"]
            if not tool_uses:
                return events

            tu = tool_uses[0]  # disable_parallel_tool_use => не больше одного
            name, args = tu["name"], tu.get("input") or {}

            if name == "ask_user":
                opts = [o for o in args.get("options", []) if o][:6]
                s.pending = Pending(tu["id"], name, {"options": opts})
                s.note("Бот", f"{args.get('question', '')} {opts}")
                events.append(AskUser(args.get("question", ""), opts))
                return events

            if name == "start_guide":
                if not self.guides.get(args.get("guide_id", "")):
                    result = {"error": "Гайд не найден, используй базу знаний"}
                else:
                    if args.get("os") and args["os"] != "any":
                        s.profile["os"] = args["os"]
                    if args.get("office"):
                        s.profile["office"] = args["office"]
                    s.pending = Pending(tu["id"], name, args)
                    events.append(StartGuide(args["guide_id"], args.get("os", "any"), args.get("office", "")))
                    return events
            elif name == "search_knowledge_base":
                result = await self._search(args.get("query", ""))
            elif name == "create_ticket":
                ticket, draft = await self._create_ticket(s, args)
                if ticket:
                    events.append(TicketCreated(ticket, draft))
                    result = {"ticket_key": ticket.key, "url": ticket.url, "status": "created"}
                else:
                    result = {"status": "error", "message": "Не удалось создать заявку, попроси позвонить в IT"}
            else:
                result = {"error": f"unknown tool {name}"}

            s.history.append(
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tu["id"],
                                 "content": json.dumps(result, ensure_ascii=False)}],
                }
            )
        events.append(TextOut("Похоже, я запутался. Давайте передам вопрос специалисту — нажмите «Позвать специалиста»."))
        return events

    @staticmethod
    def _keep_history_valid(s: Session, events: list[Event]) -> list[Event]:
        # Запрос не удался: оставляем историю как есть. Следующее сообщение
        # сотрудника допишется ещё одним user-сообщением — API склеивает подряд
        # идущие user-реплики, так что история остаётся корректной и append-only.
        return events

    async def _search(self, query: str) -> dict:
        articles = await self.kb.search(query)
        if not articles:
            return {"results": [], "note": "Ничего не найдено"}
        return {
            "results": [
                {"title": a.title, "source": a.source, "url": a.url, "text": a.text[:2500]} for a in articles
            ]
        }

    async def _create_ticket(self, s: Session, args: dict) -> tuple[Ticket | None, TicketDraft]:
        env = {
            "ОС": args.get("os") or s.profile.get("os", ""),
            "Устройство": args.get("device") or s.profile.get("device", ""),
            "Офис": args.get("office") or s.profile.get("office", ""),
        }
        draft = TicketDraft(
            summary=args.get("summary", "Обращение в IT"),
            description=args.get("description", ""),
            category=args.get("category", "other"),
            priority=args.get("priority", "medium"),
            reporter_name=s.name,
            reporter_contact=s.contact,
            environment=env,
            steps_tried=list(s.steps_tried),
            transcript="\n".join(s.transcript),
        )
        try:
            ticket = await self.tickets.create(draft)
        except Exception:
            log.exception("ticket creation failed")
            return None, draft
        s.last_ticket = ticket.key
        s.note("Система", f"Создана заявка {ticket.key}")
        return ticket, draft
