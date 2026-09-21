import os, sqlite3, calendar
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")
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
    ["➕ Додати досягнення", "❓ Правила"]
], resize_keyboard=True)

MENU_ADMIN = ReplyKeyboardMarkup([
    ["👥 Звіти Влада і Ромчика", "✅ На підтвердження"],
    ["📊 Підсумок місяця", "❓ Правила"]
], resize_keyboard=True)

def db():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS users(
      tg_id INTEGER PRIMARY KEY, role TEXT, name TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS subjects(
      id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, month TEXT,
      subject TEXT, avg REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS entries(
      id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, month TEXT,
      category TEXT, description TEXT, amount INTEGER DEFAULT 0,
      status TEXT DEFAULT 'pending')""")
    c.commit()
    return c

def month_key():
    return datetime.now().strftime("%Y-%m")

def get_user(tg_id):
    c=db(); r=c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone(); c.close(); return r

def register(tg_id, role, name):
    c=db(); c.execute("INSERT OR REPLACE INTO users(tg_id,role,name) VALUES(?,?,?)",(tg_id,role,name)); c.commit(); c.close()

def admin_id():
    c=db(); r=c.execute("SELECT tg_id FROM users WHERE role='admin' LIMIT 1").fetchone(); c.close()
    return r["tg_id"] if r else None

def study_level(avg):
    if avg >= 11: return 1500
    if avg >= 10.5: return 1200
    if avg >= 10: return 1000
    if avg >= 9.5: return 700
    return 0

def study_bonus(tg_id, month):
    c=db()
    rows=c.execute("SELECT avg FROM subjects WHERE tg_id=? AND month=?",(tg_id,month)).fetchall()
    c.close()
    n=len(rows)
    if not n: return 0
    # Each subject contributes its level divided by number of reported subjects.
    return round(sum(study_level(float(r["avg"])) for r in rows)/n)

def approved_bonus(tg_id, month):
    c=db()
    r=c.execute("SELECT COALESCE(SUM(amount),0) s FROM entries WHERE tg_id=? AND month=? AND status='approved'",(tg_id,month)).fetchone()
    c.close()
    return int(r["s"])

def summary(tg_id, month):
    s=study_bonus(tg_id,month)
    other=approved_bonus(tg_id,month)
    bonus=min(BONUS_CAP, s+other)
    total=BASE+bonus
    return s, other, bonus, total

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if u:
        await update.message.reply_text(f"Привіт, {u['name']}!", reply_markup=MENU_ADMIN if u["role"]=="admin" else MENU_CHILD)
        return
    await update.message.reply_text(
        "Привіт! Для реєстрації:\n"
        "• Андрій: /admin СЕКРЕТ\n"
        "• Влад: /join Влад\n"
        "• Ромчик: /join Ромчик\n\n"
        "Після заявки Андрій підтвердить доступ."
    )

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_SECRET:
        await update.message.reply_text("На сервері не задано ADMIN_SECRET."); return
    if not context.args or context.args[0] != ADMIN_SECRET:
        await update.message.reply_text("Невірний секрет."); return
    register(update.effective_user.id,"admin","Андрій")
    await update.message.reply_text("Андрій зареєстрований як адміністратор.", reply_markup=MENU_ADMIN)

async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0] not in ("Влад","Ромчик"):
        await update.message.reply_text("Напиши /join Влад або /join Ромчик"); return
    name=context.args[0]
    aid=admin_id()
    if not aid:
        await update.message.reply_text("Спочатку Андрій має зареєструватися."); return
    context.bot_data[f"pending:{update.effective_user.id}"]=name
    kb=InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ Підтвердити {name}", callback_data=f"joinok:{update.effective_user.id}:{name}"),
        InlineKeyboardButton("❌ Відхилити", callback_data=f"joinno:{update.effective_user.id}")
    ]])
    await context.bot.send_message(aid, f"Запит на доступ: {name}\nTelegram ID: {update.effective_user.id}", reply_markup=kb)
    await update.message.reply_text("Запит надіслано Андрію.")

async def join_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    u=get_user(q.from_user.id)
    if not u or u["role"]!="admin": return
    parts=q.data.split(":")
    tg=int(parts[1])
    if parts[0]=="joinok":
        name=parts[2]; register(tg,"child",name)
        await q.edit_message_text(f"✅ {name} підключений.")
        await context.bot.send_message(tg, f"Доступ підтверджено. Привіт, {name}!", reply_markup=MENU_CHILD)
    else:
        await q.edit_message_text("❌ Запит відхилено.")
        await context.bot.send_message(tg,"Запит на доступ відхилено.")

# ----- monthly study flow -----
SUBJECT, AVG = range(2)

async def report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="child": return ConversationHandler.END
    context.user_data["subjects"]=[]
    await update.message.reply_text(
        "Починаємо навчання. Напиши назву першого предмета.\n"
        "Коли закінчиш — напиши: ГОТОВО"
    )
    return SUBJECT

async def subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t=update.message.text.strip()
    if t.upper()=="ГОТОВО":
        items=context.user_data.get("subjects",[])
        if not items:
            await update.message.reply_text("Поки немає жодного предмета. Введи назву."); return SUBJECT
        c=db(); m=month_key()
        c.execute("DELETE FROM subjects WHERE tg_id=? AND month=?",(update.effective_user.id,m))
        c.executemany("INSERT INTO subjects(tg_id,month,subject,avg) VALUES(?,?,?,?)",
                      [(update.effective_user.id,m,s,a) for s,a in items])
        c.commit(); c.close()
        b=study_bonus(update.effective_user.id,m)
        await update.message.reply_text(f"Навчання збережено. Розрахований бонус за навчання: {b} грн.", reply_markup=MENU_CHILD)
        return ConversationHandler.END
    context.user_data["current_subject"]=t
    await update.message.reply_text(f"Середній бал з «{t}» за місяць (0–12):")
    return AVG

async def avg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: a=float(update.message.text.replace(",","."))
    except: await update.message.reply_text("Введи число, наприклад 10,7."); return AVG
    if not 0<=a<=12:
        await update.message.reply_text("Бал має бути від 0 до 12."); return AVG
    s=context.user_data["current_subject"]
    context.user_data["subjects"].append((s,a))
    await update.message.reply_text("Збережено. Наступний предмет або ГОТОВО:")
    return SUBJECT

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано.", reply_markup=MENU_CHILD)
    return ConversationHandler.END

# ----- achievement flow -----
CAT, DESC = range(2,4)
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="child": return ConversationHandler.END
    kb=ReplyKeyboardMarkup([["Спорт","Книги"],["Допомога","Саморозвиток"],["Скасувати"]],resize_keyboard=True,one_time_keyboard=True)
    await update.message.reply_text("Що додаємо?",reply_markup=kb); return CAT

async def cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mp={"Спорт":"sport","Книги":"books","Допомога":"help","Саморозвиток":"development"}
    if update.message.text=="Скасувати": return await cancel(update,context)
    if update.message.text not in mp:
        await update.message.reply_text("Вибери категорію кнопкою."); return CAT
    context.user_data["cat"]=mp[update.message.text]
    await update.message.reply_text("Коротко опиши результат:"); return DESC

async def desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat=context.user_data["cat"]; d=update.message.text.strip(); m=month_key()
    c=db(); cur=c.execute("INSERT INTO entries(tg_id,month,category,description) VALUES(?,?,?,?)",
                          (update.effective_user.id,m,cat,d)); eid=cur.lastrowid; c.commit(); c.close()
    u=get_user(update.effective_user.id); aid=admin_id()
    label,maxv=CATS[cat]
    kb=InlineKeyboardMarkup([[
        InlineKeyboardButton("0",callback_data=f"amt:{eid}:0"),
        InlineKeyboardButton("100",callback_data=f"amt:{eid}:100"),
        InlineKeyboardButton("300",callback_data=f"amt:{eid}:300"),
        InlineKeyboardButton("500",callback_data=f"amt:{eid}:500"),
        InlineKeyboardButton("700",callback_data=f"amt:{eid}:700"),
    ],[
        InlineKeyboardButton("Інша сума",callback_data=f"custom:{eid}")
    ]])
    if aid:
        await context.bot.send_message(aid,f"🔔 {u['name']}: {label}\n{d}\nМаксимум категорії за правилами: до {maxv} грн",reply_markup=kb)
    await update.message.reply_text("Записав. Надіслав Андрію на підтвердження.",reply_markup=MENU_CHILD)
    return ConversationHandler.END

async def amount_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    u=get_user(q.from_user.id)
    if not u or u["role"]!="admin": return
    _,eid,amt=q.data.split(":"); eid=int(eid); amt=int(amt)
    c=db(); row=c.execute("SELECT * FROM entries WHERE id=?",(eid,)).fetchone()
    if not row: c.close(); return
    maxv=CATS[row["category"]][1]
    amt=min(amt,maxv)
    c.execute("UPDATE entries SET amount=?,status='approved' WHERE id=?",(amt,eid)); c.commit(); c.close()
    child=get_user(row["tg_id"])
    await q.edit_message_text(q.message.text+f"\n\n✅ Підтверджено: {amt} грн")
    await context.bot.send_message(row["tg_id"],f"✅ {CATS[row['category']][0]}: Андрій підтвердив {amt} грн.")

async def custom_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    context.user_data["custom_eid"]=int(q.data.split(":")[1])
    await q.message.reply_text("Введи суму командою /sum число, наприклад /sum 450")

async def set_sum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="admin": return
    eid=context.user_data.get("custom_eid")
    if not eid or not context.args: await update.message.reply_text("Спочатку натисни «Інша сума»."); return
    try: amt=int(context.args[0])
    except: await update.message.reply_text("Приклад: /sum 450"); return
    c=db(); row=c.execute("SELECT * FROM entries WHERE id=?",(eid,)).fetchone()
    if not row: c.close(); return
    maxv=CATS[row["category"]][1]; amt=max(0,min(amt,maxv))
    c.execute("UPDATE entries SET amount=?,status='approved' WHERE id=?",(amt,eid)); c.commit(); c.close()
    await update.message.reply_text(f"✅ Підтверджено {amt} грн.")
    await context.bot.send_message(row["tg_id"],f"✅ {CATS[row['category']][0]}: Андрій підтвердив {amt} грн.")
    context.user_data.pop("custom_eid",None)

async def my_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="child": return
    m=month_key(); s,o,b,t=summary(u["tg_id"],m)
    await update.message.reply_text(
        f"📊 {u['name']} — {m}\n"
        f"База: {BASE} грн\nНавчання: {s} грн\nІнші підтверджені бонуси: {o} грн\n"
        f"Бонуси разом (до {BONUS_CAP}): {b} грн\n\n💰 Разом: {t} грн\n"
        f"70% особисті: {round(t*.7)} грн\n20% накопичення: {round(t*.2)} грн\n10% добрі справи: {round(t*.1)} грн"
    )

async def admin_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=get_user(update.effective_user.id)
    if not u or u["role"]!="admin": return
    c=db(); kids=c.execute("SELECT * FROM users WHERE role='child' ORDER BY name").fetchall(); c.close()
    m=month_key()
    if not kids: await update.message.reply_text("Хлопці ще не підключені."); return
    parts=[]
    for k in kids:
        s,o,b,t=summary(k["tg_id"],m)
        parts.append(f"{k['name']}: навчання {s}, інші {o}, бонус {b}, разом {t} грн")
    await update.message.reply_text("📊 "+m+"\n"+"\n".join(parts))

async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "База — 2 000 грн/міс. Бонусний фонд — до 4 000 грн. Максимум — 6 000 грн.\n"
        "Навчання: середній бал рахується окремо по кожному предмету; загальний бонус за навчання — до 1 500 грн.\n"
        "Інші напрямки: спорт, книги, допомога, саморозвиток. Суб'єктивні бонуси підтверджує Андрій.\n"
        "Гроші: 70% особисті витрати / 20% накопичення / 10% добрі справи."
    )

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t=update.message.text
    if t=="📊 Мій підсумок": return await my_summary(update,context)
    if t in ("👥 Звіти Влада і Ромчика","📊 Підсумок місяця"): return await admin_reports(update,context)
    if t=="❓ Правила": return await rules(update,context)
    if t=="✅ На підтвердження":
        c=db(); rows=c.execute("""SELECT e.*,u.name FROM entries e JOIN users u ON u.tg_id=e.tg_id
                                 WHERE e.status='pending' ORDER BY e.id""").fetchall(); c.close()
        if not rows: await update.message.reply_text("Немає записів на підтвердження."); return
        for r in rows:
            kb=InlineKeyboardMarkup([[
                InlineKeyboardButton("0",callback_data=f"amt:{r['id']}:0"),
                InlineKeyboardButton("300",callback_data=f"amt:{r['id']}:300"),
                InlineKeyboardButton("500",callback_data=f"amt:{r['id']}:500"),
                InlineKeyboardButton("700",callback_data=f"amt:{r['id']}:700")
            ],[InlineKeyboardButton("Інша сума",callback_data=f"custom:{r['id']}")]])
            await update.message.reply_text(f"{r['name']}: {CATS[r['category']][0]}\n{r['description']}",reply_markup=kb)

def main():
    db().close()
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("admin",admin))
    app.add_handler(CommandHandler("join",join))
    app.add_handler(CommandHandler("sum",set_sum))
    app.add_handler(CallbackQueryHandler(join_cb,pattern=r"^join(ok|no):"))
    app.add_handler(CallbackQueryHandler(amount_cb,pattern=r"^amt:"))
    app.add_handler(CallbackQueryHandler(custom_cb,pattern=r"^custom:"))
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^📝 Підсумки місяця$"),report_start)],
        states={SUBJECT:[MessageHandler(filters.TEXT & ~filters.COMMAND,subject)],
                AVG:[MessageHandler(filters.TEXT & ~filters.COMMAND,avg)]},
        fallbacks=[CommandHandler("cancel",cancel)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^➕ Додати досягнення$"),add_start)],
        states={CAT:[MessageHandler(filters.TEXT & ~filters.COMMAND,cat)],
                DESC:[MessageHandler(filters.TEXT & ~filters.COMMAND,desc)]},
        fallbacks=[CommandHandler("cancel",cancel)]
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_router))
    app.run_polling()

if __name__=="__main__":
    main()
