import os
import sqlite3
from datetime import datetime

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")
ADMIN_TG_ID = int(os.environ.get("ADMIN_TG_ID", "0") or 0)
DB = os.environ.get("DB_PATH", "motivation.db")

BASE = 2000
BONUS_CAP = 4000
STUDY_CAP = 1500

CATS = {
    "sport": ("Спорт", 500),
    "books": ("Книги", 600),
    "help": ("Допомога батькам / сімейний проєкт", 1500),
    "development": ("Саморозвиток", 700),
}

MENU_CHILD = ReplyKeyboardMarkup([
    ["📝 Підсумки місяця", "📊 Мій підсумок"],
    ["➕ Додати досягнення", "✏️ Мої записи"],
    ["❓ Правила"]
], resize_keyboard=True)

MENU_ADMIN = ReplyKeyboardMarkup([
    ["👥 Звіти Влада і Ромчика", "✅ На підтвердження"],
    ["📊 Підсумок місяця", "🗑 Скинути місяць"],
    ["❓ Правила"]
], resize_keyboard=True)


# =========================================================
# DATABASE
# =========================================================

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row

    c.execute("""
        CREATE TABLE IF NOT EXISTS users(
            tg_id INTEGER PRIMARY KEY,
            role TEXT,
            name TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS subjects(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            month TEXT,
            subject TEXT,
            avg REAL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS entries(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            month TEXT,
            category TEXT,
            description TEXT,
            amount INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending'
        )
    """)

    c.commit()
    return c


def month_key():
    return datetime.now().strftime("%Y-%m")


def get_user(tg_id):
    c = db()
    r = c.execute(
        "SELECT * FROM users WHERE tg_id=?",
        (tg_id,)
    ).fetchone()
    c.close()

    if not r and ADMIN_TG_ID and tg_id == ADMIN_TG_ID:
        register(tg_id, "admin", "Андрій")
        c = db()
        r = c.execute(
            "SELECT * FROM users WHERE tg_id=?",
            (tg_id,)
        ).fetchone()
        c.close()

    return r


def register(tg_id, role, name):
    c = db()
    c.execute(
        "INSERT OR REPLACE INTO users(tg_id,role,name) VALUES(?,?,?)",
        (tg_id, role, name)
    )
    c.commit()
    c.close()


def admin_id():
    if ADMIN_TG_ID:
        return ADMIN_TG_ID

    c = db()
    r = c.execute(
        "SELECT tg_id FROM users WHERE role='admin' LIMIT 1"
    ).fetchone()
    c.close()

    return r["tg_id"] if r else None


# =========================================================
# CALCULATIONS
# =========================================================

def study_level(avg):
    if avg >= 11:
        return 1500
    if avg >= 10.5:
        return 1200
    if avg >= 10:
        return 1000
    if avg >= 9.5:
        return 700
    return 0


def study_bonus(tg_id, month):
    c = db()
    r = c.execute("""
        SELECT COALESCE(SUM(amount),0) s
        FROM entries
        WHERE tg_id=? AND month=?
        AND category='study'
        AND status='approved'
    """, (tg_id, month)).fetchone()
    c.close()
    return int(r["s"])


def approved_bonus(tg_id, month):
    c = db()
    r = c.execute("""
        SELECT COALESCE(SUM(amount),0) s
        FROM entries
        WHERE tg_id=? AND month=?
        AND category!='study'
        AND status='approved'
    """, (tg_id, month)).fetchone()
    c.close()
    return int(r["s"])


def summary(tg_id, month):
    s = study_bonus(tg_id, month)
    other = approved_bonus(tg_id, month)
    bonus = min(BONUS_CAP, s + other)
    total = BASE + bonus
    return s, other, bonus, total


# =========================================================
# START / REGISTRATION
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)

    if u:
        menu = MENU_ADMIN if u["role"] == "admin" else MENU_CHILD
        await update.message.reply_text(
            f"Привіт, {u['name']}!",
            reply_markup=menu
        )
        return

    await update.message.reply_text(
        "Привіт!\n\n"
        "Для реєстрації:\n"
        "• Андрій: /admin СЕКРЕТ\n"
        "• Влад: /join Влад\n"
        "• Ромчик: /join Ромчик\n\n"
        "Після заявки Андрій підтвердить доступ."
    )


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_SECRET:
        await update.message.reply_text(
            "На сервері не задано ADMIN_SECRET."
        )
        return

    if not context.args or context.args[0] != ADMIN_SECRET:
        await update.message.reply_text("Невірний секрет.")
        return

    register(update.effective_user.id, "admin", "Андрій")

    await update.message.reply_text(
        "Андрій зареєстрований як адміністратор.",
        reply_markup=MENU_ADMIN
    )


