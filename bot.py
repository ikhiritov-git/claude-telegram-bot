import os
import json
import tempfile
import anthropic
import requests
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload
from datetime import datetime, timedelta, time as dtime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from garminconnect import Garmin

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL", "")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD", "")
TELEGRAM_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")
TRACKER_SPREADSHEET_ID = os.environ.get("TRACKER_SPREADSHEET_ID", "")
DRIVE_MEETINGS_FOLDER_ID = os.environ.get("DRIVE_MEETINGS_FOLDER_ID", "")
SPREADSHEET_ID = "1OiwzcHadBvDJdn4qgf0wwueaHo7p5u_KXNfEvnbMTu0"
CALENDAR_ID = "ikhiritov@gmail.com"
VIETNAM_TZ_OFFSET = "+07:00"

DAILY_HABITS = [
    ("gratitude", "Благодарности"),
    ("reading", "Чтение 10мин"),
    ("finance", "Учет финансов"),
    ("no_news", "Без новостей"),
    ("no_social", "Без соцсетей"),
    ("no_flour", "Без мучного"),
    ("no_sugar", "Без сахара"),
    ("sport", "Спорт"),
]
WEEKLY_HABITS = [
    ("planning", "Планёрка"),
    ("mom_call", "Звонок маме"),
    ("flowers", "Цветы Кате"),
]
MONTH_NAMES_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}
MONTH_NAMES_EN = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

# Строки в таблице (1-indexed)
HABIT_ROWS = {
    "steps":    5,
    "gratitude": 6,
    "sleep":    7,
    "calories": 8,
    "reading":  9,
    "finance":  10,
    "no_news":  11,
    "no_social": 12,
    "no_flour": 13,
    "no_sugar": 14,
    "planning": 16,
    "mom_call": 17,
    "flowers":  18,
    "weight":   21,
    "sport":    22,
    "protein":  23,
}

