import os
import json
import tempfile
import anthropic
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
SPREADSHEET_ID = "1OiwzcHadBvDJdn4qgf0wwueaHo7p5u_KXNfEvnbMTu0"
CALENDAR_ID = "primary"

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

**Хронические нерешённые вопросы:** родинки (3 года), юридический риск с Коном (владеет бизнесом юридически), честный разговор с Катей о том кем она хочет быть, выход из операционки.

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


def get_google_creds(scopes):
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    return Credentials.from_service_account_info(creds_info, scopes=scopes)


def save_to_sheets(tasks_data, date_str):
    creds = get_google_creds(["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
    if not sheet.cell(1, 1).value:
        sheet.append_row(["Дата", "Задача", "Ответственный", "Срок", "Приоритет", "Статус"])
    for task in tasks_data["tasks"]:
        sheet.append_row([
            date_str, task["task"], task["responsible"],
            task["deadline"], task["priority"], "Новая"
        ])


def classify_and_parse(transcript):
    today = datetime.now().strftime("%Y-%m-%d")
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": f"""Сегодня {today}. Проанализируй текст и определи — это задача или событие календаря.

Событие календаря: конкретная дата и время ("в пятницу в 14:00", "завтра встреча", "3 июня в 10:00").
Задача: нет конкретного времени, просто "нужно сделать".

Верни ТОЛЬКО JSON:
{{
  "type": "calendar" или "task",
  "title": "название",
  "date": "YYYY-MM-DD если calendar, иначе null",
  "time": "HH:MM если calendar, иначе null",
  "duration_minutes": 60,
  "description": "детали или null"
}}

Текст: {transcript}"""}]
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def create_calendar_event(title, date_str, time_str, duration_minutes=60, description=""):
    creds = get_google_creds(["https://www.googleapis.com/auth/calendar"])
    service = build("calendar", "v3", credentials=creds)

    start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=duration_minutes)

    event = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Ho_Chi_Minh"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Ho_Chi_Minh"},
    }
    created = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
    return created.get("htmlLink")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎙 Получил аудио, расшифровываю...")

    audio = update.message.voice or update.message.audio
    file = await context.bot.get_file(audio.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    try:
        transcript = transcribe(tmp_path)
        await update.message.reply_text(f"📝 {transcript[:300]}{'...' if len(transcript) > 300 else ''}")

        await update.message.reply_text("🤖 Анализирую...")
        parsed = classify_and_parse(transcript)

        if parsed["type"] == "calendar" and parsed["date"] and parsed["time"]:
            link = create_calendar_event(
                title=parsed["title"],
                date_str=parsed["date"],
                time_str=parsed["time"],
                duration_minutes=parsed.get("duration_minutes", 60),
                description=parsed.get("description") or ""
            )
            reply = f"📅 *Событие создано в календаре:*\n\n*{parsed['title']}*\n📆 {parsed['date']} в {parsed['time']}\n⏱ {parsed.get('duration_minutes', 60)} мин\n\n[Открыть в Google Calendar]({link})"
            await update.message.reply_text(reply, parse_mode="Markdown")
        else:
            date_str = datetime.now().strftime("%d.%m.%Y")
            tasks_data = extract_tasks(transcript)
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
