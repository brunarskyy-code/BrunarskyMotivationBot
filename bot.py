import os, sqlite3, sys
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

_railway_service = (os.environ.get("RAILWAY_SERVICE_NAME") or "").strip()
if _railway_service == "BrunarskyMotivationBot":
    print("Legacy Railway service BrunarskyMotivationBot is intentionally disabled; canonical service is exquisite-growth.")
    sys.exit(0)

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")
_admin_tg_id_raw = (os.environ.get("ADMIN_TG_ID", "0") or "0").strip()
try:
    ADMIN_TG_ID = int(_admin_tg_id_raw)
except ValueError:
    ADMIN_TG_ID = 0
    print("WARNING: ADMIN_TG_ID must be numeric; starting without auto-admin registration.")
DB = os.environ.get("DB_PATH", "motivation.db")
_db_dir = os.path.dirname(DB)
if _db_dir:
    try:
        os.makedirs(_db_dir, exist_ok=True)
    except OSError as e:
        print(f"WARNING: cannot create DB directory {_db_dir}: {e}. Falling back to local motivation.db")
        DB = "motivation.db"

BASE = 2000
BONUS_CAP = 4000
STUDY_CAP = 1500

CATS = {
    "sport": ("Спорт", 500),
    "books": ("Книги", 600),
    "help": ("Допомога батькам / сімейний проєкт", 1500),
    "development": ("Саморозвиток", 700),
}

# Ставки за навчання можна змінювати з адмін-меню.
# Пороги нижче — стартові значення; у БД вони зберігаються окремо.
DEFAULT_STUDY_RATES = {
    12: [
        (11.0, 1500),
        (10.5, 1200),
        (10.0, 1000),
        (9.5, 700),
        (8.0, 500),
        (7.0, 300),
        (6.0, 150),
        (0.0, 0),
    ],
    6: [
        (5.5, 1500),
        (5.25, 1200),
        (5.0, 1000),
        (4.75, 700),
        (4.0, 500),
        (3.5, 300),
        (3.0, 150),
        (0.0, 0),
    ],
}

MENU_CHILD = ReplyKeyboardMarkup([
    ["📝 Заповнити / змінити", "📋 Переглянути місяць"],
    ["📤 Відправити Андрію", "📊 Мій підсумок"],
    ["❓ Правила"]
], resize_keyboard=True)

MENU_ADMIN = ReplyKeyboardMarkup([
    ["👥 Звіти дітей", "✅ На підтвердження"],
    ["♻️ Керування місяцем", "📊 Підсумок місяця"],
    ["⚙️ Суми та ставки", "❓ Правила"]
], resize_keyboard=True)

MENU_EDIT = ReplyKeyboardMarkup([
    ["📚 Навчання", "➕ Досягнення"],
    ["✏️ Змінити досягнення", "🗑 Видалити досягнення"],
    ["🧹 Очистити мою чернетку", "⬅️ Назад"]
], resize_keyboard=True)

def db():
    global DB
    try:
        c = sqlite3.connect(DB)
    except sqlite3.OperationalError as e:
        if DB != "motivation.db":
            print(f"WARNING: cannot open DB at {DB}: {e}. Falling back to local motivation.db")
            DB = "motivation.db"
            c = sqlite3.connect(DB)
        else:
            raise
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        tg_id INTEGER PRIMARY KEY, role TEXT, name TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS subjects(
        id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, month TEXT,
        subject TEXT, avg REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, month TEXT,
        category TEXT, description TEXT, amount INTEGER DEFAULT 0,
        status TEXT DEFAULT 'draft')""")
    c.execute("""CREATE TABLE IF NOT EXISTS month_state(
        tg_id INTEGER, month TEXT, status TEXT DEFAULT 'draft',
        PRIMARY KEY(tg_id, month))""")
    c.execute("""CREATE TABLE IF NOT EXISTS month_archives(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER NOT NULL,
        month TEXT NOT NULL,
        snapshot TEXT NOT NULL,
        archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS study_rates(
        scale INTEGER NOT NULL,
        min_avg REAL NOT NULL,
        amount INTEGER NOT NULL,
        PRIMARY KEY(scale,min_avg))""")
    c.execute("""CREATE TABLE IF NOT EXISTS app_settings(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL)""")
    for _key,_value in {
        "study_cap": STUDY_CAP,
        "base_amount": BASE,
        "total_cap": BASE + BONUS_CAP,
        "cat_sport_cap": CATS["sport"][1],
        "cat_books_cap": CATS["books"][1],
        "cat_help_cap": CATS["help"][1],
        "cat_development_cap": CATS["development"][1],
    }.items():
        c.execute(
            "INSERT OR IGNORE INTO app_settings(key,value) VALUES(?,?)",
            (_key,str(_value))
        )
    for _scale, _rates in DEFAULT_STUDY_RATES.items():
        for _min_avg, _amount in _rates:
            c.execute(
                "INSERT OR IGNORE INTO study_rates(scale,min_avg,amount) VALUES(?,?,?)",
                (_scale,_min_avg,_amount)
            )
    # Migration from older bot versions.
    cols = [r["name"] for r in c.execute("PRAGMA table_info(entries)").fetchall()]
    if "status" not in cols:
        c.execute("ALTER TABLE entries ADD COLUMN status TEXT DEFAULT 'draft'")
    c.commit()
    return c

def month_key():
    return datetime.now().strftime("%Y-%m")

def register(tg_id, role, name):
    c=db()
    c.execute("INSERT OR REPLACE INTO users(tg_id,role,name) VALUES(?,?,?)",(tg_id,role,name))
    c.commit(); c.close()

def get_user(tg_id):
    c=db()
    r=c.execute("SELECT * FROM users WHERE tg_id=?",(tg_id,)).fetchone()
    c.close()
    if not r and ADMIN_TG_ID and tg_id == ADMIN_TG_ID:
        register(tg_id,"admin","Андрій")
        c=db(); r=c.execute("SELECT * FROM users WHERE tg_id=?",(tg_id,)).fetchone(); c.close()
    return r

def admin_id():
    if ADMIN_TG_ID: return ADMIN_TG_ID
    c=db(); r=c.execute("SELECT tg_id FROM users WHERE role='admin' LIMIT 1").fetchone(); c.close()
    return r["tg_id"] if r else None

def ensure_month(tg_id, month=None):
    month = month or month_key()
    c=db()
    c.execute("INSERT OR IGNORE INTO month_state(tg_id,month,status) VALUES(?,?,'draft')",(tg_id,month))
    c.commit(); c.close()
    return month

def month_status(tg_id, month=None):
    month=ensure_month(tg_id,month)
    c=db(); r=c.execute("SELECT status FROM month_state WHERE tg_id=? AND month=?",(tg_id,month)).fetchone(); c.close()
    return r["status"] if r else "draft"

def set_month_status(tg_id, month, status):
    c=db()
    c.execute("""INSERT INTO month_state(tg_id,month,status) VALUES(?,?,?)
                 ON CONFLICT(tg_id,month) DO UPDATE SET status=excluded.status""",(tg_id,month,status))
    c.commit(); c.close()

def editable(tg_id, month=None):
    return month_status(tg_id,month) == "draft"

def grade_max_for_child(tg_id):
    u=get_user(tg_id)
    if u and u["name"]=="Ромчик":
        return 6.0
    return 12.0

def grade_system_label(tg_id):
    m=grade_max_for_child(tg_id)
    return "6-бальна" if m==6 else "12-бальна"

def get_setting_int(key, default):
    c=db()
    r=c.execute("SELECT value FROM app_settings WHERE key=?",(key,)).fetchone()
    c.close()
    try:
        return max(0,int(r["value"])) if r else int(default)
    except (ValueError,TypeError):
        return int(default)

def set_setting_int(key, amount):
    amount=max(0,int(amount))
    c=db()
    c.execute(
        """INSERT INTO app_settings(key,value) VALUES(?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (key,str(amount))
    )
    c.commit(); c.close()
    return amount

def get_base_amount():
    return get_setting_int("base_amount", BASE)

def get_total_cap():
    return get_setting_int("total_cap", BASE + BONUS_CAP)

def get_bonus_cap():
    return max(0, get_total_cap() - get_base_amount())

def get_study_cap():
    return get_setting_int("study_cap", STUDY_CAP)

def set_study_cap(amount):
    return set_setting_int("study_cap", amount)

def get_category_cap(category):
    default=CATS.get(category,("",0))[1]
    return get_setting_int(f"cat_{category}_cap", default)


def get_study_rates(scale):
    c=db()
    rows=c.execute(
        "SELECT min_avg,amount FROM study_rates WHERE scale=? ORDER BY min_avg DESC",
        (int(scale),)
    ).fetchall()
    c.close()
    return [(float(r["min_avg"]), int(r["amount"])) for r in rows]

def study_rates_text(scale=None):
    scales=[int(scale)] if scale else [12,6]
    blocks=[]
    for sc in scales:
        rows=get_study_rates(sc)
        lines=[f"🎓 {sc}-бальна система:"]
        for threshold,amount in rows:
            if threshold<=0:
                lines.append(f"• нижче мінімального порога → {amount} грн")
            else:
                lines.append(f"• від {threshold:g} → {amount} грн")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)

