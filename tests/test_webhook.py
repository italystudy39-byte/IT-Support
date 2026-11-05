from unittest.mock import AsyncMock, MagicMock

from aiohttp.test_utils import TestClient, TestServer

from helpdesk.channels.webhook import build_web_app, public_url, secret_for


async def test_webhook_routes(monkeypatch):
    app = MagicMock()
    app.update_queue.put = AsyncMock()
    secret = secret_for("123:abc")
    async with TestClient(TestServer(build_web_app(app, secret))) as c:
        assert (await (await c.get("/health")).text()) == "ok"
        r = await c.post(f"/tg/{secret}", json={"update_id": 1})
        assert r.status == 403  # без секретного заголовка
        r = await c.post(f"/tg/{secret}", json={"update_id": 1},
                         headers={"X-Telegram-Bot-Api-Secret-Token": secret})
        assert r.status == 200
        app.update_queue.put.assert_awaited_once()


def test_public_url(monkeypatch):
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://x.onrender.com/")
    assert public_url() == "https://x.onrender.com"
