from helpdesk.channels.telegram_bot import FAQ_PREFIX, MAIN_MENU, build_application, faq_markup, load_faq
from helpdesk.config import Settings


def test_faq_menu():
    faq = load_faq("data/faq.yaml")
    assert len(faq) >= 4 and faq[0]["question"] == "Не работает VPN"
    m = faq_markup(faq)
    datas = [b.callback_data for row in m.inline_keyboard for b in row]
    assert datas[0] == f"{FAQ_PREFIX}0" and datas[-1] == "human"
    assert all(len(row) <= 2 for row in m.inline_keyboard)
    labels = [b.text for row in MAIN_MENU.keyboard for b in row]
    assert "❓ Частые вопросы" in labels and "🆘 Позвать специалиста" in labels


def test_app_builds():
    s = Settings()
    s.telegram_token = "123:abc"
    s.llm_provider = "gemini"; s.gemini_api_key = "x"
    app = build_application(s)
    assert len(app.handlers[0]) == 6