async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0] not in ("Влад", "Ромчик"):
        await update.message.reply_text(
            "Напиши /join Влад або /join Ромчик"
        )
        return

    name = context.args[0]
    aid = admin_id()

    if not aid:
        await update.message.reply_text(
            "Спочатку Андрій має зареєструватися."
        )
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"✅ Підтвердити {name}",
            callback_data=f"joinok:{update.effective_user.id}:{name}"
        ),
        InlineKeyboardButton(
            "❌ Відхилити",
            callback_data=f"joinno:{update.effective_user.id}"
        )
    ]])

    await context.bot.send_message(
        aid,
        f"Запит на доступ: {name}\n"
        f"Telegram ID: {update.effective_user.id}",
        reply_markup=kb
    )

    await update.message.reply_text(
        "Запит надіслано Андрію."
    )


async def join_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    u = get_user(q.from_user.id)

    if not u or u["role"] != "admin":
        return

    parts = q.data.split(":")
    tg = int(parts[1])

    if parts[0] == "joinok":
        name = parts[2]

        register(tg, "child", name)

        await q.edit_message_text(
            f"✅ {name} підключений."
        )

        await context.bot.send_message(
            tg,
            f"Доступ підтверджено. Привіт, {name}!",
            reply_markup=MENU_CHILD
        )

    else:
        await q.edit_message_text(
            "❌ Запит відхилено."
        )

        await context.bot.send_message(
            tg,
            "Запит на доступ відхилено."
        )


# =========================================================
# STUDY
# =========================================================

SUBJECT, AVG = range(2)


async def report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)

    if not u or u["role"] != "child":
        return ConversationHandler.END

    m = month_key()

    c = db()
    approved = c.execute("""
        SELECT id FROM entries
        WHERE tg_id=? AND month=?
        AND category='study'
        AND status='approved'
        LIMIT 1
    """, (u["tg_id"], m)).fetchone()
    c.close()

    if approved:
        await update.message.reply_text(
            "🔒 Навчання за цей місяць уже підтверджене Андрієм.\n"
            "Змінити його вже не можна.",
            reply_markup=MENU_CHILD
        )
        return ConversationHandler.END

    context.user_data["subjects"] = []

    kb = ReplyKeyboardMarkup(
        [["❌ Скасувати заповнення"]],
        resize_keyboard=True
    )

    await update.message.reply_text(
        "📚 Починаємо навчання.\n\n"
        "Напиши назву першого предмета.\n"
        "Коли всі предмети введені — напиши ГОТОВО.\n\n"
        "Можеш у будь-який момент натиснути "
        "«❌ Скасувати заповнення».",
        reply_markup=kb
    )

    return SUBJECT


async def subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()

    if t == "❌ Скасувати заповнення":
        context.user_data.pop("subjects", None)
        context.user_data.pop("current_subject", None)

        await update.message.reply_text(
            "Заповнення скасовано. Нічого не збережено.",
            reply_markup=MENU_CHILD
        )
        return ConversationHandler.END

    if t.upper() == "ГОТОВО":
        items = context.user_data.get("subjects", [])

        if not items:
            await update.message.reply_text(
                "Поки немає жодного предмета."
            )
            return SUBJECT

        m = month_key()
        tg = update.effective_user.id

        c = db()

        # Не дозволяємо змінювати вже затверджене
        approved = c.execute("""
            SELECT id FROM entries
            WHERE tg_id=? AND month=?
            AND category='study'
            AND status='approved'
            LIMIT 1
        """, (tg, m)).fetchone()

        if approved:
            c.close()

            await update.message.reply_text(
                "🔒 Цей місяць уже підтверджено.",
                reply_markup=MENU_CHILD
            )
            return ConversationHandler.END

        # Видаляємо попередню НЕПІДТВЕРДЖЕНУ версію
        c.execute(
            "DELETE FROM subjects WHERE tg_id=? AND month=?",
            (tg, m)
        )

        c.execute("""
            DELETE FROM entries
            WHERE tg_id=? AND month=?
            AND category='study'
            AND status!='approved'
        """, (tg, m))

        c.executemany("""
            INSERT INTO subjects(tg_id,month,subject,avg)
            VALUES(?,?,?,?)
        """, [
            (tg, m, s, a)
            for s, a in items
        ])

        calc = round(
            sum(study_level(float(a)) for _, a in items)
            / len(items)
        )

        calc = min(calc, STUDY_CAP)

        details = "\n".join(
            f"• {subj}: {a:g}"
            for subj, a in items
        )

        cur = c.execute("""
            INSERT INTO entries(
                tg_id,month,category,
                description,amount,status
            )
            VALUES(?,?,?,?,?,'pending')
        """, (
            tg,
            m,
            "study",
            details,
            calc
        ))

        eid = cur.lastrowid

        c.commit()
        c.close()

        u = get_user(tg)
        aid = admin_id()

        if aid:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"✅ Підтвердити {calc} грн",
                    callback_data=f"studyok:{eid}"
                ),
                InlineKeyboardButton(
                    "❌ Відхилити",
                    callback_data=f"studyno:{eid}"
                )
            ]])

            await context.bot.send_message(
                aid,
                f"📚 {u['name']} — навчання {m}\n\n"
                f"{details}\n\n"
                f"Розрахунок: {calc} грн\n"
                f"⏳ Очікує підтвердження.",
                reply_markup=kb
            )

        await update.message.reply_text(
            f"✅ Дані збережені.\n"
            f"Розрахунок: {calc} грн.\n\n"
            f"До підтвердження Андрієм ти можеш "
            f"виправити або видалити цей запис через "
            f"«✏️ Мої записи».",
            reply_markup=MENU_CHILD
        )

        context.user_data.pop("subjects", None)
        context.user_data.pop("current_subject", None)

        return ConversationHandler.END

    context.user_data["current_subject"] = t

    await update.message.reply_text(
        f"Середній бал з «{t}» за місяць (0–12):"
    )

    return AVG