checkin_state = {}  # {user_id: {habit_key: True/False, 'weight': float}}

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
        messages=[{"role": "user", "content": f"""Из расшифровки извлеки все задачи. Верни ТОЛЬКО JSON без лишнего текста:
{{
  "tasks": [
    {{
      "task": "описание задачи",
      "responsible": "имя или не указан",
      "deadline": "срок или не указан",
      "priority": "высокий/средний/низкий",
      "type": "работа или личное"
    }}
  ],
  "summary": "краткое резюме 2-3 предложения"
}}

Правило для type: "работа" — если связано со школой EduCamp, учениками, сотрудниками, партнёрами, бизнесом. "личное" — здоровье, семья, дети, личные дела.

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
        sheet.append_row(["Дата", "Тип", "Задача", "Ответственный", "Срок", "Приоритет", "Статус"])
    for task in tasks_data["tasks"]:
        sheet.append_row([
            date_str, task.get("type", "работа"), task["task"],
            task["responsible"], task["deadline"], task["priority"], "Новая"
        ])


def extract_knowledge(transcript):
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": f"""Проанализируй расшифровку встречи/записи и извлеки структурированные знания. Верни ТОЛЬКО JSON:
{{
  "title": "краткое название темы (5-7 слов)",
  "decisions": ["принятое решение 1", "принятое решение 2"],
  "processes": ["описание процесса/порядка действий"],
  "regulations": ["правило или стандарт который установили"],
  "context": "важный контекст: факты о людях, ситуации, школе (2-3 предложения или пусто)"
}}

Если какой-то раздел пустой — верни пустой список [].
Decisions — только конкретные принятые решения ("решили что...", "договорились...").
Processes — порядок действий, как что-то делать.
Regulations — правила, стандарты, требования ("всегда", "нельзя", "обязательно").

Расшифровка:
{transcript}"""}]
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def get_or_create_drive_folder(service, name, parent_id=None):
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        body["parents"] = [parent_id]
    folder = service.files().create(body=body, fields="id").execute()
    return folder["id"]


def save_transcript_to_drive(transcript, title, date_str):
    creds = get_google_creds([
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive",
    ])
    service = build("drive", "v3", credentials=creds)

    # Используем папку из env (расшаренную на сервис-аккаунт),
    # иначе пробуем создать структуру (может не работать без квоты)
    if DRIVE_MEETINGS_FOLDER_ID:
        meetings_id = DRIVE_MEETINGS_FOLDER_ID
    else:
        educamp_id = get_or_create_drive_folder(service, "EduCamp")
        meetings_id = get_or_create_drive_folder(service, "Встречи", parent_id=educamp_id)

    filename = f"{date_str} {title}.txt"
    content = f"Дата: {date_str}\nТема: {title}\n\n{'='*50}\n\n{transcript}"
    media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/plain")
    file_meta = {"name": filename, "parents": [meetings_id]}
    created = service.files().create(body=file_meta, media_body=media, fields="id,webViewLink").execute()
    return created.get("webViewLink", "")


def save_to_notion(knowledge, transcript, date_str):
    if not NOTION_TOKEN or not NOTION_DATABASE_ID:
        return None

    def rich(text):
        return [{"type": "text", "text": {"content": text[:2000]}}]

    def bullets(items):
        return [{"object": "block", "type": "bulleted_list_item",
                 "bulleted_list_item": {"rich_text": rich(item)}} for item in items if item]

    children = []
    if knowledge.get("decisions"):
        children.append({"object": "block", "type": "heading_2",
                          "heading_2": {"rich_text": rich("Решения")}})
        children.extend(bullets(knowledge["decisions"]))

    if knowledge.get("processes"):
        children.append({"object": "block", "type": "heading_2",
                          "heading_2": {"rich_text": rich("Процессы")}})
        children.extend(bullets(knowledge["processes"]))

    if knowledge.get("regulations"):
        children.append({"object": "block", "type": "heading_2",
                          "heading_2": {"rich_text": rich("Регламенты")}})
        children.extend(bullets(knowledge["regulations"]))

    if knowledge.get("context"):
        children.append({"object": "block", "type": "heading_2",
                          "heading_2": {"rich_text": rich("Контекст")}})
        children.append({"object": "block", "type": "paragraph",
                          "paragraph": {"rich_text": rich(knowledge["context"])}})

    children.append({"object": "block", "type": "heading_2",
                      "heading_2": {"rich_text": rich("Полная расшифровка")}})
    children.append({"object": "block", "type": "paragraph",
                      "paragraph": {"rich_text": rich(transcript[:2000])}})

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Name": {"title": rich(f"{date_str} — {knowledge.get('title', 'Без названия')}")},
        },
        "children": children,
    }
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    r = requests.post("https://api.notion.com/v1/pages", json=payload, headers=headers)
    if r.ok:
        return r.json().get("url", "")
    return None


def classify_text_intent(text):
    response = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[{"role": "user", "content": f"""Определи тип сообщения. Ответь ТОЛЬКО одним словом:
- "tasks" — список задач, планы, что нужно сделать, рабочие заметки, итоги встречи
- "calendar" — есть конкретная дата и время события
- "chat" — обычный разговор, вопрос, сообщение

Сообщение: {text}"""}]
    )
    return response.content[0].text.strip().lower()


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
        "colorId": "3",
    }
    created = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
    return created.get("htmlLink")


def fmt_duration(seconds):
    if not seconds:
        return "0мин"
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    return f"{h}ч {m}мин" if h else f"{m}мин"


def get_garmin_sleep(date_str):
    """date_str: YYYY-MM-DD. Returns dict with sleep stats or {'error': ...}."""
    try:
        client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        client.login()
        data = client.get_sleep_data(date_str)
        dto = data.get("dailySleepDTO") or {}

        hrv_data = data.get("hrvSummary") or {}
        hrv_weekly_avg = hrv_data.get("weeklyAvg")
        hrv_last_night = hrv_data.get("lastNight")
        hrv_status = hrv_data.get("hrvStatus")

        start_ts = dto.get("sleepStartTimestampLocal")
        end_ts = dto.get("sleepEndTimestampLocal")
        bed_time = datetime.fromtimestamp(start_ts / 1000).strftime("%H:%M") if start_ts else None
        wake_time = datetime.fromtimestamp(end_ts / 1000).strftime("%H:%M") if end_ts else None

        return {
            "total": fmt_duration(dto.get("sleepTimeSeconds")),
            "deep": fmt_duration(dto.get("deepSleepSeconds")),
            "light": fmt_duration(dto.get("lightSleepSeconds")),
            "rem": fmt_duration(dto.get("remSleepSeconds")),
            "awake": fmt_duration(dto.get("awakeSleepSeconds")),
            "score": dto.get("sleepScoreWithSleepNeed") or dto.get("sleepScore"),
            "spo2": dto.get("averageSpO2Value"),
            "respiration": dto.get("averageRespirationValue"),
            "hrv_status": hrv_status,
            "hrv_last_night": hrv_last_night,
            "hrv_weekly_avg": hrv_weekly_avg,
            "bed_time": bed_time,
            "wake_time": wake_time,
            "raw_hours": (dto.get("sleepTimeSeconds") or 0) / 3600,
        }
    except Exception as e:
        return {"error": str(e)}


def get_yesterday_tasks():
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%d.%m.%Y")
    try:
        creds = get_google_creds(["https://www.googleapis.com/auth/spreadsheets"])
        gc = gspread.authorize(creds)
        rows = gc.open_by_key(SPREADSHEET_ID).sheet1.get_all_values()
        result = []
        for row in rows:
            if not row or row[0] != yesterday:
                continue
            # Поддержка обоих форматов: 6 колонок (без типа) и 7 колонок (с типом)
            if len(row) >= 7:
                result.append({"Тип": row[1], "Задача": row[2], "Ответственный": row[3], "Срок": row[4], "Приоритет": row[5], "Статус": row[6]})
            elif len(row) >= 6:
                result.append({"Тип": "", "Задача": row[1], "Ответственный": row[2], "Срок": row[3], "Приоритет": row[4], "Статус": row[5]})
        return result
    except Exception:
        return []


def get_today_events():
    today = datetime.now()
    start = today.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT00:00:00") + VIETNAM_TZ_OFFSET
    end = today.replace(hour=23, minute=59, second=59, microsecond=0).strftime("%Y-%m-%dT23:59:59") + VIETNAM_TZ_OFFSET
    try:
        creds = get_google_creds(["https://www.googleapis.com/auth/calendar.readonly"])
        service = build("calendar", "v3", credentials=creds)
        result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        return result.get("items", [])
    except Exception:
        return []


def build_report_prompt(yesterday_display, today_display, sleep, daily_stats, tasks, events):
    lines = [f"Утренний брифинг. Вчера: {yesterday_display}, сегодня: {today_display}\n"]

    # Блок восстановления
    recovery_lines = []
    if daily_stats and "error" not in daily_stats:
        if daily_stats.get("body_battery") is not None:
            recovery_lines.append(f"  Body Battery (заряд после сна): {daily_stats['body_battery']}/100")
        if daily_stats.get("resting_hr"):
            recovery_lines.append(f"  ЧСС покоя: {daily_stats['resting_hr']} уд/мин")
        if daily_stats.get("stress_avg") is not None:
            recovery_lines.append(f"  Стресс за день: {daily_stats['stress_avg']} ({daily_stats.get('stress_label', '')})")
    if recovery_lines:
        lines.append("Восстановление:")
        lines.extend(recovery_lines)
        lines.append("")

    if "error" in sleep:
        lines.append(f"Сон: данные недоступны ({sleep['error']})\n")
    else:
        bed = f"лёг {sleep['bed_time']}" if sleep.get("bed_time") else ""
        wake = f"встал {sleep['wake_time']}" if sleep.get("wake_time") else ""
        timing = f" ({bed}, {wake})" if bed or wake else ""
        lines.append(f"Сон{timing}:")
        lines.append(f"  Всего: {sleep['total']}, глубокий: {sleep['deep']}, REM: {sleep['rem']}, лёгкий: {sleep['light']}, бодрствование: {sleep['awake']}")
        if sleep.get("score"):
            lines.append(f"  Оценка сна: {sleep['score']}/100")
        if sleep.get("spo2"):
            lines.append(f"  SpO₂: {sleep['spo2']}%")
        if sleep.get("respiration"):
            lines.append(f"  Дыхание: {sleep['respiration']} вд/мин")
        if sleep.get("hrv_last_night"):
            hrv_line = f"  HRV ночью: {sleep['hrv_last_night']}"
            if sleep.get("hrv_weekly_avg"):
                hrv_line += f" (средний за неделю: {sleep['hrv_weekly_avg']})"
            if sleep.get("hrv_status"):
                hrv_line += f" — статус: {sleep['hrv_status']}"
            lines.append(hrv_line)
        lines.append("")

    if tasks:
        lines.append(f"Задачи записанные вчера ({yesterday_display}), {len(tasks)} шт.:")
        for t in tasks:
            icon = "🏢" if t.get("Тип") == "работа" else "👤"
            lines.append(f"  {icon} {t.get('Задача', '')} | {t.get('Статус', '')} | {t.get('Приоритет', '')}")
        lines.append("")
    else:
        lines.append(f"Задач за вчера ({yesterday_display}) не записано.\n")

    if events:
        lines.append(f"Расписание на сегодня ({today_display}):")
        for e in events:
            start = e.get("start", {}).get("dateTime", e.get("start", {}).get("date", ""))
            time_str = start[11:16] if len(start) > 10 else ""
            lines.append(f"  {time_str} {e.get('summary', 'Без названия')}")
        lines.append("")
    else:
        lines.append(f"Событий в календаре на сегодня ({today_display}) нет.\n")

    lines.append("""Напиши утренний брифинг. Правила:

ВОССТАНОВЛЕНИЕ: начни с одной строки — оцени общий заряд по Body Battery и ЧСС покоя. Тон поддерживающий. Например: "Восстановился хорошо — батарея 78, пульс покоя 57."

СОН: оцени честно и по-человечески — хорошо/нормально/мало. SpO₂, дыхание и HRV упоминай нейтрально, без тревоги и медицинских предупреждений. Давление по Garmin не упоминай вообще.

ЗАДАЧИ: не перечисляй всё подряд. Выдели 2-3 самых важных — срочные, высокий приоритет или давно висят. Остальное одной строкой "ещё N задач".

КАЛЕНДАРЬ: коротко что предстоит.

Тон: спокойный, поддерживающий, конкретный. Без воды, без алармизма.""")
    return "\n".join(lines)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    yesterday = datetime.now() - timedelta(days=1)
    yesterday_iso = yesterday.strftime("%Y-%m-%d")
    yesterday_display = yesterday.strftime("%d.%m.%Y")
    today_display = datetime.now().strftime("%d.%m.%Y")

    try:
        await update.message.reply_text("📊 Собираю данные...")

        await update.message.reply_text("⌚ Загружаю данные Garmin...")
        sleep = get_garmin_sleep(yesterday_iso)
        daily_stats = get_garmin_daily_stats(yesterday_iso)
        if "error" in sleep:
            await update.message.reply_text(f"⚠️ Garmin сон: {sleep['error']}")
        else:
            bb = daily_stats.get("body_battery")
            hr = daily_stats.get("resting_hr")
            await update.message.reply_text(f"✅ Garmin OK — сон {sleep['total']}, батарея {bb}/100, ЧСС покоя {hr}")

        tasks = get_yesterday_tasks()
        events = get_today_events()

        await update.message.reply_text("🤖 Генерирую брифинг...")
        prompt = build_report_prompt(yesterday_display, today_display, sleep, daily_stats, tasks, events)

        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        report_text = response.content[0].text

        user_id = update.effective_user.id
        if user_id not in chat_histories:
            chat_histories[user_id] = []
        chat_histories[user_id].append({"role": "assistant", "content": f"[Утренний брифинг {today_display}]\n{report_text}"})
        if len(chat_histories[user_id]) > 40:
            chat_histories[user_id] = chat_histories[user_id][-40:]

        await update.message.reply_text(f"🌅 *Утренний брифинг {today_display}*\n\n{report_text}", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


def get_garmin_steps(date_str):
    try:
        client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        client.login()
        stats = client.get_stats(date_str)
        return stats.get("totalSteps", "")
    except Exception:
        return ""


def get_garmin_daily_stats(date_str):
    """Body Battery, ЧСС покоя, стресс за date_str (YYYY-MM-DD)."""
    try:
        client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        client.login()
        stats = client.get_stats(date_str)

        resting_hr = stats.get("restingHeartRate")
        stress_avg = stats.get("averageStressLevel")
        stress_max = stats.get("maxStressLevel")

        body_battery = None
        try:
            bb = client.get_body_battery(date_str, date_str)
            if bb:
                val = max((entry.get("endBodyBatteryValue") or 0) for entry in bb)
                if val > 0:
                    body_battery = val
        except Exception:
            pass

        def stress_label(val):
            if val is None:
                return None
            if val < 26:
                return "низкий"
            if val < 51:
                return "средний"
            if val < 76:
                return "высокий"
            return "очень высокий"

        return {
            "resting_hr": resting_hr,
            "stress_avg": stress_avg,
            "stress_label": stress_label(stress_avg),
            "body_battery": body_battery,
        }
    except Exception as e:
        return {"error": str(e)}


def get_tracker_sheet():
    today = datetime.now()
    month, year = today.month, today.year
    creds = get_google_creds(["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(TRACKER_SPREADSHEET_ID)
    for name in [
        f"{MONTH_NAMES_RU[month]} {year}",
        f"{MONTH_NAMES_EN[month]} {year}",
        f"{MONTH_NAMES_RU[month].lower()} {year}",
    ]:
        try:
            return sh.worksheet(name)
        except Exception:
            continue
    raise Exception(
        f"Не найден лист «{MONTH_NAMES_RU[month]} {year}» или «{MONTH_NAMES_EN[month]} {year}». "
        f"Создай такую вкладку в таблице."
    )


def today_col():
    # Column A=1 — названия, B=2 — "В дни", C=3 — день 1, D=4 — день 2 ...
    return datetime.now().day + 2


def bool_emoji(val):
    if val is True: return "✅"
    if val is False: return "❌"
    return "—"


def build_checkin_keyboard(user_id, include_weekly=False):
    habits = DAILY_HABITS + (WEEKLY_HABITS if include_weekly else [])
    state = checkin_state.get(user_id, {})
    keyboard = []
    for key, name in habits:
        val = state.get(key)
        yes = "✅✓" if val is True else "✅"
        no = "❌✓" if val is False else "❌"
        keyboard.append([
            InlineKeyboardButton(yes, callback_data=f"h:{key}:1"),
            InlineKeyboardButton(name, callback_data="noop"),
            InlineKeyboardButton(no, callback_data=f"h:{key}:0"),
        ])
    weight = checkin_state.get(user_id, {}).get("weight")
    weight_text = f"⚖️ Вес: {weight} кг" if weight else "⚖️ Вес: напиши «вес 90.5»"
    keyboard.append([InlineKeyboardButton(weight_text, callback_data="noop")])
    keyboard.append([InlineKeyboardButton("💾 Сохранить", callback_data="h:save")])
    return InlineKeyboardMarkup(keyboard)


async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not TRACKER_SPREADSHEET_ID:
        await update.message.reply_text(
            "⚙️ Добавь в Railway переменную:\n`TRACKER_SPREADSHEET_ID=1IuG70Hb3tqOAsPQHt57Zzk4rar4nmNF6`",
            parse_mode="Markdown"
        )
        return
    try:
        sheet = get_tracker_sheet()
        url = f"https://docs.google.com/spreadsheets/d/{TRACKER_SPREADSHEET_ID}"
        today = datetime.now()
        month = today.month
        year = today.year
        await update.message.reply_text(
            f"✅ Подключено! Лист «{sheet.title}»\n\n[Открыть таблицу]({url})",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def cmd_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in checkin_state:
        checkin_state[user_id] = {}
    today = datetime.now().strftime("%d.%m.%Y")
    is_sunday = datetime.now().weekday() == 6
    text = f"📋 *Чек-ин {today}*\n\nОтметь привычки:"
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=build_checkin_keyboard(user_id, include_weekly=is_sunday)
    )


async def handle_checkin_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "noop":
        return

    if user_id not in checkin_state:
        checkin_state[user_id] = {}

    if data == "h:save":
        await save_checkin_to_sheet(user_id, query, context)
        return

    parts = data.split(":")
    if len(parts) == 3:
        _, key, val = parts
        checkin_state[user_id][key] = (val == "1")

    is_sunday = datetime.now().weekday() == 6
    today = datetime.now().strftime("%d.%m.%Y")
    await query.edit_message_text(
        f"📋 *Чек-ин {today}*\n\nОтметь привычки:",
        parse_mode="Markdown",
        reply_markup=build_checkin_keyboard(user_id, include_weekly=is_sunday)
    )


async def save_checkin_to_sheet(user_id, query, context):
    today = datetime.now()
    today_str = today.strftime("%d.%m.%Y")
    yesterday_iso = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    today_iso = today.strftime("%Y-%m-%d")
    is_sunday = today.weekday() == 6
    state = checkin_state.get(user_id, {})
    col = today_col()

    def tick(val):
        if val is True: return "✓"
        if val is False: return ""
        return ""

    try:
        sheet = get_tracker_sheet()

        def w(row, value):
            if value not in (None, ""):
                sheet.update_cell(row, col, value)

        # Garmin шаги
        steps = get_garmin_steps(today_iso)
        w(HABIT_ROWS["steps"], steps)

        # Garmin сон до 00:00
        garmin = get_garmin_sleep(yesterday_iso)
        if "error" not in garmin and garmin.get("bed_time"):
            h = int(garmin["bed_time"].split(":")[0])
            w(HABIT_ROWS["sleep"], "✓" if h < 24 else "")

        # Привычки
        w(HABIT_ROWS["gratitude"], tick(state.get("gratitude")))
        w(HABIT_ROWS["reading"],   tick(state.get("reading")))
        w(HABIT_ROWS["finance"],   tick(state.get("finance")))
        w(HABIT_ROWS["no_news"],   tick(state.get("no_news")))
        w(HABIT_ROWS["no_social"], tick(state.get("no_social")))
        w(HABIT_ROWS["no_flour"],  tick(state.get("no_flour")))
        w(HABIT_ROWS["no_sugar"],  tick(state.get("no_sugar")))
        w(HABIT_ROWS["sport"],     tick(state.get("sport")))

        # Вес
        if state.get("weight"):
            w(HABIT_ROWS["weight"], state["weight"])

        # Еженедельные (только воскресенье)
        if is_sunday:
            w(HABIT_ROWS["planning"], tick(state.get("planning")))
            w(HABIT_ROWS["mom_call"], tick(state.get("mom_call")))
            w(HABIT_ROWS["flowers"],  tick(state.get("flowers")))

        url = f"https://docs.google.com/spreadsheets/d/{TRACKER_SPREADSHEET_ID}"
        await query.edit_message_text(
            f"✅ *Чек-ин сохранён — {today_str}!*\n\n"
            f"📊 Шаги: {steps or '—'}\n\n"
            f"[Открыть таблицу]({url})",
            parse_mode="Markdown"
        )
        checkin_state[user_id] = {}

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка сохранения: {e}")


async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Твой chat ID: `{update.effective_chat.id}`", parse_mode="Markdown")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_histories[user_id] = []
    await update.message.reply_text("🗑 История чата очищена.")


async def cmd_testbrief(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧪 Тест автобрифинга...")
    await send_morning_brief(context)


async def send_morning_brief(context: ContextTypes.DEFAULT_TYPE):
    if not TELEGRAM_CHAT_ID:
        return

    yesterday = datetime.now() - timedelta(days=1)
    yesterday_iso = yesterday.strftime("%Y-%m-%d")
    yesterday_display = yesterday.strftime("%d.%m.%Y")
    today_display = datetime.now().strftime("%d.%m.%Y")

    try:
        sleep = get_garmin_sleep(yesterday_iso)
        daily_stats = get_garmin_daily_stats(yesterday_iso)
        tasks = get_yesterday_tasks()
        events = get_today_events()

        prompt = build_report_prompt(yesterday_display, today_display, sleep, daily_stats, tasks, events)

        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        report_text = response.content[0].text

        if TELEGRAM_CHAT_ID not in chat_histories:
            chat_histories[TELEGRAM_CHAT_ID] = []
        chat_histories[TELEGRAM_CHAT_ID].append({"role": "assistant", "content": f"[Утренний брифинг {today_display}]\n{report_text}"})
        if len(chat_histories[TELEGRAM_CHAT_ID]) > 40:
            chat_histories[TELEGRAM_CHAT_ID] = chat_histories[TELEGRAM_CHAT_ID][-40:]

        await context.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"🌅 *Утренний брифинг {today_display}*\n\n{report_text}",
            parse_mode="Markdown",
        )
    except Exception as e:
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"❌ Ошибка автобрифинга: {e}")


BLOCKED_USERS = {7783251668}


async def _save_tasks_and_calendar(text, message, date_str=None):
    if date_str is None:
        date_str = datetime.now().strftime("%d.%m.%Y")

    # 1. Задачи → Google Sheets
    tasks_data = extract_tasks(text)
    save_to_sheets(tasks_data, date_str)

    reply = f"✅ *Резюме:*\n{tasks_data['summary']}\n\n📋 *Задачи ({len(tasks_data['tasks'])} шт.):*\n"
    for i, t in enumerate(tasks_data["tasks"], 1):
        icon = "🏢" if t.get("type") == "работа" else "👤"
        reply += f"\n{i}. {icon} {t['task']}\n   👤 {t['responsible']} | 📅 {t['deadline']} | ⚡ {t['priority']}\n"
    reply += "\n📊 Сохранено в Google Sheets"
    await message.reply_text(reply, parse_mode="Markdown")

    # 2. Календарь если есть дата+время
    parsed = classify_and_parse(text)
    if parsed["type"] == "calendar" and parsed.get("date") and parsed.get("time"):
        link = create_calendar_event(
            title=parsed["title"],
            date_str=parsed["date"],
            time_str=parsed["time"],
            duration_minutes=parsed.get("duration_minutes", 60),
            description=parsed.get("description") or ""
        )
        cal_reply = (
            f"📅 *+ Событие в календарь:*\n\n"
            f"*{parsed['title']}*\n"
            f"📆 {parsed['date']} в {parsed['time']}\n"
            f"⏱ {parsed.get('duration_minutes', 60)} мин\n\n"
            f"[Открыть в Google Calendar]({link})"
        )
        await message.reply_text(cal_reply, parse_mode="Markdown")

    # 3. Знания → Google Drive + Notion
    try:
        knowledge = extract_knowledge(text)
        title = knowledge.get("title", "Без названия")

        drive_link = save_transcript_to_drive(text, title, date_str)
        drive_msg = f"[📁 Открыть в Drive]({drive_link})" if drive_link else "📁 Drive: ошибка загрузки"

        notion_url = save_to_notion(knowledge, text, date_str)
        notion_msg = f"[🧠 Открыть в Notion]({notion_url})" if notion_url else "🧠 Notion: не настроен (добавь NOTION_TOKEN)"

        has_content = any([knowledge.get("decisions"), knowledge.get("processes"), knowledge.get("regulations")])
        if has_content:
            kb_reply = f"*📚 База знаний обновлена*\n\n"
            if knowledge.get("decisions"):
                kb_reply += "*Решения:*\n" + "\n".join(f"• {d}" for d in knowledge["decisions"]) + "\n\n"
            if knowledge.get("regulations"):
                kb_reply += "*Регламенты:*\n" + "\n".join(f"• {r}" for r in knowledge["regulations"]) + "\n\n"
            if knowledge.get("processes"):
                kb_reply += "*Процессы:*\n" + "\n".join(f"• {p}" for p in knowledge["processes"]) + "\n\n"
            kb_reply += f"{drive_msg}  |  {notion_msg}"
            await message.reply_text(kb_reply, parse_mode="Markdown")
        else:
            await message.reply_text(f"📁 Транскрипт сохранён\n{drive_msg}  |  {notion_msg}", parse_mode="Markdown")
    except Exception as e:
        await message.reply_text(f"⚠️ База знаний: {e}")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id in BLOCKED_USERS:
        return
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
        fwd_origin = update.message.forward_origin
        date_str = (fwd_origin.date + timedelta(hours=7)).strftime("%d.%m.%Y") if fwd_origin else None
        await _save_tasks_and_calendar(transcript, update.message, date_str=date_str)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
    finally:
        os.unlink(tmp_path)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in BLOCKED_USERS:
        return
    text = update.message.text

    if text.lower().startswith("вес "):
        try:
            weight = float(text.split()[1].replace(",", "."))
            if user_id not in checkin_state:
                checkin_state[user_id] = {}
            checkin_state[user_id]["weight"] = weight
            await update.message.reply_text(f"⚖️ Вес {weight} кг записан в чек-ин.")
            return
        except Exception:
            pass

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    intent = classify_text_intent(text)

    if intent in ("tasks", "calendar"):
        await update.message.reply_text("🤖 Анализирую как задачи...")
        fwd_origin = update.message.forward_origin
        date_str = (fwd_origin.date + timedelta(hours=7)).strftime("%d.%m.%Y") if fwd_origin else None
        await _save_tasks_and_calendar(text, update.message, date_str=date_str)
        return

    if user_id not in chat_histories:
        chat_histories[user_id] = []

    chat_histories[user_id].append({"role": "user", "content": text})
    if len(chat_histories[user_id]) > 40:
        chat_histories[user_id] = chat_histories[user_id][-40:]

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
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CommandHandler("testbrief", cmd_testbrief))
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler("checkin", cmd_checkin))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CallbackQueryHandler(handle_checkin_callback))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Автобриф в 8:30 по Нячангу (UTC+7 = 01:30 UTC)
    app.job_queue.run_daily(send_morning_brief, time=dtime(hour=1, minute=30))

    print("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