def study_level(tg_id, avg):
    scale=int(grade_max_for_child(tg_id))
    for threshold,amount in get_study_rates(scale):
        if avg >= threshold:
            return min(get_study_cap(),max(0,int(amount)))
    return 0

def calc_study(tg_id, month):
    c=db(); rows=c.execute("SELECT avg FROM subjects WHERE tg_id=? AND month=?",(tg_id,month)).fetchall(); c.close()
    if not rows: return 0
    return min(get_study_cap(), round(sum(study_level(tg_id,float(r["avg"])) for r in rows)/len(rows)))

def approved_bonus(tg_id, month):
    c=db()
    r=c.execute("""SELECT COALESCE(SUM(amount),0) s FROM entries
                   WHERE tg_id=? AND month=? AND category!='study' AND status='approved'""",(tg_id,month)).fetchone()
    c.close(); return int(r["s"])

def summary(tg_id, month):
    st = month_status(tg_id,month)
    study = calc_study(tg_id,month) if st=="approved" else 0
    other = approved_bonus(tg_id,month)
    base=get_base_amount()
    bonus_cap=get_bonus_cap()
    bonus=min(bonus_cap,study+other)
    return study,other,bonus,min(get_total_cap(),base+bonus)

def status_label(s):
    return {"draft":"📝 Чернетка","submitted":"⏳ Надіслано Андрію","approved":"🔒 Підтверджено"}.get(s,s)

def month_text(tg_id, month):
    u=get_user(tg_id)
    c=db()
    subs=c.execute("SELECT subject,avg FROM subjects WHERE tg_id=? AND month=? ORDER BY id",(tg_id,month)).fetchall()
    ents=c.execute("""SELECT id,category,description,amount,status FROM entries
                      WHERE tg_id=? AND month=? AND category!='study' ORDER BY id""",(tg_id,month)).fetchall()
    c.close()
    st=month_status(tg_id,month)
    gmax=grade_max_for_child(tg_id)
    gmax_text=str(int(gmax))
    lines=[
        f"👤 {u['name'] if u else tg_id} — {month}",
        f"🎓 Система оцінювання: {grade_system_label(tg_id)}",
        status_label(st),
        ""
    ]
    lines.append("📚 Навчання:")
    if subs:
        lines.extend([f"• {r['subject']}: {r['avg']:g} / {gmax_text}" for r in subs])
        lines.append(f"💰 Бонус за навчання: {calc_study(tg_id,month)} грн")
    else:
        lines.append("— не заповнено")
    lines.append("")
    lines.append("🏆 Досягнення:")
    if ents:
        for r in ents:
            label=CATS.get(r["category"],(r["category"],0))[0]
            lines.append(f"#{r['id']} {label}: {r['description']} — {r['amount']} грн")
    else:
        lines.append("— немає")
    return "\n".join(lines)

async def require_child_editable(update):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="child": return False
    st=month_status(u["tg_id"],month_key())
    if st=="submitted":
        await update.message.reply_text("⏳ Цей місяць уже надіслано Андрію. Змінювати його можна лише якщо Андрій поверне на виправлення.",reply_markup=MENU_CHILD)
        return False
    if st=="approved":
        await update.message.reply_text("🔒 Цей місяць уже підтверджений Андрієм і заблокований.",reply_markup=MENU_CHILD)
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if u:
        if u["role"]=="child": ensure_month(u["tg_id"])
        await update.message.reply_text(f"Привіт, {u['name']}!",reply_markup=MENU_ADMIN if u["role"]=="admin" else MENU_CHILD)
        return
    await update.message.reply_text(
        "Привіт! Для реєстрації:\n• Андрій: /admin СЕКРЕТ\n• Влад: /join Влад\n• Ромчик: /join Ромчик\n• Тест: /join Тест"
    )

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_SECRET:
        await update.message.reply_text("На сервері не задано ADMIN_SECRET."); return
    if not context.args or context.args[0]!=ADMIN_SECRET:
        await update.message.reply_text("Невірний секрет."); return
    register(update.effective_user.id,"admin","Андрій")
    await update.message.reply_text("Андрій зареєстрований як адміністратор.",reply_markup=MENU_ADMIN)

