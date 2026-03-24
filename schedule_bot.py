#!/usr/bin/env python3
"""
Telegram бот для відстеження розкладу ВКМНАУ
Автоматично моніторить https://vk.mnau.edu.ua/rozklad
та надсилає розклад для Ляшенко Д.В.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, date

import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ─────────────────────────────────────────────
# НАЛАШТУВАННЯ — змініть ці значення!
# ─────────────────────────────────────────────
BOT_TOKEN = "8667725385:AAHb3z2himN1mPKsfujhqDfJ20ALTEf4Uqg"
CHAT_IDS = ["662218673"]
TEACHER_NAME = "Ляшенко Д.В"         # прізвище для пошуку (без крапки в кінці)
SCHEDULE_URL = "https://vk.mnau.edu.ua/rozklad"
CHECK_INTERVAL_MINUTES = 30          # як часто перевіряти сайт
# ─────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Зберігаємо дати, про які вже надсилали повідомлення
notified_dates: set = set()


def fetch_schedule_page() -> BeautifulSoup | None:
    """Завантажує сторінку розкладу та повертає BeautifulSoup об'єкт."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ScheduleBot/1.0)"}
        response = requests.get(SCHEDULE_URL, headers=headers, timeout=15)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        logger.error(f"Помилка завантаження сторінки: {e}")
        return None


def parse_schedule_for_date(target_date: date) -> str | None:
    """
    Парсить розклад для вказаної дати.
    Повертає відформатований текст або None якщо не знайдено.
    """
    soup = fetch_schedule_page()
    if not soup:
        return None

    # Формат дати на сайті: YYYY-MM-DD
    date_str = target_date.strftime("%Y-%m-%d")

    # Шукаємо посилання з потрібною датою
    date_link = soup.find("a", string=date_str)
    if not date_link:
        # Спробуємо пошук за текстом у будь-якому елементі
        for elem in soup.find_all(string=date_str):
            if elem.strip() == date_str:
                date_link = elem.parent
                break

    if not date_link:
        logger.info(f"Дату {date_str} не знайдено на сайті")
        return None

    # Шукаємо таблицю після цього елемента
    # Таблиця може бути наступним siblings або в батьківському контейнері
    table = None
    current = date_link.parent if date_link else None

    # Шукаємо таблицю поруч
    for _ in range(10):
        if current is None:
            break
        table = current.find_next("table")
        if table:
            break
        current = current.parent

    if not table:
        # Якщо таблиця одна — беремо першу
        table = soup.find("table")

    if not table:
        return None

    return parse_table_for_teacher(table, date_str)


def parse_table_for_teacher(table, date_str: str) -> str | None:
    """Парсить таблицю та знаходить пари для викладача."""
    rows = table.find_all("tr")
    if not rows:
        return None

    # Отримуємо заголовки
    headers = []
    header_row = rows[0]
    for th in header_row.find_all(["th", "td"]):
        headers.append(th.get_text(strip=True))

    teacher_lessons = []

    current_course = ""
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        cell_texts = [c.get_text(strip=True) for c in cells]

        # Перевіряємо чи це рядок з курсом (1 курс, 2 курс тощо)
        if len(cell_texts) == 1 and "курс" in cell_texts[0].lower():
            current_course = cell_texts[0]
            continue

        if len(cell_texts) < 2:
            continue

        group = cell_texts[0]

        # Перевіряємо кожну пару (колонки І, ІІ, ІІІ, IV)
        pairs = cell_texts[1:]
        pair_names = ["І пара", "ІІ пара", "ІІІ пара", "IV пара"]

        for i, pair_text in enumerate(pairs):
            if TEACHER_NAME.lower() in pair_text.lower():
                pair_label = pair_names[i] if i < len(pair_names) else f"Пара {i+1}"
                teacher_lessons.append({
                    "course": current_course,
                    "group": group,
                    "pair": pair_label,
                    "subject": pair_text,
                })

    if not teacher_lessons:
        return None

    # Групуємо по парах
    pair_names = ["І пара", "ІІ пара", "ІІІ пара", "IV пара"]
    pairs_dict = {p: [] for p in pair_names}

    for lesson in teacher_lessons:
        pair = lesson["pair"]
        if pair in pairs_dict:
            subject_part = lesson["subject"].split("/")[0].strip()
            pairs_dict[pair].append(f"гр. *{lesson['group']}* — {subject_part}")

    lines = [f"📅 *Розклад на {date_str}*", f"👤 *{TEACHER_NAME}*", ""]

    pair_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    for i, pair in enumerate(pair_names):
        groups = pairs_dict[pair]
        if groups:
            lines.append(f"{pair_emojis[i]} *{pair}:*")
            for g in groups:
                lines.append(f"  • {g}")
            lines.append("")

    return "\n".join(lines)


