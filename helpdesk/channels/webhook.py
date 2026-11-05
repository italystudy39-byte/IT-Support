"""Режим вебхука для бесплатных хостингов (Render и т.п.).

Telegram сам присылает сообщения на https://<адрес>/tg/<секрет>, поэтому боту нужен
только HTTP-сервер. GET / и /health отвечают «ok» — для проверки здоровья и пинга.
Бесплатный Render усыпляет сервис после 15 минут без входящих запросов, поэтому
бот раз в 10 минут сам обращается к своему публичному адресу (KEEPALIVE_MINUTES=0 — выкл).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os

import httpx
from aiohttp import web
from telegram import Update
from telegram.ext import Application

log = logging.getLogger(__name__)


def public_url() -> str:
    # Render задаёт RENDER_EXTERNAL_URL сам; на другом хостинге укажите WEBHOOK_URL
    return (os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")


def secret_for(token: str) -> str:
    return hashlib.sha256(("helpdesk-webhook:" + token).encode()).hexdigest()[:32]


def build_web_app(app: Application, secret: str) -> web.Application:
    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def telegram_update(request: web.Request) -> web.Response:
        header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(header, secret):
            return web.Response(status=403)
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            return web.Response(status=400)
        await app.update_queue.put(Update.de_json(data, app.bot))
        return web.Response(text="ok")

    web_app = web.Application()
    web_app.router.add_get("/", health)
    web_app.router.add_get("/health", health)
    web_app.router.add_post(f"/tg/{secret}", telegram_update)
    return web_app


async def _keepalive(url: str, minutes: float) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            await asyncio.sleep(minutes * 60)
            try:
                await client.get(f"{url}/health")
            except Exception as e:  # noqa: BLE001
                log.warning("keepalive failed: %s", type(e).__name__)


async def serve(app: Application, token: str, url: str) -> None:
    secret = secret_for(token)
    port = int(os.getenv("PORT", "10000"))
    runner = web.AppRunner(build_web_app(app, secret))
    async with app:
        if app.post_init:
            await app.post_init(app)
        await app.start()
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", port).start()
        await app.bot.set_webhook(
            f"{url}/tg/{secret}", secret_token=secret, allowed_updates=Update.ALL_TYPES, drop_pending_updates=False
        )
        log.info("Webhook mode: listening on port %s, public url %s", port, url)
        minutes = float(os.getenv("KEEPALIVE_MINUTES", "10"))
        task = asyncio.create_task(_keepalive(url, minutes)) if minutes > 0 else None
        try:
            await asyncio.Event().wait()
        finally:
            if task:
                task.cancel()
            await runner.cleanup()
            await app.stop()