async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=(update.message.text or "").strip()
    parts=raw.split(maxsplit=1)
    arg=(context.args[0] if context.args else (parts[1] if len(parts)>1 else "")).strip()
    aliases={
        "влад":"Влад",
        "ромчик":"Ромчик",
        "тест":"Тест",
        "test":"Тест",
    }
    name=aliases.get(arg.casefold())
    print(f"JOIN_REQUEST tg_id={update.effective_user.id} arg={arg!r} resolved={name!r}")
    if not name:
        await update.message.reply_text("Напиши /join Влад, /join Ромчик або /join Тест"); return
    aid=admin_id()
    if not aid:
        await update.message.reply_text("Спочатку Андрій має зареєструватися."); return
    kb=InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ Підтвердити {name}",callback_data=f"joinok:{update.effective_user.id}:{name}"),
        InlineKeyboardButton("❌ Відхилити",callback_data=f"joinno:{update.effective_user.id}")
    ]])
    await context.bot.send_message(aid,f"Запит на доступ: {name}\nTelegram ID: {update.effective_user.id}",reply_markup=kb)
    await update.message.reply_text("Запит надіслано Андрію.")

async def join_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    u=get_user(q.from_user.id)
    if not u or u["role"]!="admin": return
    p=q.data.split(":"); tg=int(p[1])
    if p[0]=="joinok":
        name=p[2]; register(tg,"child",name); ensure_month(tg)
        await q.edit_message_text(f"✅ {name} підключений.")
        await context.bot.send_message(tg,f"Доступ підтверджено. Привіт, {name}!",reply_markup=MENU_CHILD)
    else:
        await q.edit_message_text("❌ Запит відхилено.")
        await context.bot.send_message(tg,"Запит на доступ відхилено.")

# ---------- Study draft ----------
SUBJECT, AVG = range(2)

