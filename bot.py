import os
import json
import tempfile
import anthropic
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
SPREADSHEET_ID = "1OiwzcHadBvDJdn4qgf0wwueaHo7p5u_KXNfEvnbMTu0"

import os
import json
import tempfile
import anthropic
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
SPREADSHEET_ID = "1OiwzcHadBvDJdn4qgf0wwueaHo7p5u_KXNfEvnbMTu0"

SYSTEM_PROMPT = """Ты личный ассистент Александра Ихиритова (Саши). У тебя есть глубокий контекст о его жизни — анализ 140+ файлов его личного дневника за 2020–2026 годы. Используй этот контекст когда отвечаешь — не нужно каждый раз его упоминать, просто знай его.

Всегда отвечай на русском языке. Будь конкретным и полезным. Говори прямо, без лишних слов.

---

## КТО ТАКОЙ САША

Бурят из Улан-Удэ, 38 лет. Живёт в Нячанге, Вьетнам с 2022 года. Владелец EduCamp — русская школа (1–7 класс) + садик + летний лагерь + лагерь в Далате. Сейчас 220 учеников, цель — 1000. Прибыль школы ~1 млн рублей в месяц.

Жена Катя (болезнь Грейвса в стадии затихания) — директор школы.
Дети: Арсалан (2013, 6 класс), Батор (2016, 3 класс, кудо), Даша (2020, садик).
Заместитель Размик, ~50 сотрудников.

Здоровье: гипертония (Амлодипин 5 мг), давление 160/110 было, сейчас контролирует. Вес ~91 кг, цель 75 кг. Родинки — не проверял с 2023, боится.

---

## ПОРТРЕТ И ПАТТЕРНЫ (из анализа дневника)

**Сила:** Исключительная адаптивность. Из долговой ямы 8 млн рублей и депрессии — к школе на 220 учеников за 4 года. Умеет действовать когда по-настоящему страшно (сентябрь 2022 — уехал за 2 дня).

**Главный цикл:** Рывок → перегорание → прострация → новый рывок. Каждые несколько недель, неизменно с 2020 по 2026.

**Механизм защиты:** Умеет успокоить себя раньше чем становится по-настоящему страшно. Дневник — место куда выгружает тревогу, а не место где с ней работает. Написание плана снижает тревогу и мотивацию к действию одновременно.

**Хронические нерешённые вопросы:** родинки (3 года), , выход из операционки.

**Деньги:** Финансовая тревога как постоянный фон. Цикл: посчитал → испугался → написал контраргументы → успокоился → через неделю снова.

**История:** Улан-Удэ → Дубай (2019) → Улан-Удэ (ковид 2020) → мобилизация 21 сентября 2022 → уехал через 2 дня (маршрут: Красноярск → Москва → Минск → Ташкент → Бишкек → Дели → Фукуок → Нячанг).

---

## КАК РАБОТАТЬ С НИМ

- Если он говорит "я счастлив" — за этим обычно идёт лавина тревог, будь готов
- Если он строит большой план — мягко спроси про первый конкретный шаг сегодня
- Не поддакивай самооправданиям ("это символ победы", "это инвестиция") — лучше мягко назови что происходит
- Ценит прямоту и конкретность, не любит воду
- Хорошо реагирует на вопросы которые возвращают к телу и к близким людям
"""

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
openai = OpenAI(api_key=OPENAI_API_KEY)
chat_histories = {}


def transcribe(file_path):
    with open(file_path, "rb") as f:
        result = openai.audio.transcriptions.create(
            model="whisper-1", file=f, language="ru"
        )
    return result.text


def extract_tasks(transcript):
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": f"""Из расшифровки планёрки извлеки все задачи. Верни ТОЛЬКО JSON без лишнего текста:
{{
  "tasks": [
    {{
      "task": "описание задачи",
      "responsible": "имя или не указан",
      "deadline": "срок или не указан",
      "priority": "высокий/средний/низкий"
    }}
  ],
  "summary": "краткое резюме 2-3 предложения"
}}

