import os
import json
import base64
import tempfile
import anthropic
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
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
TRACKER_SPREADSHEET_ID = os.environ.get("TRACKER_SPREADSHEET_ID", "")
SPREADSHEET_ID = "1OiwzcHadBvDJdn4qgf0wwueaHo7p5u_KXNfEvnbMTu0"
CALENDAR_ID = "ikhiritov@gmail.com"
VIETNAM_TZ_OFFSET = "+07:00"

DAILY_HABITS = [
    ("gratitude", "Благодарности"),
    ("reading", "Чтение 10мин"),
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
TRACKER_HEADERS = [
    "Дата", "Шаги", "Сон до 00:00", "Благодарности",
    "Калории", "Белок (г)", "Чтение 10мин",
    "Без новостей", "Без соцсетей", "Без мучного", "Без сахара",
    "Спорт", "Вес (кг)", "Планёрка", "Звонок маме", "Цветы Кате",
]

checkin_state = {}  # {user_id: {habit_key: True/False, 'weight': float}}
food_log = {}       # {user_id: {'date': str, 'calories': int, 'protein': int}}

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


def build_report_prompt(yesterday_display, today_display, sleep, tasks, events):
    lines = [f"Утренний брифинг. Вчера: {yesterday_display}, сегодня: {today_display}\n"]

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

    lines.append("Напиши утренний брифинг: как спал (честно), что важного в задачах вчера, что предстоит сегодня по календарю. Если что-то требует внимания — скажи прямо. Без воды.")
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
        if "error" in sleep:
            await update.message.reply_text(f"⚠️ Garmin: {sleep['error']}")
        else:
            await update.message.reply_text(f"✅ Garmin OK — сон {sleep['total']}")

        tasks = get_yesterday_tasks()
        events = get_today_events()

        await update.message.reply_text("🤖 Генерирую брифинг...")
        prompt = build_report_prompt(yesterday_display, today_display, sleep, tasks, events)

        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        report_text = response.content[0].text
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


def get_or_create_tracker_sheet():
    global TRACKER_SPREADSHEET_ID
    creds = get_google_creds([
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ])
    gc = gspread.authorize(creds)
    if TRACKER_SPREADSHEET_ID:
        return gc.open_by_key(TRACKER_SPREADSHEET_ID).sheet1
    sh = gc.create("Трекер привычек — Саша")
    sh.share("ikhiritov@gmail.com", perm_type="user", role="writer")
    TRACKER_SPREADSHEET_ID = sh.id
    sh.sheet1.append_row(TRACKER_HEADERS)
    return sh.sheet1


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


def food_summary(user_id):
    today = datetime.now().strftime("%d.%m.%Y")
    log = food_log.get(user_id, {})
    if log.get("date") != today:
        return "нет данных"
    return f"{log.get('calories', 0)} ккал | {log.get('protein', 0)}г белка"


async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ Создаю таблицу трекера...")
    try:
        sheet = get_or_create_tracker_sheet()
        url = f"https://docs.google.com/spreadsheets/d/{TRACKER_SPREADSHEET_ID}"
        await update.message.reply_text(
            f"✅ Таблица готова!\n\n"
            f"[Открыть таблицу]({url})\n\n"
            f"Добавь в Railway переменную:\n`TRACKER_SPREADSHEET_ID={TRACKER_SPREADSHEET_ID}`",
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
    text = (
        f"📋 *Чек-ин {today}*\n\n"
        f"🍽 Питание: {food_summary(user_id)}\n"
        f"_(кидай фото еды прямо в чат)_\n\n"
        f"Отметь привычки:"
    )
    msg = await update.message.reply_text(
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
    text = (
        f"📋 *Чек-ин {today}*\n\n"
        f"🍽 Питание: {food_summary(user_id)}\n"
        f"_(кидай фото еды прямо в чат)_\n\n"
        f"Отметь привычки:"
    )
    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=build_checkin_keyboard(user_id, include_weekly=is_sunday)
    )


async def save_checkin_to_sheet(user_id, query, context):
    today = datetime.now()
    today_str = today.strftime("%d.%m.%Y")
    yesterday_iso = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    today_iso = today.strftime("%Y-%m-%d")
    is_sunday = today.weekday() == 6
    state = checkin_state.get(user_id, {})

    steps = get_garmin_steps(today_iso)

    garmin = get_garmin_sleep(yesterday_iso)
    sleep_ok = ""
    if "error" not in garmin and garmin.get("bed_time"):
        h = int(garmin["bed_time"].split(":")[0])
        sleep_ok = "✅" if h < 24 else "❌"

    log = food_log.get(user_id, {})
    calories = log.get("calories", "") if log.get("date") == today_str else ""
    protein = log.get("protein", "") if log.get("date") == today_str else ""

    row = [
        today_str, steps, sleep_ok,
        bool_emoji(state.get("gratitude")),
        calories, protein,
        bool_emoji(state.get("reading")),
        bool_emoji(state.get("no_news")),
        bool_emoji(state.get("no_social")),
        bool_emoji(state.get("no_flour")),
        bool_emoji(state.get("no_sugar")),
        bool_emoji(state.get("sport")),
        state.get("weight", ""),
        bool_emoji(state.get("planning")) if is_sunday else "",
        bool_emoji(state.get("mom_call")) if is_sunday else "",
        bool_emoji(state.get("flowers")) if is_sunday else "",
    ]

    try:
        sheet = get_or_create_tracker_sheet()
        sheet.append_row(row)
        url = f"https://docs.google.com/spreadsheets/d/{TRACKER_SPREADSHEET_ID}"
        summary = (
            f"✅ *Чек-ин сохранён!*\n\n"
            f"📊 Шаги: {steps or '—'} | Сон: {sleep_ok or '—'}\n"
            f"🍽 {calories or 0} ккал | {protein or 0}г белка\n\n"
            f"[Открыть таблицу]({url})"
        )
        await query.edit_message_text(summary, parse_mode="Markdown")
        if TRACKER_SPREADSHEET_ID and "TRACKER_SPREADSHEET_ID" not in os.environ:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⚙️ Добавь в Railway env var:\n`TRACKER_SPREADSHEET_ID={TRACKER_SPREADSHEET_ID}`",
                parse_mode="Markdown"
            )
        checkin_state[user_id] = {}
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка сохранения: {e}")


async def scheduled_checkin(context: ContextTypes.DEFAULT_TYPE):
    if not TELEGRAM_CHAT_ID:
        return
    if TELEGRAM_CHAT_ID not in checkin_state:
        checkin_state[TELEGRAM_CHAT_ID] = {}
    today = datetime.now().strftime("%d.%m.%Y")
    is_sunday = datetime.now().weekday() == 6
    text = (
        f"📋 *Чек-ин {today}*\n\n"
        f"🍽 Питание: {food_summary(TELEGRAM_CHAT_ID)}\n"
        f"_(кидай фото еды прямо в чат)_\n\n"
        f"Отметь привычки:"
    )
    await context.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=build_checkin_keyboard(TELEGRAM_CHAT_ID, include_weekly=is_sunday)
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("🍽 Анализирую еду...")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = tmp.name
    try:
        with open(tmp_path, "rb") as f:
            img_data = base64.standard_b64encode(f.read()).decode("utf-8")
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data}},
                {"type": "text", "text": "Это фото еды. Оцени калории и белок. Верни ТОЛЬКО JSON без лишнего текста: {\"calories\": 450, \"protein\": 25, \"description\": \"краткое название блюда\"}"}
            ]}]
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())
        cal = data.get("calories", 0)
        prot = data.get("protein", 0)
        desc = data.get("description", "")
        today = datetime.now().strftime("%d.%m.%Y")
        if user_id not in food_log or food_log[user_id].get("date") != today:
            food_log[user_id] = {"date": today, "calories": 0, "protein": 0}
        food_log[user_id]["calories"] += cal
        food_log[user_id]["protein"] += prot
        total_cal = food_log[user_id]["calories"]
        total_prot = food_log[user_id]["protein"]
        await update.message.reply_text(
            f"🍽 *{desc}*\n~{cal} ккал | ~{prot}г белка\n\n📊 За сегодня: {total_cal} ккал | {total_prot}г белка",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Не смог проанализировать: {e}")
    finally:
        os.unlink(tmp_path)


async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Твой chat ID: `{update.effective_chat.id}`", parse_mode="Markdown")


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
        tasks = get_yesterday_tasks()
        events = get_today_events()

        prompt = build_report_prompt(yesterday_display, today_display, sleep, tasks, events)

        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        report_text = response.content[0].text
        await context.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"🌅 *Утренний брифинг {today_display}*\n\n{report_text}",
            parse_mode="Markdown",
        )
    except Exception as e:
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"❌ Ошибка автобрифинга: {e}")


BLOCKED_USERS = {7783251668}

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
                icon = "🏢" if t.get("type") == "работа" else "👤"
            reply += f"\n{i}. {icon} {t['task']}\n   👤 {t['responsible']} | 📅 {t['deadline']} | ⚡ {t['priority']}\n"
        reply += "\n📊 Всё записано в Google Sheets!"

        await update.message.reply_text(reply, parse_mode="Markdown")
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
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CommandHandler("testbrief", cmd_testbrief))
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler("checkin", cmd_checkin))
    app.add_handler(CallbackQueryHandler(handle_checkin_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Автобриф в 8:30 по Нячангу (UTC+7 = 01:30 UTC)
    app.job_queue.run_daily(send_morning_brief, time=dtime(hour=1, minute=30))
    # Чек-ин в 22:00 по Нячангу (UTC+7 = 15:00 UTC)
    app.job_queue.run_daily(scheduled_checkin, time=dtime(hour=15, minute=0))

    print("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