async def escape_child_wizard_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allow menu buttons to escape a half-finished wizard instead of being read as data."""
    t=(update.message.text or "").strip()
    main_nav={
        "📝 Заповнити / змінити",
        "📋 Переглянути місяць",
        "📤 Відправити Андрію",
        "📊 Мій підсумок",
        "❓ Правила",
        "⬅️ Назад",
    }
    if t not in main_nav:
        return False

    for key in ("subjects","current_subject","cat"):
        context.user_data.pop(key,None)

    if t=="📝 Заповнити / змінити":
        if not await require_child_editable(update):
            return True
        await update.message.reply_text("Що хочеш заповнити або змінити?",reply_markup=MENU_EDIT)
        return True
    if t=="⬅️ Назад":
        await update.message.reply_text("Головне меню.",reply_markup=MENU_CHILD)
        return True
    if t=="📋 Переглянути місяць":
        await view_month(update,context); return True
    if t=="📤 Відправити Андрію":
        await submit_month(update,context); return True
    if t=="📊 Мій підсумок":
        await my_summary(update,context); return True
    if t=="❓ Правила":
        await rules(update,context); return True
    return False

async def report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_child_editable(update): return ConversationHandler.END
    context.user_data["subjects"]=[]
    tg=update.effective_user.id
    gmax=int(grade_max_for_child(tg))
    await update.message.reply_text(
        f"📚 Твоя система оцінювання — {gmax}-бальна.\n"
        "Введи назву першого предмета.\n"
        "Потім бот попросить середній бал за місяць.\n"
        "Коли закінчиш — напиши ГОТОВО.\n"
        "Для виходу без збереження — /cancel"
    )
    return SUBJECT

async def subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await escape_child_wizard_to_menu(update,context):
        return ConversationHandler.END
    t=(update.message.text or "").strip()
    if t.upper()=="ГОТОВО":
        items=context.user_data.get("subjects",[])
        if not items:
            await update.message.reply_text("Поки немає предметів."); return SUBJECT
        m=month_key(); tg=update.effective_user.id
        if not editable(tg,m):
            await update.message.reply_text("Місяць уже заблокований.",reply_markup=MENU_CHILD)
            return ConversationHandler.END
        c=db()
        c.execute("DELETE FROM subjects WHERE tg_id=? AND month=?",(tg,m))
        c.executemany("INSERT INTO subjects(tg_id,month,subject,avg) VALUES(?,?,?,?)",
                      [(tg,m,s,a) for s,a in items])
        c.commit(); c.close()
        await update.message.reply_text(
            f"✅ Навчання збережено в ЧЕРНЕТКУ.\nРозрахунок: {calc_study(tg,m)} грн.\n"
            "Можеш ще змінювати дані. Андрію нічого не надіслано.",
            reply_markup=MENU_EDIT)
        return ConversationHandler.END
    if t in ("Скасувати","⬅️ Назад"):
        return await cancel(update,context)
    context.user_data["current_subject"]=t
    gmax=int(grade_max_for_child(update.effective_user.id))
    example="5,5" if gmax==6 else "10,7"
    await update.message.reply_text(f"Середній бал з «{t}» за місяць (0–{gmax}). Наприклад: {example}")
    return AVG

async def avg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await escape_child_wizard_to_menu(update,context):
        return ConversationHandler.END
    gmax=grade_max_for_child(update.effective_user.id)
    try: a=float((update.message.text or "").replace(",","."))
    except:
        example="5,5" if gmax==6 else "10,7"
        await update.message.reply_text(f"Введи число, наприклад {example}."); return AVG
    if not 0<=a<=gmax:
        await update.message.reply_text(f"Бал має бути від 0 до {int(gmax)}."); return AVG
    context.user_data["subjects"].append((context.user_data["current_subject"],a))
    await update.message.reply_text("Збережено. Наступний предмет або ГОТОВО:")
    return SUBJECT

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("subjects",None)
    context.user_data.pop("current_subject",None)
    await update.message.reply_text("Скасовано. Нічого не відправлено Андрію.",reply_markup=MENU_CHILD)
    return ConversationHandler.END

# ---------- Achievement draft ----------
CAT, DESC = range(2,4)

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_child_editable(update): return ConversationHandler.END
    kb=ReplyKeyboardMarkup([["Спорт","Книги"],["Допомога","Саморозвиток"],["Скасувати"]],resize_keyboard=True,one_time_keyboard=True)
    await update.message.reply_text("Що додаємо?",reply_markup=kb)
    return CAT

async def cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await escape_child_wizard_to_menu(update,context):
        return ConversationHandler.END
    mp={"Спорт":"sport","Книги":"books","Допомога":"help","Саморозвиток":"development"}
    t=(update.message.text or "").strip()
    if t=="Скасувати": return await cancel(update,context)
    if t not in mp:
        await update.message.reply_text("Вибери категорію кнопкою."); return CAT
    context.user_data["cat"]=mp[t]
    await update.message.reply_text("Коротко опиши результат. Для виходу — /cancel")
    return DESC

async def desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await escape_child_wizard_to_menu(update,context):
        return ConversationHandler.END
    tg=update.effective_user.id; m=month_key()
    if not editable(tg,m):
        await update.message.reply_text("Місяць уже заблокований.",reply_markup=MENU_CHILD)
        return ConversationHandler.END
    d=(update.message.text or "").strip()
    if not d:
        await update.message.reply_text("Опис не може бути порожнім."); return DESC
    cat=context.user_data["cat"]
    c=db()
    c.execute("""INSERT INTO entries(tg_id,month,category,description,amount,status)
                 VALUES(?,?,?,?,0,'draft')""",(tg,m,cat,d))
    c.commit(); c.close()
    await update.message.reply_text("✅ Додано в ЧЕРНЕТКУ. Можеш змінити або видалити до відправлення Андрію.",reply_markup=MENU_EDIT)
    return ConversationHandler.END

# ---------- Child edit/delete ----------
async def edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_child_editable(update): return
    tg=update.effective_user.id; m=month_key()
    c=db(); rows=c.execute("""SELECT id,category,description FROM entries
                             WHERE tg_id=? AND month=? AND category!='study' ORDER BY id""",(tg,m)).fetchall(); c.close()
    if not rows:
        await update.message.reply_text("Немає досягнень для зміни.",reply_markup=MENU_EDIT); return
    kb=InlineKeyboardMarkup([[InlineKeyboardButton(
        f"#{r['id']} {CATS.get(r['category'],(r['category'],0))[0]}: {r['description'][:30]}",
        callback_data=f"edit:{r['id']}")] for r in rows])
    await update.message.reply_text("Вибери запис, який хочеш змінити:",reply_markup=kb)

async def edit_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    eid=int(q.data.split(":")[1]); tg=q.from_user.id; m=month_key()
    if not editable(tg,m):
        await q.message.reply_text("Місяць уже заблокований."); return
    c=db(); r=c.execute("SELECT * FROM entries WHERE id=? AND tg_id=? AND month=?",(eid,tg,m)).fetchone(); c.close()
    if not r: await q.message.reply_text("Запис не знайдено."); return
    context.user_data["edit_eid"]=eid
    await q.message.reply_text(f"Поточний текст:\n{r['description']}\n\nНадішли новий текст одним повідомленням.")

async def save_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    eid=context.user_data.get("edit_eid")
    if not eid: return False
    tg=update.effective_user.id; m=month_key()
    if not editable(tg,m):
        context.user_data.pop("edit_eid",None)
        await update.message.reply_text("Місяць уже заблокований.",reply_markup=MENU_CHILD); return True
    text=(update.message.text or "").strip()
    if text in ("⬅️ Назад","Скасувати"):
        context.user_data.pop("edit_eid",None)
        await update.message.reply_text("Зміну скасовано.",reply_markup=MENU_EDIT); return True
    c=db(); c.execute("UPDATE entries SET description=? WHERE id=? AND tg_id=? AND month=?",(text,eid,tg,m)); c.commit(); c.close()
    context.user_data.pop("edit_eid",None)
    await update.message.reply_text("✅ Запис змінено.",reply_markup=MENU_EDIT)
    return True

async def delete_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_child_editable(update): return
    tg=update.effective_user.id; m=month_key()
    c=db(); rows=c.execute("""SELECT id,category,description FROM entries
                             WHERE tg_id=? AND month=? AND category!='study' ORDER BY id""",(tg,m)).fetchall(); c.close()
    if not rows:
        await update.message.reply_text("Немає досягнень для видалення.",reply_markup=MENU_EDIT); return
    kb=InlineKeyboardMarkup([[InlineKeyboardButton(
        f"🗑 #{r['id']} {CATS.get(r['category'],(r['category'],0))[0]}: {r['description'][:28]}",
        callback_data=f"delask:{r['id']}")] for r in rows])
    await update.message.reply_text("Що видалити?",reply_markup=kb)

async def delask_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    eid=int(q.data.split(":")[1]); tg=q.from_user.id
    c=db(); r=c.execute("SELECT * FROM entries WHERE id=? AND tg_id=?",(eid,tg)).fetchone(); c.close()
    if not r or not editable(tg,r["month"]): await q.message.reply_text("Цей запис уже не можна видалити."); return
    kb=InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑 Так, видалити",callback_data=f"delok:{eid}"),
        InlineKeyboardButton("Ні",callback_data=f"delno:{eid}")
    ]])
    await q.edit_message_text(f"Видалити?\n{r['description']}",reply_markup=kb)

async def del_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    action,eid=q.data.split(":"); eid=int(eid); tg=q.from_user.id
    if action=="delno":
        await q.edit_message_text("Скасовано."); return
    c=db(); r=c.execute("SELECT * FROM entries WHERE id=? AND tg_id=?",(eid,tg)).fetchone()
    if r and editable(tg,r["month"]):
        c.execute("DELETE FROM entries WHERE id=? AND tg_id=?",(eid,tg)); c.commit()
        c.close(); await q.edit_message_text("🗑 Видалено.")
    else:
        c.close(); await q.edit_message_text("Запис уже заблокований.")

async def clear_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_child_editable(update): return
    kb=InlineKeyboardMarkup([[
        InlineKeyboardButton("🧹 Так, очистити",callback_data="clearok"),
        InlineKeyboardButton("Ні",callback_data="clearno")
    ]])
    await update.message.reply_text("Очистити ВСІ твої дані за поточний місяць і почати заново?",reply_markup=kb)

async def clear_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    if q.data=="clearno":
        await q.edit_message_text("Скасовано."); return
    tg=q.from_user.id; m=month_key()
    if not editable(tg,m):
        await q.edit_message_text("Місяць уже заблокований."); return
    c=db()
    c.execute("DELETE FROM subjects WHERE tg_id=? AND month=?",(tg,m))
    c.execute("DELETE FROM entries WHERE tg_id=? AND month=?",(tg,m))
    c.commit(); c.close()
    await q.edit_message_text("🧹 Чернетку очищено. Можеш заповнювати заново.")

# ---------- Admin amounts for achievements ----------
def achievement_keyboard(eid):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("0",callback_data=f"amt:{eid}:0"),
            InlineKeyboardButton("100",callback_data=f"amt:{eid}:100"),
            InlineKeyboardButton("300",callback_data=f"amt:{eid}:300"),
            InlineKeyboardButton("500",callback_data=f"amt:{eid}:500"),
            InlineKeyboardButton("700",callback_data=f"amt:{eid}:700"),
        ],
        [InlineKeyboardButton("Інша сума",callback_data=f"custom:{eid}")]
    ])

async def amount_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    u=get_user(q.from_user.id)
    if not u or u["role"]!="admin": return
    _,eid,amt=q.data.split(":"); eid=int(eid); amt=int(amt)
    c=db(); row=c.execute("SELECT * FROM entries WHERE id=?",(eid,)).fetchone()
    if not row:
        c.close(); await q.answer("Запис не знайдено.",show_alert=True); return
    if row["category"] not in CATS:
        c.close(); return
    maxv=get_category_cap(row["category"])
    amt=max(0,min(amt,maxv))
    c.execute("UPDATE entries SET amount=? WHERE id=?",(amt,eid)); c.commit(); c.close()
    await q.edit_message_text(q.message.text+f"\n\n💰 Сума: {amt} грн")

async def custom_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    u=get_user(q.from_user.id)
    if not u or u["role"]!="admin": return
    context.user_data["custom_eid"]=int(q.data.split(":")[1])
    await q.message.reply_text("Введи суму командою /sum число. Наприклад: /sum 450")

async def set_sum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="admin": return
    eid=context.user_data.get("custom_eid")
    if not eid or not context.args:
        await update.message.reply_text("Спочатку натисни «Інша сума»."); return
    try: amt=int(context.args[0])
    except ValueError:
        await update.message.reply_text("Приклад: /sum 450"); return
    c=db(); row=c.execute("SELECT * FROM entries WHERE id=?",(eid,)).fetchone()
    if not row or row["category"] not in CATS:
        c.close(); await update.message.reply_text("Запис не знайдено."); return
    maxv=CATS[row["category"]][1]
    amt=max(0,min(amt,maxv))
    c.execute("UPDATE entries SET amount=? WHERE id=?",(amt,eid)); c.commit(); c.close()
    context.user_data.pop("custom_eid",None)
    await update.message.reply_text(f"✅ Для запису #{eid} встановлено {amt} грн.")

async def send_achievement_amount_controls(context, tg, month, aid):
    c=db(); rows=c.execute("""SELECT id,category,description,amount FROM entries
                              WHERE tg_id=? AND month=? AND category!='study' ORDER BY id""",(tg,month)).fetchall(); c.close()
    child=get_user(tg)
    for r in rows:
        label=CATS.get(r["category"],(r["category"],0))[0]
        maxv=get_category_cap(r["category"])
        await context.bot.send_message(
            aid,
            f"🏆 {child['name']}: {label}\n{r['description']}\nПоточна сума: {r['amount']} грн · максимум {maxv} грн",
            reply_markup=achievement_keyboard(r["id"])
        )

# ---------- Submit whole month ----------
async def submit_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="child": return
    tg=u["tg_id"]; m=month_key(); st=month_status(tg,m)
    if st=="approved":
        await update.message.reply_text("🔒 Місяць уже підтверджений."); return
    if st=="submitted":
        await update.message.reply_text("⏳ Місяць уже надіслано Андрію."); return
    c=db()
    nsub=c.execute("SELECT COUNT(*) n FROM subjects WHERE tg_id=? AND month=?",(tg,m)).fetchone()["n"]
    nent=c.execute("SELECT COUNT(*) n FROM entries WHERE tg_id=? AND month=? AND category!='study'",(tg,m)).fetchone()["n"]
    c.close()
    if nsub==0 and nent==0:
        await update.message.reply_text("Чернетка порожня. Спочатку щось заповни."); return
    kb=InlineKeyboardMarkup([[
        InlineKeyboardButton("📤 Так, відправити",callback_data="submitok"),
        InlineKeyboardButton("✏️ Ще змінити",callback_data="submitno")
    ]])
    await update.message.reply_text(month_text(tg,m)+"\n\nПісля відправлення змінювати не можна, доки Андрій не поверне на виправлення.",reply_markup=kb)

async def submit_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    tg=q.from_user.id; m=month_key()
    if q.data=="submitno":
        await q.edit_message_text("Залишено як чернетку. Можеш продовжити редагування."); return
    if not editable(tg,m):
        await q.edit_message_text("Місяць уже відправлений або підтверджений."); return
    set_month_status(tg,m,"submitted")
    c=db(); c.execute("UPDATE entries SET status='submitted' WHERE tg_id=? AND month=?",(tg,m)); c.commit(); c.close()
    u=get_user(tg); aid=admin_id()
    await q.edit_message_text("📤 Надіслано Андрію. Тепер місяць заблокований для змін.")
    if aid:
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Підтвердити місяць",callback_data=f"monthok:{tg}:{m}")],
            [InlineKeyboardButton("↩️ Повернути на виправлення",callback_data=f"monthback:{tg}:{m}")]
        ])
        await context.bot.send_message(aid,month_text(tg,m)+"\n\nПідтвердити весь місяць?",reply_markup=kb)
        await send_achievement_amount_controls(context,tg,m,aid)

# ---------- Admin approve/return ----------
async def month_admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    u=get_user(q.from_user.id)
    if not u or u["role"]!="admin": return
    action,tg,m=q.data.split(":",2); tg=int(tg)
    if month_status(tg,m)!="submitted":
        await q.edit_message_text(q.message.text+"\n\n⚠️ Статус уже змінився."); return
    if action=="monthback":
        set_month_status(tg,m,"draft")
        c=db(); c.execute("UPDATE entries SET status='draft',amount=0 WHERE tg_id=? AND month=?",(tg,m)); c.commit(); c.close()
        await q.edit_message_text(q.message.text+"\n\n↩️ Повернуто на виправлення.")
        await context.bot.send_message(tg,"↩️ Андрій повернув місяць на виправлення. Можеш змінювати дані й відправити ще раз.",reply_markup=MENU_CHILD)
        return
    # Approve: study is calculated automatically; achievements receive 0 initially
    # and can be assigned by admin before/after approval via achievement buttons.
    set_month_status(tg,m,"approved")
    c=db(); c.execute("""UPDATE entries SET status='approved'
                        WHERE tg_id=? AND month=? AND category!='study'""",(tg,m)); c.commit(); c.close()
    await q.edit_message_text(q.message.text+"\n\n✅ Місяць підтверджено та ЗАБЛОКОВАНО.")
    await context.bot.send_message(tg,"✅ Андрій підтвердив місяць. 🔒 Дані заблоковані й більше не редагуються.",reply_markup=MENU_CHILD)

async def pending_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="admin":
        await update.message.reply_text("Спочатку авторизуйся як адміністратор."); return
    c=db()
    rows=c.execute("""SELECT ms.tg_id,ms.month,u.name FROM month_state ms
                      JOIN users u ON u.tg_id=ms.tg_id
                      WHERE ms.status='submitted' ORDER BY ms.month, u.name""").fetchall()
    c.close()
    if not rows:
        await update.message.reply_text("Немає місяців на підтвердження."); return
    for r in rows:
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Підтвердити місяць",callback_data=f"monthok:{r['tg_id']}:{r['month']}")],
            [InlineKeyboardButton("↩️ Повернути на виправлення",callback_data=f"monthback:{r['tg_id']}:{r['month']}")]
        ])
        await update.message.reply_text(month_text(r["tg_id"],r["month"]),reply_markup=kb)
        await send_achievement_amount_controls(context,r["tg_id"],r["month"],update.effective_user.id)

# ---------- Admin reset ----------
async def reset_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="admin": return
    c=db(); kids=c.execute("SELECT tg_id,name FROM users WHERE role='child' ORDER BY name").fetchall(); c.close()
    if not kids:
        await update.message.reply_text("Хлопці ще не підключені."); return
    kb=InlineKeyboardMarkup([[InlineKeyboardButton(f"♻️ {k['name']} — {month_key()}",callback_data=f"resetask:{k['tg_id']}:{month_key()}")] for k in kids])
    await update.message.reply_text("Чий поточний місяць скинути? Попередня версія залишиться в архіві.",reply_markup=kb)

async def resetask_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    u=get_user(q.from_user.id)
    if not u or u["role"]!="admin": return
    _,tg,m=q.data.split(":",2); tg=int(tg); child=get_user(tg)
    kb=InlineKeyboardMarkup([[
        InlineKeyboardButton("⚠️ Так, скинути",callback_data=f"resetok:{tg}:{m}"),
        InlineKeyboardButton("Ні",callback_data=f"resetno:{tg}:{m}")
    ]])
    await q.edit_message_text(f"⚠️ Скинути активні дані {child['name']} за {m}?\nПопередня версія збережеться в архіві, після цього місяць можна заповнювати заново.",reply_markup=kb)

async def reset_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    u=get_user(q.from_user.id)
    if not u or u["role"]!="admin": return
    action,tg,m=q.data.split(":",2); tg=int(tg)
    if action=="resetno":
        await q.edit_message_text("Скасовано."); return
    child=get_user(tg)
    snapshot=month_text(tg,m)
    c=db()
    c.execute("INSERT INTO month_archives(tg_id,month,snapshot) VALUES(?,?,?)",(tg,m,snapshot))
    c.execute("DELETE FROM subjects WHERE tg_id=? AND month=?",(tg,m))
    c.execute("DELETE FROM entries WHERE tg_id=? AND month=?",(tg,m))
    c.execute("DELETE FROM month_state WHERE tg_id=? AND month=?",(tg,m))
    c.commit(); c.close()
    ensure_month(tg,m)
    await q.edit_message_text(f"♻️ {child['name']}: {m} скинуто. Попередню версію збережено в архіві.")
    await context.bot.send_message(tg,f"♻️ Андрій скинув {m}. Можеш заповнити місяць заново.",reply_markup=MENU_CHILD)

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="admin": return
    if not context.args:
        await update.message.reply_text("Приклад: /history 2026-09"); return
    m=context.args[0].strip()
    c=db(); rows=c.execute("""SELECT ma.snapshot,ma.archived_at,u.name FROM month_archives ma
                              LEFT JOIN users u ON u.tg_id=ma.tg_id
                              WHERE ma.month=? ORDER BY ma.id""",(m,)).fetchall(); c.close()
    if not rows:
        await update.message.reply_text(f"Архівів за {m} немає."); return
    for r in rows:
        await update.message.reply_text(f"📚 Архів {m} · {r['archived_at']}\n\n{r['snapshot']}")

# ---------- Views ----------
async def view_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="child": return
    await update.message.reply_text(month_text(u["tg_id"],month_key()),reply_markup=MENU_CHILD)

async def my_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="child": return
    m=month_key(); s,o,b,t=summary(u["tg_id"],m)
    st=month_status(u["tg_id"],m)
    await update.message.reply_text(
        f"📊 {u['name']} — {m}\n{status_label(st)}\n"
        f"База: {get_base_amount()} грн\nНавчання підтверджено: {s} грн\nІнші підтверджені: {o} грн\n"
        f"Бонуси разом (до {get_bonus_cap()}): {b} грн\n\n💰 Разом: {t} грн (макс. {get_total_cap()})\n"
        f"70% особисті: {round(t*.7)} грн\n20% накопичення: {round(t*.2)} грн\n10% добрі справи: {round(t*.1)} грн"
    )

MONEY_SETTING_LABELS = {
    "base_amount": "💵 База",
    "total_cap": "💰 Максимум разом",
    "study_cap": "🎓 Максимум за навчання",
    "cat_sport_cap": "🏃 Спорт",
    "cat_books_cap": "📚 Книги",
    "cat_help_cap": "🏠 Допомога",
    "cat_development_cap": "🚀 Саморозвиток",
}

def money_settings_text():
    base=get_base_amount()
    total=get_total_cap()
    bonus=get_bonus_cap()
    return (
        "⚙️ Суми та ліміти\n\n"
        f"💵 База: {base} грн\n"
        f"🎁 Доступно бонусів: до {bonus} грн\n"
        f"💰 Максимум разом: {total} грн\n"
        f"🎓 Максимум за навчання: {get_study_cap()} грн\n"
        f"🏃 Спорт: до {get_category_cap('sport')} грн\n"
        f"📚 Книги: до {get_category_cap('books')} грн\n"
        f"🏠 Допомога: до {get_category_cap('help')} грн\n"
        f"🚀 Саморозвиток: до {get_category_cap('development')} грн"
    )

async def money_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="admin":
        await update.message.reply_text("Спочатку авторизуйся як адміністратор."); return
    kb=InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💵 База",callback_data="moneyedit:base_amount"),
            InlineKeyboardButton("💰 Максимум разом",callback_data="moneyedit:total_cap"),
        ],
        [InlineKeyboardButton("🎓 Максимум навчання",callback_data="moneyedit:study_cap")],
        [
            InlineKeyboardButton("🏃 Спорт",callback_data="moneyedit:cat_sport_cap"),
            InlineKeyboardButton("📚 Книги",callback_data="moneyedit:cat_books_cap"),
        ],
        [
            InlineKeyboardButton("🏠 Допомога",callback_data="moneyedit:cat_help_cap"),
            InlineKeyboardButton("🚀 Саморозвиток",callback_data="moneyedit:cat_development_cap"),
        ],
        [InlineKeyboardButton("📈 Пороги оцінок і ставки",callback_data="study_rates_open")]
    ])
    await update.message.reply_text(money_settings_text(),reply_markup=kb)

async def moneyedit_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    u=get_user(q.from_user.id)
    if not u or u["role"]!="admin": return
    key=q.data.split(":",1)[1]
    if key not in MONEY_SETTING_LABELS: return
    defaults={
        "base_amount":BASE,
        "total_cap":BASE+BONUS_CAP,
        "study_cap":STUDY_CAP,
        "cat_sport_cap":CATS["sport"][1],
        "cat_books_cap":CATS["books"][1],
        "cat_help_cap":CATS["help"][1],
        "cat_development_cap":CATS["development"][1],
    }
    current=get_setting_int(key,defaults[key])
    context.user_data["money_setting_edit"]=key
    await q.message.reply_text(
        f"{MONEY_SETTING_LABELS[key]} зараз: {current} грн.\n"
        "Введи нову суму одним числом."
    )

async def save_money_setting_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key=context.user_data.get("money_setting_edit")
    if not key: return False
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="admin":
        context.user_data.pop("money_setting_edit",None); return False
    t=(update.message.text or "").strip().replace(" ","")
    try:
        amount=int(t)
    except ValueError:
        await update.message.reply_text("Введи лише суму числом.")
        return True
    if amount<0:
        await update.message.reply_text("Сума не може бути від'ємною.")
        return True

    if key=="total_cap" and amount<get_base_amount():
        await update.message.reply_text(
            f"Максимум разом не може бути меншим за базу ({get_base_amount()} грн)."
        )
        return True

    set_setting_int(key,amount)
    context.user_data.pop("money_setting_edit",None)
    warning=""
    if key=="study_cap" and amount>get_bonus_cap():
        warning=(
            f"\n\n⚠️ Зараз загальний бонусний ліміт — {get_bonus_cap()} грн, "
            "тому фактично понад нього в загальну суму не потрапить. "
            "За потреби збільш «💰 Максимум разом»."
        )
    await update.message.reply_text(
        f"✅ {MONEY_SETTING_LABELS[key]}: {amount} грн.\n\n"
        + money_settings_text() + warning,
        reply_markup=MENU_ADMIN
    )
    return True

async def study_rates_open_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    u=get_user(q.from_user.id)
    if not u or u["role"]!="admin": return
    cap=get_study_cap()
    kb=InlineKeyboardMarkup([[
        InlineKeyboardButton("✏️ 12-бальна",callback_data="ratescale:12"),
        InlineKeyboardButton("✏️ 6-бальна",callback_data="ratescale:6")
    ]])
    await q.message.reply_text(
        "📈 Пороги оцінок і ставки\n\n"+study_rates_text()+
        f"\n\nМаксимум за навчання: {cap} грн.",
        reply_markup=kb
    )

async def study_rates_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="admin":
        await update.message.reply_text("Спочатку авторизуйся як адміністратор."); return
    cap=get_study_cap()
    kb=InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ 12-бальна",callback_data="ratescale:12"),
            InlineKeyboardButton("✏️ 6-бальна",callback_data="ratescale:6")
        ],
        [InlineKeyboardButton(f"💰 Максимум за навчання: {cap} грн",callback_data="studycap")]
    ])
    await update.message.reply_text(
        "⚙️ Поточні ставки за навчання\n\n"+study_rates_text()+
        f"\n\nМаксимум за навчання: {cap} грн.",
        reply_markup=kb
    )

async def studycap_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    u=get_user(q.from_user.id)
    if not u or u["role"]!="admin": return
    context.user_data["study_cap_edit"]=True
    await q.message.reply_text(
        f"Зараз максимум за навчання: {get_study_cap()} грн.\n"
        "Введи нову максимальну суму одним числом, наприклад 2000."
    )

async def save_study_cap_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("study_cap_edit"): return False
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="admin":
        context.user_data.pop("study_cap_edit",None); return False
    t=(update.message.text or "").strip().replace(" ","")
    try:
        amount=int(t)
    except ValueError:
        await update.message.reply_text("Введи лише суму числом, наприклад 2000.")
        return True
    if amount<0:
        await update.message.reply_text("Сума не може бути від'ємною.")
        return True
    set_study_cap(amount)
    context.user_data.pop("study_cap_edit",None)
    await update.message.reply_text(
        f"✅ Максимум за навчання змінено на {amount} грн.\n"
        "Окремі ставки за порогами можеш змінити через «⚙️ Ставки навчання».",
        reply_markup=MENU_ADMIN
    )
    return True

async def ratescale_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    u=get_user(q.from_user.id)
    if not u or u["role"]!="admin": return
    scale=int(q.data.split(":")[1])
    rows=get_study_rates(scale)
    buttons=[]
    for threshold,amount in rows:
        if threshold<=0:
            label=f"Нижче порога → {amount} грн"
        else:
            label=f"≥ {threshold:g} → {amount} грн"
        buttons.append([InlineKeyboardButton(label,callback_data=f"rateedit:{scale}:{threshold:g}")])
    await q.message.reply_text(
        f"✏️ {scale}-бальна система\nНатисни ставку, яку хочеш змінити.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def rateedit_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    u=get_user(q.from_user.id)
    if not u or u["role"]!="admin": return
    _,scale,threshold=q.data.split(":",2)
    scale=int(scale); threshold=float(threshold)
    c=db()
    row=c.execute(
        "SELECT amount FROM study_rates WHERE scale=? AND min_avg=?",
        (scale,threshold)
    ).fetchone()
    c.close()
    if not row:
        await q.message.reply_text("Ставку не знайдено."); return
    context.user_data["study_rate_edit"]=(scale,threshold)
    await q.message.reply_text(
        f"Введи нову суму для {scale}-бальної системи, поріг {threshold:g}.\n"
        f"Зараз: {row['amount']} грн. Максимум: {get_study_cap()} грн.\n"
        "Просто надішли число, наприклад 450."
    )

async def save_study_rate_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    edit=context.user_data.get("study_rate_edit")
    if not edit: return False
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="admin":
        context.user_data.pop("study_rate_edit",None); return False
    t=(update.message.text or "").strip().replace(" ","")
    try:
        amount=int(t)
    except ValueError:
        await update.message.reply_text("Введи лише суму числом, наприклад 450.")
        return True
    amount=max(0,min(amount,get_study_cap()))
    scale,threshold=edit
    c=db()
    c.execute(
        "UPDATE study_rates SET amount=? WHERE scale=? AND min_avg=?",
        (amount,scale,threshold)
    )
    c.commit(); c.close()
    context.user_data.pop("study_rate_edit",None)
    await update.message.reply_text(
        f"✅ Ставку змінено: {scale}-бальна, від {threshold:g} → {amount} грн.\n\n"
        + study_rates_text(scale),
        reply_markup=MENU_ADMIN
    )
    return True

async def admin_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="admin":
        await update.message.reply_text("Спочатку авторизуйся як адміністратор."); return
    c=db(); kids=c.execute("SELECT * FROM users WHERE role='child' ORDER BY name").fetchall(); c.close()
    if not kids:
        await update.message.reply_text("Хлопці ще не підключені."); return
    m=month_key()
    await update.message.reply_text("\n\n".join(month_text(k["tg_id"],m) for k in kids))

async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"База — {get_base_amount()} грн/міс. Бонусний фонд — до {get_bonus_cap()} грн. Максимум — {get_total_cap()} грн.\n"
        "Навчання: Влад — 12-бальна система, Ромчик — 6-бальна. Кожен сам пише назву предмета й середню оцінку.\n"
        f"Бонус за навчання бот рахує автоматично, максимум {get_study_cap()} грн.\n\n"
        + study_rates_text() +
        "\n\nІнші напрямки: спорт, книги, допомога, саморозвиток.\n\n"
        "📝 Поки місяць у чернетці — можна змінювати й видаляти.\n"
        "📤 Після відправлення Андрію редагування блокується.\n"
        "↩️ Андрій може повернути на виправлення.\n"
        "🔒 Після підтвердження місяць заблокований.\n"
        "♻️ При скиданні місяця попередня версія зберігається в архіві.\n\n"
        "Гроші: 70% особисті витрати / 20% накопичення / 10% добрі справи."
    )

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("money_setting_edit"):
        if await save_money_setting_text(update,context): return

    if context.user_data.get("study_cap_edit"):
        if await save_study_cap_text(update,context): return

    if context.user_data.get("study_rate_edit"):
        if await save_study_rate_text(update,context): return

    # If child is currently editing one achievement, next text is the replacement.
    if context.user_data.get("edit_eid"):
        if await save_edit_text(update,context): return

    t=(update.message.text or "").strip()
    u=get_user(update.effective_user.id)

    if t=="❓ Правила": return await rules(update,context)
    if u and u["role"]=="child":
        if t=="📝 Заповнити / змінити":
            if not await require_child_editable(update): return
            await update.message.reply_text("Що хочеш заповнити або змінити?",reply_markup=MENU_EDIT); return
        if t=="📋 Переглянути місяць": return await view_month(update,context)
        if t=="📤 Відправити Андрію": return await submit_month(update,context)
        if t=="📊 Мій підсумок": return await my_summary(update,context)
        if t=="✏️ Змінити досягнення": return await edit_menu(update,context)
        if t=="🗑 Видалити досягнення": return await delete_menu(update,context)
        if t=="🧹 Очистити мою чернетку": return await clear_draft(update,context)
        if t=="⬅️ Назад":
            await update.message.reply_text("Головне меню.",reply_markup=MENU_CHILD); return

    if u and u["role"]=="admin":
        if t in ("👥 Звіти дітей","👥 Звіти Влада і Ромчика","📊 Підсумок місяця"): return await admin_reports(update,context)
        if t=="✅ На підтвердження": return await pending_admin(update,context)
        if t=="♻️ Керування місяцем": return await reset_menu(update,context)
        if t in ("⚙️ Суми та ставки","⚙️ Ставки навчання"): return await money_settings_menu(update,context)

async def error_handler(update, context):
    print("ERROR:",repr(context.error))

def main():
    db().close()
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("admin",admin))
    app.add_handler(CommandHandler("join",join))
    app.add_handler(CommandHandler("cancel",cancel))
    app.add_handler(CommandHandler("sum",set_sum))
    app.add_handler(CommandHandler("history",history_cmd))

    app.add_handler(CallbackQueryHandler(join_cb,pattern=r"^join(ok|no):"))
    app.add_handler(CallbackQueryHandler(amount_cb,pattern=r"^amt:"))
    app.add_handler(CallbackQueryHandler(custom_cb,pattern=r"^custom:"))
    app.add_handler(CallbackQueryHandler(edit_cb,pattern=r"^edit:"))
    app.add_handler(CallbackQueryHandler(delask_cb,pattern=r"^delask:"))
    app.add_handler(CallbackQueryHandler(del_cb,pattern=r"^del(ok|no):"))
    app.add_handler(CallbackQueryHandler(clear_cb,pattern=r"^clear(ok|no)$"))
    app.add_handler(CallbackQueryHandler(submit_cb,pattern=r"^submit(ok|no)$"))
    app.add_handler(CallbackQueryHandler(month_admin_cb,pattern=r"^month(ok|back):"))
    app.add_handler(CallbackQueryHandler(resetask_cb,pattern=r"^resetask:"))
    app.add_handler(CallbackQueryHandler(reset_cb,pattern=r"^reset(ok|no):"))
    app.add_handler(CallbackQueryHandler(moneyedit_cb,pattern=r"^moneyedit:"))
    app.add_handler(CallbackQueryHandler(study_rates_open_cb,pattern=r"^study_rates_open$"))
    app.add_handler(CallbackQueryHandler(studycap_cb,pattern=r"^studycap$"))
    app.add_handler(CallbackQueryHandler(ratescale_cb,pattern=r"^ratescale:"))
    app.add_handler(CallbackQueryHandler(rateedit_cb,pattern=r"^rateedit:"))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^📚 Навчання$"),report_start)],
        states={
            SUBJECT:[MessageHandler(filters.TEXT & ~filters.COMMAND,subject)],
            AVG:[MessageHandler(filters.TEXT & ~filters.COMMAND,avg)]
        },
        fallbacks=[CommandHandler("cancel",cancel)],
        allow_reentry=True
    ))
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^(➕ Досягнення|➕ Додати досягнення)$"),add_start)],
        states={
            CAT:[MessageHandler(filters.TEXT & ~filters.COMMAND,cat)],
            DESC:[MessageHandler(filters.TEXT & ~filters.COMMAND,desc)]
        },
        fallbacks=[CommandHandler("cancel",cancel)],
        allow_reentry=True
    ))

    app.add_handler(MessageHandler(filters.Regex(r"^/join(?:@\\w+)?(?:\\s+.*)?$"),join))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_router))
    app.add_error_handler(error_handler)
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()