async def avg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Скасувати заповнення":
        context.user_data.pop("subjects", None)
        context.user_data.pop("current_subject", None)

        await update.message.reply_text(
            "Заповнення скасовано. Нічого не збережено.",
            reply_markup=MENU_CHILD
        )

        return ConversationHandler.END

    try:
        a = float(
            update.message.text.replace(",", ".")
        )
    except ValueError:
        await update.message.reply_text(
            "Введи число, наприклад 10,7."
        )
        return AVG

    if not 0 <= a <= 12:
        await update.message.reply_text(
            "Бал має бути від 0 до 12."
        )
        return AVG

    s = context.user_data["current_subject"]

    context.user_data["subjects"].append(
        (s, a)
    )

    await update.message.reply_text(
        "✅ Збережено.\n"
        "Напиши наступний предмет або ГОТОВО."
    )

    return SUBJECT


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("subjects", None)
    context.user_data.pop("current_subject", None)
    context.user_data.pop("cat", None)

    await update.message.reply_text(
        "Скасовано.",
        reply_markup=MENU_CHILD
    )

    return ConversationHandler.END


async def study_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    u = get_user(q.from_user.id)

    if not u or u["role"] != "admin":
        return

    action, eid = q.data.split(":")
    eid = int(eid)

    c = db()

    row = c.execute("""
        SELECT * FROM entries
        WHERE id=? AND category='study'
    """, (eid,)).fetchone()

    if not row:
        c.close()
        await q.answer("Запис уже видалено.", show_alert=True)
        return

    if row["status"] != "pending":
        c.close()
        await q.answer(
            "Цей запис уже оброблений.",
            show_alert=True
        )
        return

    if action == "studyok":
        c.execute(
            "UPDATE entries SET status='approved' WHERE id=?",
            (eid,)
        )
        c.commit()
        c.close()

        await q.edit_message_text(
            q.message.text +
            f"\n\n🔒 ПІДТВЕРДЖЕНО: {row['amount']} грн"
        )

        await context.bot.send_message(
            row["tg_id"],
            f"✅ Навчання підтверджено: "
            f"{row['amount']} грн.\n"
            f"🔒 Дані зафіксовані."
        )

    else:
        c.execute(
            "UPDATE entries SET status='rejected', amount=0 WHERE id=?",
            (eid,)
        )
        c.commit()
        c.close()

        await q.edit_message_text(
            q.message.text +
            "\n\n❌ ВІДХИЛЕНО"
        )

        await context.bot.send_message(
            row["tg_id"],
            "❌ Звіт по навчанню відхилено.\n"
            "Можеш виправити дані та подати заново."
        )


# =========================================================
# ACHIEVEMENTS
# =========================================================

CAT, DESC = range(2, 4)


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)

    if not u or u["role"] != "child":
        return ConversationHandler.END

    kb = ReplyKeyboardMarkup([
        ["Спорт", "Книги"],
        ["Допомога", "Саморозвиток"],
        ["❌ Скасувати"]
    ], resize_keyboard=True)

    await update.message.reply_text(
        "Що додаємо?",
        reply_markup=kb
    )

    return CAT


