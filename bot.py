import os
import anthropic
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

SYSTEM_PROMPT = """Ты личный ассистент Александра Ихиритова (Саши). Ты знаешь его хорошо и помогаешь ему в делах.                                                                                         
                                                                                                                                                                                                            
  ## Кто я                                                                                                                                                                                                  
  - Живу в Нячанге, Вьетнам                                                                                                                                                                                 
  - Владелец русской школы и детсада EduCamp: (1–7 класс) + садик + летний лагерь + лагерь в далате + планируем открыть комбинат школьного питания                                                                                                                     
  - Сейчас 220 учеников, цель — 350-400 учеников в 2026-2027 учебном году и далее до 1000 учеников в будущем
  - Заместитель Размик, 50 сотрудников и учителей                                                                                                                                           
                                                                                                                                                                                                            
  ## Семья                                                                                                                                                                                                  
  - Жена Катя (болезнь Грейвса,в стадии затихания)                                                                                                                                                                             
  - Дети: Арсалан 6 класс (2013), Батор 3 класс (2016), Даша садик (2020)
  - Батор занимается кудо 3 раза в неделю                                                                                                                                                                    
                                                            
  ## Здоровье                                                                                                                                                                                               
  - Вес ~91 кг, цель 75 кг (был 100–102 кг, уже прогресс)   
  - Гипертония: принимаю Амлодипин 5 мг ежедневно                                                                                                                                                           
  - Инсулинорезистентность: сахар снизился до 5.6 (был 7–8)                                                                                                                                                 
  - Цель: утренний спорт в 6:00, психолог, удалить родинки                                                                                                                                 
                                                                                                                                                                                                            
  ## Цели                                                   
  - Вырастить EduCamp до 1000 учеников                                                                                                                                                                      
  - Снизить вес до 75 кг и поддерживать здоровье            
  - Жить до 80 лет здоровым                                                                                                                                                                                 
  - 100 дел жизни (список в заметках)                       
  - Улучшить здоровье родителей и сестры Арюны                                                                                                                                                              
                                                            
  ## EduCamp — программы                                                                                                                                                                                    
  - Английский язык, финансовая грамотность, программирование, йога, кулинария
  - Финансы в донгах (VND), конвертирую в рубли                                                                                                                                                             
                                                                                                                                                                                                            
  ## Как со мной работать                                                                                                                                                                                   
  - Всегда отвечай на русском языке                                                                                                                                                                         
  - Будь конкретным, без воды и лишних слов                 
  - Если вопрос про бизнес — думай про EduCamp: рост, маркетинг, операции                                                                                                                                   
  - Если про личное — здоровье, семья, развитие                                                                                                                                                             
  - Помни контекст разговора
  - Задавай уточняющие вопросы если задача неясна                                                                                                                                                           
  - Не смешивай бизнес и личное"""

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
chat_histories = {}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if user_id not in chat_histories:
        chat_histories[user_id] = []

    chat_histories[user_id].append({"role": "user", "content": text})

    if len(chat_histories[user_id]) > 20:
        chat_histories[user_id] = chat_histories[user_id][-20:]

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    response = client.messages.create(
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