def get_schedule_message(target_date: date) -> str:
    """Повертає повідомлення з розкладом або текст про відсутність."""
    weekday_names = {
        0: "понеділок", 1: "вівторок", 2: "середа",
        3: "четвер", 4: "пʼятниця", 5: "субота", 6: "неділя"
    }
    weekday = weekday_names.get(target_date.weekday(), "")
    date_display = f"{target_date.strftime('%d.%m.%Y')} ({weekday})"

    result = parse_schedule_for_date(target_date)

    if result:
        return result
    else:
        return (
            f"📅 Розклад на *{date_display}*\n\n"
            f"❌ Пар для *{TEACHER_NAME}* не знайдено або розклад ще не виставлено."
        )


def build_main_keyboard() -> InlineKeyboardMarkup:
    """Створює клавіатуру з кнопками."""
    keyboard = [
        [
            InlineKeyboardButton("📅 Сьогодні", callback_data="today"),
            InlineKeyboardButton("📆 Завтра", callback_data="tomorrow"),
        ],
        [
            InlineKeyboardButton("🔄 Оновити", callback_data="tomorrow"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# ─────────────────────────────────────────────
# КОМАНДИ БОТА
# ─────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник команди /start."""
    text = (
        f"👋 Привіт! Я бот розкладу ВКМНАУ.\n\n"
        f"🔍 Слідкую за розкладом для *{TEACHER_NAME}*\n"
        f"⏰ Перевіряю сайт кожні {CHECK_INTERVAL_MINUTES} хвилин\n\n"
        f"Оберіть дію:"
    )
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=build_main_keyboard(),
    )


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Розклад на сьогодні."""
    msg = await update.message.reply_text("⏳ Шукаю розклад...")
    text = get_schedule_message(date.today())
    await msg.edit_text(text, parse_mode="Markdown", reply_markup=build_main_keyboard())


async def tomorrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Розклад на завтра."""
    msg = await update.message.reply_text("⏳ Шукаю розклад...")
    tomorrow = date.today() + timedelta(days=1)
    text = get_schedule_message(tomorrow)
    await msg.edit_text(text, parse_mode="Markdown", reply_markup=build_main_keyboard())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник натискання кнопок."""
    query = update.callback_query
    await query.answer()

    if query.data == "today":
        target = date.today()
    elif query.data == "tomorrow":
        target = date.today() + timedelta(days=1)
    else:
        return

    await query.edit_message_text("⏳ Шукаю розклад...", parse_mode="Markdown")
    text = get_schedule_message(target)
    await query.edit_message_text(
        text, parse_mode="Markdown", reply_markup=build_main_keyboard()
    )


# ─────────────────────────────────────────────
# АВТОМАТИЧНИЙ МОНІТОРИНГ
# ─────────────────────────────────────────────

async def check_and_notify(context: ContextTypes.DEFAULT_TYPE):
    """
    Перевіряє розклад на завтра та надсилає повідомлення
    якщо він з'явився і ще не надсилався.
    """
    tomorrow = date.today() + timedelta(days=1)
    date_str = tomorrow.strftime("%Y-%m-%d")

    if date_str in notified_dates:
        return  # Вже надсилали

    logger.info(f"Перевіряю розклад на {date_str}...")
    result = parse_schedule_for_date(tomorrow)

    if result:
        notified_dates.add(date_str)
        logger.info(f"Знайдено розклад на {date_str}, надсилаю повідомлення")

        notification = f"🔔 *З'явився новий розклад!*\n\n{result}"

        for chat_id in CHAT_IDS:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=notification,
                    parse_mode="Markdown",
                    reply_markup=build_main_keyboard(),
                )
            except Exception as e:
                logger.error(f"Помилка надсилання в {chat_id}: {e}")


# ─────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────

def main():
    if BOT_TOKEN == "ВАШ_ТОКЕН_БОТ":
        print("❌ Будь ласка, встановіть BOT_TOKEN у налаштуваннях!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # Команди
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("tomorrow", tomorrow_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    # Автоматична перевірка кожні N хвилин
    job_queue = app.job_queue
    job_queue.run_repeating(
        check_and_notify,
        interval=CHECK_INTERVAL_MINUTES * 60,
        first=10,  # перша перевірка через 10 секунд після запуску
    )

    logger.info("🤖 Бот запущено!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