async def cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mp = {
        "Спорт": "sport",
        "Книги": "books",
        "Допомога": "help",
        "Саморозвиток": "development"
    }

    if update.message.text in ("Скасувати", "❌ Скасувати"):
        return await cancel(update, context)

    if update.message.text not in mp:
        await update.message.reply_text(
            "Вибери категорію кнопкою."
        )
        return CAT

    context.user_data["cat"] = mp[update.message.text]

    await update.message.reply_text(
        "Коротко опиши результат.\n\n"
        "Для скасування натисни «❌ Скасувати»."
    )

    return DESC


async def desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Скасувати":
        return await cancel(update, context)

    cat_key = context.user_data["cat"]
    d = update.message.text.strip()
    m = month_key()

    c = db()

    cur = c.execute("""
        INSERT INTO entries(
            tg_id,month,category,
            description,amount,status
        )
        VALUES(?,?,?,?,0,'pending')
    """, (
        update.effective_user.id,
        m,
        cat_key,
        d
    ))

    eid = cur.lastrowid

    c.commit()
    c.close()

    u = get_user(update.effective_user.id)
    aid = admin_id()

    label, maxv = CATS[cat_key]

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "0",
                callback_data=f"amt:{eid}:0"
            ),
            InlineKeyboardButton(
                "100",
                callback_data=f"amt:{eid}:100"
            ),
            InlineKeyboardButton(
                "300",
                callback_data=f"amt:{eid}:300"
            ),
            InlineKeyboardButton(
                "500",
                callback_data=f"amt:{eid}:500"
            ),
            InlineKeyboardButton(
                "700",
                callback_data=f"amt:{eid}:700"
            ),
        ],
        [
            InlineKeyboardButton(
                "Інша сума",
                callback_data=f"custom:{eid}"
            )
        ]
    ])

    if aid:
        await context.bot.send_message(
            aid,
            f"🔔 {u['name']}: {label}\n"
            f"{d}\n\n"
            f"Максимум категорії: до {maxv} грн.",
            reply_markup=kb
        )

    await update.message.reply_text(
        "✅ Запис збережено.\n"
        "До підтвердження Андрієм його можна "
        "змінити або видалити через «✏️ Мої записи».",
        reply_markup=MENU_CHILD
    )

    context.user_data.pop("cat", None)

    return ConversationHandler.END


async def amount_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    u = get_user(q.from_user.id)

    if not u or u["role"] != "admin":
        return

    _, eid, amt = q.data.split(":")
    eid = int(eid)
    amt = int(amt)

    c = db()

    row = c.execute(
        "SELECT * FROM entries WHERE id=?",
        (eid,)
    ).fetchone()

    if not row:
        c.close()
        await q.answer(
            "Запис уже видалено хлопцем.",
            show_alert=True
        )
        return

    if row["status"] != "pending":
        c.close()
        await q.answer(
            "Цей запис уже оброблений.",
            show_alert=True
        )
        return

    if row["category"] not in CATS:
        c.close()
        return

    maxv = CATS[row["category"]][1]
    amt = max(0, min(amt, maxv))

    c.execute("""
        UPDATE entries
        SET amount=?, status='approved'
        WHERE id=?
    """, (amt, eid))

    c.commit()
    c.close()

    await q.edit_message_text(
        q.message.text +
        f"\n\n🔒 ПІДТВЕРДЖЕНО: {amt} грн"
    )

    await context.bot.send_message(
        row["tg_id"],
        f"✅ {CATS[row['category']][0]}: "
        f"Андрій підтвердив {amt} грн.\n"
        f"🔒 Запис зафіксований."
    )


async def custom_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    u = get_user(q.from_user.id)

    if not u or u["role"] != "admin":
        return

    context.user_data["custom_eid"] = int(
        q.data.split(":")[1]
    )

    await q.message.reply_text(
        "Введи суму командою /sum число\n"
        "Наприклад: /sum 450"
    )


async def set_sum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)

    if not u or u["role"] != "admin":
        return

    eid = context.user_data.get("custom_eid")

    if not eid or not context.args:
        await update.message.reply_text(
            "Спочатку натисни «Інша сума»."
        )
        return

    try:
        amt = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "Приклад: /sum 450"
        )
        return

    c = db()

    row = c.execute(
        "SELECT * FROM entries WHERE id=?",
        (eid,)
    ).fetchone()

    if not row:
        c.close()
        await update.message.reply_text(
            "Цей запис уже видалений."
        )
        return

    if row["status"] != "pending":
        c.close()
        await update.message.reply_text(
            "Цей запис уже оброблений."
        )
        return

    maxv = CATS[row["category"]][1]
    amt = max(0, min(amt, maxv))

    c.execute("""
        UPDATE entries
        SET amount=?, status='approved'
        WHERE id=?
    """, (amt, eid))

    c.commit()
    c.close()

    await update.message.reply_text(
        f"✅ Підтверджено {amt} грн."
    )

    await context.bot.send_message(
        row["tg_id"],
        f"✅ {CATS[row['category']][0]}: "
        f"Андрій підтвердив {amt} грн.\n"
        f"🔒 Запис зафіксований."
    )

    context.user_data.pop("custom_eid", None)


