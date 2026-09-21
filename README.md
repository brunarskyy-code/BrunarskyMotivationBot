# BrunarskyMotivationBot

Telegram-бот для системи фінансової мотивації Влада і Ромчика.

## Railway variables
BOT_TOKEN = токен від BotFather
ADMIN_SECRET = придуманий Андрієм секрет для першої реєстрації адміністратора

## Start
Railway Start Command:
python bot.py

Після запуску:
1. Андрій відкриває бота і надсилає `/admin ВАШ_СЕКРЕТ`.
2. Влад: `/join Влад`
3. Ромчик: `/join Ромчик`
4. Андрій підтверджує кожного кнопкою.

Дані зберігаються в SQLite. Для постійного збереження на Railway бажано підключити Volume
і задати DB_PATH=/data/motivation.db.
