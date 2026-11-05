"""Провайдер Gemini: перевод истории и ответов, без сети."""
import json
from types import SimpleNamespace

from google.genai import types

from helpdesk.app import build_engine
from helpdesk.config import Settings
from helpdesk.engine import BTN_OK
from helpdesk.llm import GeminiProvider


def fc_part(name, sig=b"sig-1", **args):
    return types.Part(function_call=types.FunctionCall(name=name, args=args), thought_signature=sig)


class FakeGenai:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._gen))

    async def _gen(self, model, contents, config):
        self.calls.append(contents)
        parts = self.script.pop(0)
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=parts), finish_reason="STOP")]
        )


async def test_gemini_guide_flow(tmp_path):
    s = Settings()
    s.kb_sources, s.kb_local_dir, s.guides_dir = ["local"], "data/kb", "data/guides"
    s.tickets_backend, s.mock_tickets_file = "mock", str(tmp_path / "t.jsonl")
    fake = FakeGenai([
        [fc_part("ask_user", question="Какая ОС?", options=["Windows", "macOS"])],
        [types.Part(text="Запускаю инструкцию."), fc_part("start_guide", sig=b"sig-2", guide_id="vpn_not_working", os="macos", office="")],
        [types.Part(text="Отлично!")],
    ])
    llm = GeminiProvider(api_key="x", client=fake)
    engine = build_engine(s, llm=llm)
    sess = engine.session("g1")

    outs = await engine.on_text(sess, "Не работает VPN")
    assert outs[0].text == "Какая ОС?"
    outs = await engine.on_button(sess, "opt:1")
    assert "Шаг 1 из 5" in outs[-1].text and sess.guide_run.variant == "macos"

    # второй запрос: function_call c подписью + function_response с ответом
    second = fake.calls[1]
    model_turn, user_turn = second[-2], second[-1]
    assert model_turn.role == "model" and model_turn.parts[0].function_call.name == "ask_user"
    assert model_turn.parts[0].thought_signature == b"sig-1"
    assert user_turn.parts[0].function_response.name == "ask_user"
    assert user_turn.parts[0].function_response.response == {"answer": "macOS"}

    outs = await engine.on_button(sess, BTN_OK)
    assert outs[-1].text == "Отлично!"
    resp = fake.calls[2][-1].parts[0].function_response
    assert resp.name == "start_guide" and resp.response["outcome"] == "resolved"


async def test_gemini_falls_back_when_busy(monkeypatch):
    from google.genai import errors
    import helpdesk.llm as llm_mod

    async def no_sleep(_):
        return None

    monkeypatch.setattr(llm_mod.asyncio, "sleep", no_sleep)
    used = []

    class Busy:
        def __init__(self):
            self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._gen))

        async def _gen(self, model, contents, config):
            used.append(model)
            if model == "main":
                raise errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
            return types.GenerateContentResponse(candidates=[types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text="ok")]), finish_reason="STOP")])

    p = GeminiProvider("x", model="main", client=Busy(), fallback_models=["lite"])
    r = await p.complete("s", [], [{"role": "user", "content": "hi"}], "u")
    assert r.content == [{"type": "text", "text": "ok"}]
    assert used == ["main", "main", "lite"]


async def test_gemini_timeout_moves_to_next_model():
    import asyncio
    used = []

    class Hang:
        def __init__(self):
            self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._gen))

        async def _gen(self, model, contents, config):
            used.append(model)
            if model == "slow":
                await asyncio.sleep(10)
            return types.GenerateContentResponse(candidates=[types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text="ok")]), finish_reason="STOP")])

    p = GeminiProvider("x", model="slow", client=Hang(), fallback_models=["fast"])
    p.TIMEOUT = 0.05
    r = await p.complete("s", [], [{"role": "user", "content": "hi"}], "u")
    assert r.content[0]["text"] == "ok" and used == ["slow", "fast"]