Расшифровка:
{transcript}"""}]
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def save_to_sheets(tasks_data, date_str):
    import json as _json
    creds_info = _json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
    if not sheet.cell(1, 1).value:
        sheet.append_row(["Дата", "Задача", "Ответственный", "Срок", "Приоритет", "Статус"])
    for task in tasks_data["tasks"]:
        sheet.append_row([
            date_str, task["task"], task["responsible"],
            task["deadline"], task["priority"], "Новая"
        ])


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎙 Получил аудио, расшифровываю...")

    audio = update.message.voice or update.message.audio
    file = await context.bot.get_file(audio.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    try:
        transcript = transcribe(tmp_path)
        await update.message.reply_text(f"📝 Расшифровка:\n{transcript[:500]}{'...' if len(transcript) > 500 else ''}")

        await update.message.reply_text("🤖 Извлекаю задачи...")
        tasks_data = extract_tasks(transcript)

        from datetime import datetime
        date_str = datetime.now().strftime("%d.%m.%Y")
        save_to_sheets(tasks_data, date_str)

        reply = f"✅ *Резюме:*\n{tasks_data['summary']}\n\n📋 *Задачи ({len(tasks_data['tasks'])} шт.):*\n"
        for i, t in enumerate(tasks_data["tasks"], 1):
            reply += f"\n{i}. {t['task']}\n   👤 {t['responsible']} | 📅 {t['deadline']} | ⚡ {t['priority']}\n"
        reply += "\n📊 Всё записано в Google Sheets!"

        await update.message.reply_text(reply, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
    finally:
        os.unlink(tmp_path)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if user_id not in chat_histories:
        chat_histories[user_id] = []

    chat_histories[user_id].append({"role": "user", "content": text})
    if len(chat_histories[user_id]) > 20:
        chat_histories[user_id] = chat_histories[user_id][-20:]

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=chat_histories[user_id],
    )

    reply = response.content[0].text
    chat_histories[user_id].append({"role": "assistant", "content": reply})
    await update.message.reply_text(reply)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()


claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
openai = OpenAI(api_key=OPENAI_API_KEY)
chat_histories = {}


def transcribe(file_path):
    with open(file_path, "rb") as f:
        result = openai.audio.transcriptions.create(
            model="whisper-1", file=f, language="ru"
        )
    return result.text


def extract_tasks(transcript):
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": f"""Из расшифровки планёрки извлеки все задачи. Верни ТОЛЬКО JSON без лишнего текста:
{{
  "tasks": [
    {{
      "task": "описание задачи",
      "responsible": "имя или не указан",
      "deadline": "срок или не указан",
      "priority": "высокий/средний/низкий"
    }}
  ],
  "summary": "краткое резюме 2-3 предложения"
}}

Расшифровка:
{transcript}"""}]
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def save_to_sheets(tasks_data, date_str):
    import json as _json
    creds_info = _json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
    if not sheet.cell(1, 1).value:
        sheet.append_row(["Дата", "Задача", "Ответственный", "Срок", "Приоритет", "Статус"])
    for task in tasks_data["tasks"]:
        sheet.append_row([
            date_str, task["task"], task["responsible"],
            task["deadline"], task["priority"], "Новая"
        ])


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎙 Получил аудио, расшифровываю...")

    audio = update.message.voice or update.message.audio
    file = await context.bot.get_file(audio.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    try:
        transcript = transcribe(tmp_path)
        await update.message.reply_text(f"📝 Расшифровка:\n{transcript[:500]}{'...' if len(transcript) > 500 else ''}")

        await update.message.reply_text("🤖 Извлекаю задачи...")
        tasks_data = extract_tasks(transcript)

        from datetime import datetime
        date_str = datetime.now().strftime("%d.%m.%Y")
        save_to_sheets(tasks_data, date_str)

        reply = f"✅ *Резюме:*\n{tasks_data['summary']}\n\n📋 *Задачи ({len(tasks_data['tasks'])} шт.):*\n"
        for i, t in enumerate(tasks_data["tasks"], 1):
            reply += f"\n{i}. {t['task']}\n   👤 {t['responsible']} | 📅 {t['deadline']} | ⚡ {t['priority']}\n"
        reply += "\n📊 Всё записано в Google Sheets!"

        await update.message.reply_text(reply, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
    finally:
        os.unlink(tmp_path)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if user_id not in chat_histories:
        chat_histories[user_id] = []

    chat_histories[user_id].append({"role": "user", "content": text})
    if len(chat_histories[user_id]) > 20:
        chat_histories[user_id] = chat_histories[user_id][-20:]

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=chat_histories[user_id],
    )

    reply = response.content[0].text
    chat_histories[user_id].append({"role": "assistant", "content": reply})
    await update.message.reply_text(reply)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
