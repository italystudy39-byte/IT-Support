"""Провайдеры LLM: Claude (Anthropic) и Gemini (Google).

История диалога хранится в одном формате (блоки text / tool_use / tool_result,
как в Messages API). Провайдер Gemini переводит её в формат Google на каждом
запросе и переводит ответ обратно, поэтому остальной код от модели не зависит.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


class LLMRateLimit(Exception):
    """Превышен лимит запросов (особенно часто на бесплатном тарифе Gemini)."""


class LLMError(Exception):
    """Любая другая ошибка провайдера."""


@dataclass
class LLMReply:
    content: list[dict]  # блоки text / tool_use / thinking ...
    refused: bool = False


class LLMProvider(Protocol):
    async def complete(self, system: str, tools: list[dict], history: list[dict], user_id: str) -> LLMReply: ...


# ----------------------------------------------------------------- Claude


FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


def _sanitize_claude(blocks: list[dict]) -> list[dict]:
    """После серверного fallback посреди ответа нельзя возвращать thinking/tool_use до блока fallback."""
    idx = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    if idx < 0:
        return blocks
    drop = {"thinking", "redacted_thinking", "tool_use"}
    return [b for i, b in enumerate(blocks) if i > idx or (i < idx and b.get("type") not in drop)]


class ClaudeProvider:
    def __init__(self, client=None, model: str = "claude-opus-5-5", effort: str = "low"):
        import anthropic

        self._anthropic = anthropic
        self.client = client or anthropic.AsyncAnthropic()
        self.model = model
        self.effort = effort

    async def complete(self, system: str, tools: list[dict], history: list[dict], user_id: str) -> LLMReply:
        a = self._anthropic
        kwargs: dict = dict(
            model=self.model,
            max_tokens=4096,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            messages=history,
            output_config={"effort": self.effort},
            metadata={"user_id": str(user_id)},
        )
        if self.model in FALLBACK_MODELS:
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        try:
            resp = await self.client.beta.messages.create(**kwargs)
        except a.RateLimitError as e:
            raise LLMRateLimit(str(e)) from e
        except (a.APIStatusError, a.APIConnectionError) as e:
            raise LLMError(str(e)) from e
        if resp.stop_reason == "refusal":
            return LLMReply([], refused=True)
        return LLMReply(_sanitize_claude([b.model_dump(mode="json", exclude_none=True) for b in resp.content]))


# ----------------------------------------------------------------- Gemini


def _clean_schema(schema: dict) -> dict:
    return {k: v for k, v in schema.items() if k != "strict"}


class GeminiProvider:
    RETRY_CODES = {500, 502, 503, 504}

    def __init__(self, api_key: str, model: str = "gemini-flash-lite-latest", client=None,
                 fallback_models: list[str] | None = None):
        from google import genai
        from google.genai import types

        self.types = types
        self.client = client or genai.Client(api_key=api_key)
        self.model = model
        # если основная модель перегружена (503) — пробуем запасные
        self.models = [model] + [m for m in (fallback_models or []) if m and m != model]

    TIMEOUT = 25.0  # секунд на один запрос: перегруженная модель иногда просто не отвечает

    async def _generate(self, contents, config):
        from google.genai import errors

        last: Exception | None = None
        for model in self.models:
            for attempt in range(2):
                try:
                    return await asyncio.wait_for(
                        self.client.aio.models.generate_content(model=model, contents=contents, config=config),
                        timeout=self.TIMEOUT,
                    )
                except asyncio.TimeoutError as e:
                    last = e
                    log.warning("Gemini %s timed out, trying next model", model)
                    break
                except errors.APIError as e:
                    last = e
                    code = getattr(e, "code", None)
                    if code == 429:
                        raise LLMRateLimit(str(e)) from e
                    if code == 404:
                        log.warning("Gemini model %s not found, trying next", model)
                        break
                    if code not in self.RETRY_CODES:
                        raise LLMError(str(e)) from e
                    log.warning("Gemini %s busy (%s), attempt %d", model, code, attempt + 1)
                    await asyncio.sleep(1.0 * (attempt + 1))
        raise LLMError(f"Gemini недоступен: {last!r}")

    # история (формат Messages API) -> contents Gemini
    def _to_contents(self, history: list[dict]):
        t = self.types
        names: dict[str, str] = {}
        contents: list = []

        def add(role: str, parts: list) -> None:
            if not parts:
                return
            if contents and contents[-1].role == role:
                contents[-1].parts.extend(parts)
            else:
                contents.append(t.Content(role=role, parts=parts))

        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            content = msg["content"]
            if isinstance(content, str):
                add(role, [t.Part(text=content)])
                continue
            parts = []
            for b in content:
                kind = b.get("type")
                if kind == "text" and b.get("text"):
                    parts.append(t.Part(text=b["text"]))
                elif kind == "tool_use":
                    names[b["id"]] = b["name"]
                    sig = b.get("gemini_signature")
                    parts.append(
                        t.Part(
                            function_call=t.FunctionCall(name=b["name"], args=b.get("input") or {}),
                            thought_signature=base64.b64decode(sig) if sig else None,
                        )
                    )
                elif kind == "tool_result":
                    raw = b.get("content", "")
                    try:
                        data = json.loads(raw) if isinstance(raw, str) else {"result": raw}
                    except json.JSONDecodeError:
                        data = {"result": raw}
                    if not isinstance(data, dict):
                        data = {"result": data}
                    parts.append(
                        t.Part.from_function_response(name=names.get(b["tool_use_id"], "tool"), response=data)
                    )
            add(role, parts)
        return contents

    async def complete(self, system: str, tools: list[dict], history: list[dict], user_id: str) -> LLMReply:
        t = self.types
        config = t.GenerateContentConfig(
            system_instruction=system,
            tools=[
                t.Tool(
                    function_declarations=[
                        t.FunctionDeclaration(
                            name=x["name"],
                            description=x["description"],
                            parameters_json_schema=_clean_schema(x["input_schema"]),
                        )
                        for x in tools
                    ]
                )
            ],
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=4096,
        )
        resp = await self._generate(self._to_contents(history), config)

        cand = (resp.candidates or [None])[0]
        if cand is None or cand.content is None or not cand.content.parts:
            reason = str(getattr(cand, "finish_reason", "")) if cand else "no candidates"
            blocked = any(k in reason for k in ("SAFETY", "PROHIBITED", "BLOCKLIST", "SPII")) or cand is None
            if blocked:
                return LLMReply([], refused=True)
            return LLMReply([{"type": "text", "text": "Не совсем понял. Опишите, пожалуйста, проблему чуть подробнее."}])

        blocks: list[dict] = []
        for p in cand.content.parts:
            if p.thought:  # внутренние рассуждения не показываем
                continue
            if p.function_call:
                if any(b["type"] == "tool_use" for b in blocks):
                    continue  # обрабатываем по одному инструменту за раз
                block = {
                    "type": "tool_use",
                    "id": p.function_call.id or f"call_{uuid.uuid4().hex[:12]}",
                    "name": p.function_call.name,
                    "input": dict(p.function_call.args or {}),
                }
                if p.thought_signature:
                    block["gemini_signature"] = base64.b64encode(p.thought_signature).decode()
                blocks.append(block)
            elif p.text:
                blocks.append({"type": "text", "text": p.text})
        return LLMReply(blocks)
