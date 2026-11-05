"""Сквозные тесты движка с фейковым Claude-клиентом (без сети и ключей)."""
import json
from types import SimpleNamespace

import pytest
from anthropic.types.beta import BetaTextBlock, BetaToolUseBlock

from helpdesk.app import build_engine
from helpdesk.config import Settings
from helpdesk.engine import BTN_FAIL, BTN_HUMAN, BTN_NEXT, BTN_OK
from helpdesk.kb.local import LocalMarkdownKB
from helpdesk.llm import ClaudeProvider


def text(t):
    return BetaTextBlock(type="text", text=t)


def tool(name, **inp):
    tool.n += 1
    return BetaToolUseBlock(type="tool_use", id=f"toolu_{tool.n}", name=name, input=inp)


tool.n = 0


class FakeClient:
    """Отдаёт заранее записанные ответы и запоминает запросы."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=str)))
        blocks = self.script.pop(0)
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        return SimpleNamespace(content=blocks, stop_reason=stop)


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.kb_sources = ["local"]
    s.kb_local_dir = "data/kb"
    s.guides_dir = "data/guides"
    s.tickets_backend = "mock"
    s.mock_tickets_file = str(tmp_path / "tickets.jsonl")
    s.claude_model = "claude-opus-5-5"
    return s


async def test_vpn_guide_fails_then_ticket(settings, tmp_path):
    client = FakeClient([
        [tool("ask_user", question="Какая у вас ОС?", options=["Windows", "macOS"])],
        [text("Сейчас разберёмся."), tool("start_guide", guide_id="vpn_not_working", os="windows", office="")],
        [tool("create_ticket", summary="Не подключается VPN (Windows)", description="Гайд не помог",
              category="network_vpn", priority="medium", os="windows", device="ноутбук", office="")],
        [text("Создал заявку, специалист свяжется.")],
    ])
    notified = []

    async def hook(s, e):
        notified.append(e.ticket.key)

    engine = build_engine(settings, on_escalation=hook, llm=ClaudeProvider(client=client, model="claude-opus-5-5"))
    s = engine.session("u1", "Иван Петров", "ivan.petrov@corp.example")

    outs = await engine.on_text(s, "Не работает VPN")
    assert outs[0].text == "Какая у вас ОС?"
    assert [b.label for b in outs[0].buttons[0]] == ["Windows"]

    outs = await engine.on_button(s, "opt:0")
    assert "Шаг 1 из 5" in outs[-1].text
    # tool_result с ответом ушёл модели
    last_user = client.calls[1]["messages"][-1]
    assert last_user["content"][0]["type"] == "tool_result"
    assert json.loads(last_user["content"][0]["content"]) == {"answer": "Windows"}

    await engine.on_button(s, BTN_NEXT)
    outs = await engine.on_button(s, BTN_NEXT)
    assert "Шаг 3 из 5" in outs[0].text
    outs = await engine.on_button(s, BTN_FAIL)

    guide_result = json.loads(client.calls[2]["messages"][-1]["content"][0]["content"])
    assert guide_result["outcome"] == "not_resolved"
    assert guide_result["stopped_at_step"] == 3
    assert len(guide_result["steps_done"]) == 2

    assert any("HELP-1" in o.text for o in outs)
    assert notified == ["HELP-1"]
    rec = json.loads((tmp_path / "tickets.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["reporter_contact"] == "ivan.petrov@corp.example"
    assert len(rec["steps_tried"]) == 3  # 2 выполненных + 1 неудачный
    assert "Не работает VPN" in rec["transcript"]

    # системный промпт кешируется, fallback включён, tool_choice без принуждения
    call = client.calls[0]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["fallbacks"] == "default"
    assert call["tool_choice"]["type"] == "auto"


async def test_guide_resolved(settings):
    client = FakeClient([
        [tool("start_guide", guide_id="password_reset", os="any", office="")],
        [text("Отлично, рад был помочь!")],
    ])
    engine = build_engine(settings, llm=ClaudeProvider(client=client, model="claude-opus-5-5"))
    s = engine.session("u2")
    outs = await engine.on_text(s, "Забыл пароль от почты")
    assert "Сброс пароля" in outs[0].text
    outs = await engine.on_button(s, BTN_OK)
    assert outs[-1].text == "Отлично, рад был помочь!"
    assert json.loads(client.calls[1]["messages"][-1]["content"][0]["content"])["outcome"] == "resolved"


async def test_kb_search_roundtrip(settings):
    client = FakeClient([
        [tool("search_knowledge_base", query="гостевой wifi пароль")],
        [text("Гостевая сеть — `Corp-Guest`, пароль на стене переговорной.")],
    ])
    engine = build_engine(settings, llm=ClaudeProvider(client=client, model="claude-opus-5-5"))
    s = engine.session("u3")
    outs = await engine.on_text(s, "Где взять гостевой Wi-Fi?")
    assert "Corp-Guest" in outs[0].text
    result = json.loads(client.calls[1]["messages"][-1]["content"][0]["content"])
    assert "Гостевой" in result["results"][0]["title"]


async def test_human_button_always_creates_ticket(settings, tmp_path):
    # модель не вызвала create_ticket — движок всё равно создаёт заявку
    client = FakeClient([
        [tool("ask_user", question="Какая ОС?", options=["Windows", "macOS"])],
        [text("Передаю специалисту.")],
    ])
    engine = build_engine(settings, llm=ClaudeProvider(client=client, model="claude-opus-5-5"))
    s = engine.session("u4", "Анна", "@anna")
    await engine.on_text(s, "Сломался ноутбук, не включается")
    outs = await engine.on_button(s, BTN_HUMAN)
    assert any("HELP-1" in o.text for o in outs)


async def test_text_during_guide_interrupts(settings):
    client = FakeClient([
        [tool("start_guide", guide_id="printer_connect", os="macos", office="Москва, 3 этаж")],
        [text("Понял, принтер другой модели — давайте уточню.")],
    ])
    engine = build_engine(settings, llm=ClaudeProvider(client=client, model="claude-opus-5-5"))
    s = engine.session("u5")
    await engine.on_text(s, "Как подключить принтер на 3 этаже?")
    assert s.guide_run is not None
    await engine.on_text(s, "У меня нет такого пункта в настройках")
    res = json.loads(client.calls[1]["messages"][-1]["content"][0]["content"])
    assert res["outcome"] == "interrupted" and res["user_message"].startswith("У меня нет")
    assert s.profile["office"] == "Москва, 3 этаж"


async def test_local_kb_ranking():
    kb = LocalMarkdownKB("data/kb")
    res = await kb.search("как настроить почту на телефоне")
    assert res and "телефоне" in res[0].title
    res = await kb.search("письмо фишинг ссылка")
    assert "фишинг" in res[0].title.lower()


async def test_human_without_problem_asks_first(settings, tmp_path):
    client = FakeClient([[text("Передаю специалисту.")]])
    engine = build_engine(settings, llm=ClaudeProvider(client=client, model="claude-opus-5-5"))
    s = engine.session("u6", "Пётр", "@petr")
    outs = await engine.on_button(s, BTN_HUMAN)
    assert "опишите" in outs[0].text.lower() and not client.calls
    outs = await engine.on_text(s, "Монитор не включается")
    assert "Монитор не включается" in client.calls[0]["messages"][-1]["content"]
    assert any("HELP-1" in o.text for o in outs)
    rec = json.loads((tmp_path / "tickets.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert "Монитор не включается" in rec["transcript"]
