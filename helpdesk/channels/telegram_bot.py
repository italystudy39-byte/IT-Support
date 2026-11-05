"""Telegram-бот. Запуск: python -m helpdesk.channels.telegram_bot"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml
from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                      ReplyKeyboardMarkup, Update)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from ..app import build_engine
from ..config import Settings
from ..engine import BTN_HUMAN, BTN_NEW, Engine, Out, escalation_text
from .formatting import md_to_plain, md_to_tg_html

log = logging.getLogger(__name__)

# Кнопки постоянного меню внизу экрана
MENU_NEW = "🚀 Новый вопрос"
MENU_FAQ = "❓ Частые вопросы"
MENU_HUMAN = "🆘 Позвать специалиста"
MAIN_MENU = ReplyKeyboardMarkup(
    [[KeyboardButton(MENU_FAQ)], [KeyboardButton(MENU_NEW), KeyboardButton(MENU_HUMAN)]],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Опишите проблему своими словами…",
)
FAQ_PREFIX = "faq:"

WELCOME = (
    "Привет! Я AI-помощник IT-поддержки 👋\n\n"
    "Опишите проблему обычными словами — например, «не работает VPN» или «как подключить принтер на 3 этаже?» — "
    "или выберите тему из частых вопросов ниже.\n\n"
    "Если не получится решить вместе, я сразу передам вопрос специалисту — кнопка «🆘 Позвать специалиста» всегда внизу."
)


def load_faq(path: str = "data/faq.yaml") -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [i for i in (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("items", []) if i.get("question")]


def faq_markup(faq: list[dict]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(i.get("label", i["question"])[:40], callback_data=f"{FAQ_PREFIX}{n}")
               for n, i in enumerate(faq)]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("🆘 Позвать специалиста", callback_data=BTN_HUMAN)])
    return InlineKeyboardMarkup(rows)


def _markup(out: Out) -> InlineKeyboardMarkup | None:
    if not out.buttons:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(b.label[:64], callback_data=b.data) for b in row] for row in out.buttons]
    )


async def _send(update: Update, outs: list[Out]) -> None:
    chat = update.effective_chat
    for out in outs:
        markup = _markup(out)
        if markup is None and out.text.startswith("Начнём заново"):
            markup = MAIN_MENU
        try:
            await chat.send_message(md_to_tg_html(out.text), parse_mode=ParseMode.HTML, reply_markup=markup)
        except BadRequest:  # кривой HTML — отправляем как есть
            await chat.send_message(md_to_plain(out.text), reply_markup=markup)


def _who(update: Update) -> tuple[str, str, str]:
    u = update.effective_user
    contact = f"@{u.username}" if u.username else f"tg:{u.id}"
    return str(u.id), u.full_name, contact


def build_application(settings: Settings) -> Application:
    faq = load_faq()

    async def post_init(app: Application) -> None:
        await app.bot.set_my_commands([
            BotCommand("start", "Начать / главное меню"),
            BotCommand("faq", "Частые вопросы"),
            BotCommand("new", "Новый вопрос"),
            BotCommand("human", "Позвать специалиста"),
        ])

    app = Application.builder().token(settings.telegram_token).concurrent_updates(True).post_init(post_init).build()

    async def notify_it(session, event) -> None:
        if settings.it_support_chat_id:
            await app.bot.send_message(settings.it_support_chat_id, escalation_text(session, event)[:4000])

    engine: Engine = build_engine(settings, on_escalation=notify_it)

    async def show_faq(update: Update) -> None:
        if faq:
            await update.effective_chat.send_message("Выберите тему:", reply_markup=faq_markup(faq))
        else:
            await update.effective_chat.send_message("Список частых вопросов пока пуст — просто опишите проблему.")

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        engine.reset(str(update.effective_user.id))
        await update.effective_chat.send_message(WELCOME, reply_markup=MAIN_MENU)
        await show_faq(update)

    async def new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        engine.reset(str(update.effective_user.id))
        await update.effective_chat.send_message("Начнём заново. Опишите, что случилось, или выберите тему 🙂",
                                                 reply_markup=MAIN_MENU)
        await show_faq(update)

    async def faq_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await show_faq(update)

    async def human(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        uid, name, contact = _who(update)
        async with engine.lock(uid):
            await update.effective_chat.send_chat_action(ChatAction.TYPING)
            await _send(update, await engine.on_button(engine.session(uid, name, contact), BTN_HUMAN))

    async def text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message.text.strip()
        if msg == MENU_FAQ:
            return await faq_cmd(update, ctx)
        if msg == MENU_NEW:
            return await new(update, ctx)
        if msg == MENU_HUMAN:
            return await human(update, ctx)
        uid, name, contact = _who(update)
        async with engine.lock(uid):
            await update.effective_chat.send_chat_action(ChatAction.TYPING)
            await _send(update, await engine.on_text(engine.session(uid, name, contact), msg))

    async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        await q.answer()
        uid, name, contact = _who(update)
        async with engine.lock(uid):
            # убираем кнопки у нажатого сообщения и показываем выбор
            label = next((b.text for row in (q.message.reply_markup.inline_keyboard if q.message.reply_markup else [])
                          for b in row if b.callback_data == q.data), "")
            try:
                await q.edit_message_reply_markup(None)
                if label:
                    await update.effective_chat.send_message(f"› {label}")
            except BadRequest:
                pass
            await update.effective_chat.send_chat_action(ChatAction.TYPING)
            if q.data.startswith(FAQ_PREFIX):
                n = int(q.data[len(FAQ_PREFIX):])
                if 0 <= n < len(faq):
                    engine.reset(uid)  # частый вопрос = новое обращение
                    s = engine.session(uid, name, contact)
                    await _send(update, await engine.on_text(s, faq[n]["question"]))
                return
            if q.data == BTN_NEW:
                engine.reset(uid)
                await update.effective_chat.send_message("Начнём заново. Опишите, что случилось 🙂",
                                                         reply_markup=MAIN_MENU)
                await show_faq(update)
                return
            await _send(update, await engine.on_button(engine.session(uid, name, contact), q.data))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new))
    app.add_handler(CommandHandler("faq", faq_cmd))
    app.add_handler(CommandHandler("human", human))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx пишет URL запросов, а в URL Telegram API есть токен бота — не логируем их
    for noisy in ("httpx", "httpcore", "telegram.ext.Updater"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = Settings()
    if not settings.telegram_token:
        raise SystemExit("Укажите TELEGRAM_BOT_TOKEN в .env")
    from .webhook import public_url, serve

    app = build_application(settings)
    url = public_url()
    if url:  # хостинг с публичным адресом (Render) — вебхук
        import asyncio

        asyncio.run(serve(app, settings.telegram_token, url))
    else:  # ноутбук / сервер — опрос Telegram
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