# =========================================================
# CHILD: VIEW / EDIT / DELETE
# =========================================================

async def my_entries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)

    if not u or u["role"] != "child":
        return

    m = month_key()

    c = db()

    rows = c.execute("""
        SELECT * FROM entries
        WHERE tg_id=? AND month=?
        ORDER BY id
    """, (u["tg_id"], m)).fetchall()

    c.close()

    if not rows:
        await update.message.reply_text(
            "За цей місяць записів ще немає."
        )
        return

    await update.message.reply_text(
        f"✏️ Твої записи за {m}\n\n"
        f"⏳ — можна змінити або видалити\n"
        f"🔒 — Андрій уже підтвердив"
    )

    for r in rows:
        if r["category"] == "study":
            label = "📚 Навчання"
        else:
            label = CATS.get(
                r["category"],
                (r["category"], 0)
            )[0]

        if r["status"] == "approved":
            await update.message.reply_text(
                f"🔒 {label}\n"
                f"{r['description']}\n"
                f"Підтверджено: {r['amount']} грн"
            )

        elif r["status"] == "pending":
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✏️ Змінити",
                    callback_data=f"edit:{r['id']}"
                ),
                InlineKeyboardButton(
                    "🗑 Видалити",
                    callback_data=f"deleteask:{r['id']}"
                )
            ]])

            await update.message.reply_text(
                f"⏳ {label}\n"
                f"{r['description']}\n"
                f"Очікує підтвердження.",
                reply_markup=kb
            )

        else:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✏️ Заповнити заново",
                    callback_data=f"edit:{r['id']}"
                ),
                InlineKeyboardButton(
                    "🗑 Видалити",
                    callback_data=f"deleteask:{r['id']}"
                )
            ]])

            await update.message.reply_text(
                f"❌ {label}\n"
                f"{r['description']}\n"
                f"Відхилено.",
                reply_markup=kb
            )


async def delete_ask_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    eid = int(q.data.split(":")[1])

    c = db()

    row = c.execute(
        "SELECT * FROM entries WHERE id=?",
        (eid,)
    ).fetchone()

    c.close()

    if not row:
        await q.edit_message_text(
            "Запис уже видалений."
        )
        return

    if row["tg_id"] != q.from_user.id:
        return

    if row["status"] == "approved":
        await q.answer(
            "Підтверджений запис видалити не можна.",
            show_alert=True
        )
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Так, видалити",
            callback_data=f"deleteyes:{eid}"
        ),
        InlineKeyboardButton(
            "❌ Ні",
            callback_data=f"deleteno:{eid}"
        )
    ]])

    await q.edit_message_reply_markup(
        reply_markup=kb
    )


async def delete_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    action, eid = q.data.split(":")
    eid = int(eid)

    if action == "deleteno":
        await q.edit_message_text(
            "Видалення скасовано."
        )
        return

    c = db()

    row = c.execute(
        "SELECT * FROM entries WHERE id=?",
        (eid,)
    ).fetchone()

    if not row:
        c.close()
        await q.edit_message_text(
            "Запис уже видалений."
        )
        return

    if row["tg_id"] != q.from_user.id:
        c.close()
        return

    if row["status"] == "approved":
        c.close()
        await q.answer(
            "Підтверджений запис видалити не можна.",
            show_alert=True
        )
        return

    if row["category"] == "study":
        c.execute("""
            DELETE FROM subjects
            WHERE tg_id=? AND month=?
        """, (row["tg_id"], row["month"]))

    c.execute(
        "DELETE FROM entries WHERE id=?",
        (eid,)
    )

    c.commit()
    c.close()

    await q.edit_message_text(
        "🗑 Запис видалено."
    )


async def edit_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    eid = int(q.data.split(":")[1])

    c = db()

    row = c.execute(
        "SELECT * FROM entries WHERE id=?",
        (eid,)
    ).fetchone()

    if not row:
        c.close()
        await q.edit_message_text(
            "Запис уже видалений."
        )
        return

    if row["tg_id"] != q.from_user.id:
        c.close()
        return

    if row["status"] == "approved":
        c.close()
        await q.answer(
            "🔒 Андрій уже підтвердив цей запис.",
            show_alert=True
        )
        return

    if row["category"] == "study":
        c.execute("""
            DELETE FROM subjects
            WHERE tg_id=? AND month=?
        """, (row["tg_id"], row["month"]))

        c.execute(
            "DELETE FROM entries WHERE id=?",
            (eid,)
        )

        c.commit()
        c.close()

        await q.edit_message_text(
            "✏️ Старі дані навчання видалено.\n\n"
            "Натисни «📝 Підсумки місяця» "
            "і введи правильні дані заново."
        )

    else:
        context.user_data["edit_eid"] = eid
        c.close()

        await q.edit_message_text(
            f"✏️ Старий текст:\n"
            f"{row['description']}\n\n"
            f"Надішли новий опис одним повідомленням."
        )


async def edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    eid = context.user_data.get("edit_eid")

    if not eid:
        return False

    text = update.message.text.strip()

    c = db()

    row = c.execute(
        "SELECT * FROM entries WHERE id=?",
        (eid,)
    ).fetchone()

    if not row:
        c.close()
        context.user_data.pop("edit_eid", None)

        await update.message.reply_text(
            "Запис уже не існує.",
            reply_markup=MENU_CHILD
        )

        return True

    if row["tg_id"] != update.effective_user.id:
        c.close()
        context.user_data.pop("edit_eid", None)
        return True

    if row["status"] == "approved":
        c.close()
        context.user_data.pop("edit_eid", None)

        await update.message.reply_text(
            "🔒 Андрій уже підтвердив цей запис.",
            reply_markup=MENU_CHILD
        )

        return True

    c.execute("""
        UPDATE entries
        SET description=?, status='pending', amount=0
        WHERE id=?
    """, (text, eid))

    c.commit()
    c.close()

    context.user_data.pop("edit_eid", None)

    await update.message.reply_text(
        "✅ Запис змінено.\n"
        "Оновлена версія очікує підтвердження Андрія.",
        reply_markup=MENU_CHILD
    )

    # Повідомляємо адміну про зміну
    aid = admin_id()
    u = get_user(update.effective_user.id)

    if aid:
        label = CATS[row["category"]][0]

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "0",
                    callback_data=f"amt:{eid}:0"
                ),
                InlineKeyboardButton(
                    "100",
                    callback_data=f"amt:{eid}:100"
                ),
                InlineKeyboardButton(
                    "300",
                    callback_data=f"amt:{eid}:300"
                ),
                InlineKeyboardButton(
                    "500",
                    callback_data=f"amt:{eid}:500"
                ),
                InlineKeyboardButton(
                    "700",
                    callback_data=f"amt:{eid}:700"
                )
            ],
            [
                InlineKeyboardButton(
                    "Інша сума",
                    callback_data=f"custom:{eid}"
                )
            ]
        ])

        await context.bot.send_message(
            aid,
            f"✏️ {u['name']} змінив запис:\n"
            f"{label}\n{text}",
            reply_markup=kb
        )

    return True


# =========================================================
# SUMMARY
# =========================================================

async def my_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)

    if not u or u["role"] != "child":
        return

    m = month_key()
    s, o, b, t = summary(u["tg_id"], m)

    await update.message.reply_text(
        f"📊 {u['name']} — {m}\n\n"
        f"База: {BASE} грн\n"
        f"Навчання: {s} грн\n"
        f"Інші підтверджені бонуси: {o} грн\n"
        f"Бонуси разом (до {BONUS_CAP}): {b} грн\n\n"
        f"💰 Разом: {t} грн\n\n"
        f"70% особисті: {round(t * .7)} грн\n"
        f"20% накопичення: {round(t * .2)} грн\n"
        f"10% добрі справи: {round(t * .1)} грн"
    )


# =========================================================
# ADMIN REPORTS
# =========================================================

async def admin_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)

    if not u or u["role"] != "admin":
        await update.message.reply_text(
            "⚠️ Адміністратор не авторизований.\n"
            "Введи /admin СЕКРЕТ один раз."
        )
        return

    c = db()

    kids = c.execute("""
        SELECT * FROM users
        WHERE role='child'
        ORDER BY name
    """).fetchall()

    c.close()

    m = month_key()

    if not kids:
        await update.message.reply_text(
            "Хлопці ще не підключені."
        )
        return

    parts = []

    for k in kids:
        s, o, b, t = summary(k["tg_id"], m)

        c = db()

        subj = c.execute("""
            SELECT subject,avg
            FROM subjects
            WHERE tg_id=? AND month=?
            ORDER BY subject
        """, (k["tg_id"], m)).fetchall()

        ents = c.execute("""
            SELECT category,description,amount,status
            FROM entries
            WHERE tg_id=? AND month=?
            ORDER BY id
        """, (k["tg_id"], m)).fetchall()

        c.close()

        lines = [
            f"👤 {k['name']}",
            f"База: {BASE} грн",
            f"📚 Навчання підтверджено: +{s} грн"
        ]

        if subj:
            lines.extend([
                f"   • {r['subject']}: {r['avg']:g}"
                for r in subj
            ])

        for r in ents:
            if r["category"] == "study":
                if r["status"] == "pending":
                    lines.append(
                        f"⏳ Навчання очікує: "
                        f"{r['amount']} грн"
                    )
                continue

            icon = (
                "🔒" if r["status"] == "approved"
                else "⏳" if r["status"] == "pending"
                else "❌"
            )

            label = CATS.get(
                r["category"],
                (r["category"], 0)
            )[0]

            amt = (
                f"+{r['amount']} грн"
                if r["status"] == "approved"
                else "не враховано"
            )

            lines.append(
                f"{icon} {label}: "
                f"{r['description']} — {amt}"
            )

        lines += [
            f"Інші підтверджені: +{o} грн",
            f"Бонус разом: {b} грн",
            f"💰 РАЗОМ: {t} грн"
        ]

        parts.append("\n".join(lines))

    await update.message.reply_text(
        "📊 " + m + "\n\n" +
        "\n\n".join(parts)
    )


# =========================================================
# ADMIN RESET MONTH
# =========================================================

async def reset_month_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)

    if not u or u["role"] != "admin":
        return

    c = db()

    kids = c.execute("""
        SELECT * FROM users
        WHERE role='child'
        ORDER BY name
    """).fetchall()

    c.close()

    if not kids:
        await update.message.reply_text(
            "Хлопці ще не підключені."
        )
        return

    buttons = []

    for k in kids:
        buttons.append([
            InlineKeyboardButton(
                f"🗑 Скинути {k['name']}",
                callback_data=f"resetask:{k['tg_id']}"
            )
        ])

    await update.message.reply_text(
        f"🗑 Скидання місяця {month_key()}\n\n"
        f"Вибери, кому повністю видалити дані "
        f"за поточний місяць.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def reset_ask_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    u = get_user(q.from_user.id)

    if not u or u["role"] != "admin":
        return

    tg = int(q.data.split(":")[1])
    child = get_user(tg)

    if not child or child["role"] != "child":
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "⚠️ ТАК, ВИДАЛИТИ",
            callback_data=f"resetyes:{tg}"
        ),
        InlineKeyboardButton(
            "❌ Ні",
            callback_data="resetno:0"
        )
    ]])

    await q.edit_message_text(
        f"⚠️ Точно видалити ВСІ дані "
        f"{child['name']} за {month_key()}?\n\n"
        f"Буде видалено навчання, досягнення "
        f"та всі підтвердження.\n"
        f"Після цього він зможе заповнити місяць заново.",
        reply_markup=kb
    )


async def reset_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    u = get_user(q.from_user.id)

    if not u or u["role"] != "admin":
        return

    action, value = q.data.split(":")

    if action == "resetno":
        await q.edit_message_text(
            "Скидання скасовано."
        )
        return

    tg = int(value)
    child = get_user(tg)

    if not child:
        return

    m = month_key()

    c = db()

    c.execute(
        "DELETE FROM subjects WHERE tg_id=? AND month=?",
        (tg, m)
    )

    c.execute(
        "DELETE FROM entries WHERE tg_id=? AND month=?",
        (tg, m)
    )

    c.commit()
    c.close()

    await q.edit_message_text(
        f"🗑 {child['name']}: місяць {m} "
        f"повністю скинуто.\n"
        f"Тепер можна заповнювати заново."
    )

    try:
        await context.bot.send_message(
            tg,
            f"🔄 Андрій скинув твої дані за {m}.\n"
            f"Можеш заповнити місяць заново.",
            reply_markup=MENU_CHILD
        )
    except Exception:
        pass


# =========================================================
# PENDING
# =========================================================

async def pending_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)

    if not u or u["role"] != "admin":
        await update.message.reply_text(
            "⚠️ Адміністратор не авторизований."
        )
        return

    c = db()

    rows = c.execute("""
        SELECT e.*,u.name
        FROM entries e
        JOIN users u ON u.tg_id=e.tg_id
        WHERE e.status='pending'
        ORDER BY e.id
    """).fetchall()

    c.close()

    if not rows:
        await update.message.reply_text(
            "Немає записів на підтвердження."
        )
        return

    for r in rows:
        if r["category"] == "study":
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"✅ Підтвердити {r['amount']} грн",
                    callback_data=f"studyok:{r['id']}"
                ),
                InlineKeyboardButton(
                    "❌ Відхилити",
                    callback_data=f"studyno:{r['id']}"
                )
            ]])

            txt = (
                f"📚 {r['name']} — навчання\n"
                f"{r['description']}\n\n"
                f"Розрахунок: {r['amount']} грн\n"
                f"⏳ Ще НЕ входить у підсумок."
            )

        else:
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "0",
                        callback_data=f"amt:{r['id']}:0"
                    ),
                    InlineKeyboardButton(
                        "100",
                        callback_data=f"amt:{r['id']}:100"
                    ),
                    InlineKeyboardButton(
                        "300",
                        callback_data=f"amt:{r['id']}:300"
                    ),
                    InlineKeyboardButton(
                        "500",
                        callback_data=f"amt:{r['id']}:500"
                    ),
                    InlineKeyboardButton(
                        "700",
                        callback_data=f"amt:{r['id']}:700"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "Інша сума",
                        callback_data=f"custom:{r['id']}"
                    )
                ]
            ])

            txt = (
                f"{r['name']}: "
                f"{CATS[r['category']][0]}\n"
                f"{r['description']}"
            )

        await update.message.reply_text(
            txt,
            reply_markup=kb
        )


# =========================================================
# RULES / ROUTER
# =========================================================

async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "База — 2 000 грн/міс.\n"
        "Бонусний фонд — до 4 000 грн.\n"
        "Максимум — 6 000 грн.\n\n"
        "📚 Навчання: середній бал рахується "
        "окремо по кожному предмету; бонус — до 1 500 грн.\n\n"
        "Інші напрямки: спорт, книги, допомога, "
        "саморозвиток.\n\n"
        "До підтвердження Андрієм запис можна "
        "змінити або видалити.\n"
        "Після підтвердження запис блокується 🔒.\n\n"
        "Гроші:\n"
        "70% особисті витрати\n"
        "20% накопичення\n"
        "10% добрі справи."
    )


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Якщо хлопець зараз редагує опис
    if context.user_data.get("edit_eid"):
        handled = await edit_text(update, context)
        if handled:
            return

    t = (update.message.text or "").strip()

    if t == "📊 Мій підсумок":
        return await my_summary(update, context)

    if t == "✏️ Мої записи":
        return await my_entries(update, context)

    if t in (
        "👥 Звіти Влада і Ромчика",
        "📊 Підсумок місяця"
    ):
        return await admin_reports(update, context)

    if t == "✅ На підтвердження":
        return await pending_reports(update, context)

    if t == "🗑 Скинути місяць":
        return await reset_month_start(update, context)

    if t == "❓ Правила":
        return await rules(update, context)


# =========================================================
# MAIN
# =========================================================

def main():
    db().close()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("join", join))
    app.add_handler(CommandHandler("sum", set_sum))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(
        CallbackQueryHandler(
            join_cb,
            pattern=r"^join(ok|no):"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            amount_cb,
            pattern=r"^amt:"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            custom_cb,
            pattern=r"^custom:"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            study_cb,
            pattern=r"^study(ok|no):"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            edit_cb,
            pattern=r"^edit:"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            delete_ask_cb,
            pattern=r"^deleteask:"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            delete_confirm_cb,
            pattern=r"^delete(yes|no):"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            reset_ask_cb,
            pattern=r"^resetask:"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            reset_confirm_cb,
            pattern=r"^reset(yes|no):"
        )
    )

    app.add_handler(
        ConversationHandler(
            entry_points=[
                MessageHandler(
                    filters.Regex(r"^📝 Підсумки місяця$"),
                    report_start
                )
            ],
            states={
                SUBJECT: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        subject
                    )
                ],
                AVG: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        avg
                    )
                ]
            },
            fallbacks=[
                CommandHandler("cancel", cancel)
            ]
        )
    )

    app.add_handler(
        ConversationHandler(
            entry_points=[
                MessageHandler(
                    filters.Regex(r"^➕ Додати досягнення$"),
                    add_start
                )
            ],
            states={
                CAT: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        cat
                    )
                ],
                DESC: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        desc
                    )
                ]
            },
            fallbacks=[
                CommandHandler("cancel", cancel)
            ]
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_router
        )
    )

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
