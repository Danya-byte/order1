import os
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import BadRequest
from database import Database
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
CHANNEL = "@romakottochannel"
ADMINS = [1940359844]

db = Database(DATABASE_URL)


def check_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMINS:
            if update.message:
                await update.message.reply_text("⛔ У вас нет доступа!")
            elif update.callback_query:
                await update.callback_query.answer("⛔ У вас нет доступа!", show_alert=True)
            return
        return await func(update, context)

    return wrapper


def check_captcha(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        captcha = await db.fetchrow("SELECT passed FROM captcha WHERE user_id = $1", user_id)
        if not captcha or not captcha['passed']:
            await update.message.reply_text("⚠️ Сначала пройдите капчу: /start")
            return
        return await func(update, context)

    return wrapper


def check_subscription(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        try:
            member = await context.bot.get_chat_member(CHANNEL, user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                await request_subscription(update.message, user_id, True)
                return
            else:
                await db.execute("UPDATE users SET subscribed = TRUE WHERE user_id = $1", user_id)
        except Exception as e:
            logger.error(f"Ошибка проверки подписки: {e}")
            await update.message.reply_text("⚠️ Опс... похоже вы не подписаны,сделайте это нажав /start")
            return
        return await func(update, context)

    return wrapper


async def init_bot():
    await db.connect()
    await db.init_tables()
    for admin_id in ADMINS:
        await db.execute("INSERT INTO admins (admin_id) VALUES ($1) ON CONFLICT DO NOTHING", admin_id)


async def back_to_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    is_admin = user_id in ADMINS

    text = "📜 Главное меню:"
    keyboard = [
        [InlineKeyboardButton("👥 Мои рефералы", callback_data='my_refs'),
         InlineKeyboardButton("🏆 Лидерборд", callback_data='leaders')],
        [InlineKeyboardButton("🔗 Реферальная ссылка", callback_data='get_ref_link')]
    ]
    if is_admin:
        keyboard.append([
            InlineKeyboardButton("📊 Статистика", callback_data='stats'),
            InlineKeyboardButton("🤖 Список ботов", callback_data='bots')
        ])

    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise e


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    referrer_id = int(context.args[0]) if context.args else None
    await db.execute('''
        INSERT INTO users (user_id, username, referrer_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (user_id) DO UPDATE
        SET username = EXCLUDED.username
    ''', user.id, user.username, referrer_id)

    keyboard = [[InlineKeyboardButton("✅ Я не бот", callback_data='captcha')]]
    message = await update.message.reply_text("Подтвердите, что вы не бот:",
                                              reply_markup=InlineKeyboardMarkup(keyboard))
    await db.execute('''
        INSERT INTO captcha (user_id, message_id) 
        VALUES ($1, $2)
        ON CONFLICT (user_id) DO UPDATE
        SET message_id = EXCLUDED.message_id,
            passed = FALSE
    ''', user.id, message.message_id)


async def handle_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await db.execute("UPDATE captcha SET passed = TRUE WHERE user_id = $1", query.from_user.id)
    try:
        member = await context.bot.get_chat_member(CHANNEL, query.from_user.id)
        if member.status in ['member', 'administrator', 'creator']:
            await db.execute("UPDATE users SET subscribed = TRUE WHERE user_id = $1", query.from_user.id)
            await send_referral_link(query.message, query.from_user.id)
            return
    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
    await request_subscription(query.message, query.from_user.id)


async def request_subscription(message, user_id, is_retry=False):
    text = "❌ Вы не подписаны! Подпишитесь:" if is_retry else "Подпишитесь на канал:"
    keyboard = [
        [InlineKeyboardButton("Перейти в канал", url=f"https://t.me/{CHANNEL[1:]}")],
        [InlineKeyboardButton("Проверить подписку", callback_data='check_sub')]
    ]
    try:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise e


async def check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        member = await context.bot.get_chat_member(CHANNEL, query.from_user.id)
        if member.status in ['member', 'administrator', 'creator']:
            await db.execute("UPDATE users SET subscribed = TRUE WHERE user_id = $1", query.from_user.id)
            await send_referral_link(query.message, query.from_user.id)
        else:
            await query.answer("❌ Вы не подписаны!", show_alert=True)
            await request_subscription(query.message, query.from_user.id, True)
    except Exception as e:
        logger.error(f"Ошибка проверки: {e}")
        await query.answer("⚠️ Ошибка проверки", show_alert=True)


async def send_referral_link(message, user_id):
    ref_link = f"https://t.me/romakotto_bot?start={user_id}"
    keyboard = [
        [InlineKeyboardButton("📋 Скопировать ссылку", callback_data=f'copy_{user_id}')],
        [InlineKeyboardButton("👥 Мои рефералы", callback_data='my_refs')],
        [InlineKeyboardButton("🔙 Назад", callback_data='back_to_commands')]
    ]
    await message.edit_text(f"🎉 Ваша реферальная ссылка:\n`{ref_link}`", parse_mode='Markdown',
                            reply_markup=InlineKeyboardMarkup(keyboard))


@check_admin
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now().replace(hour=0, minute=0, second=0)
    week_ago = today - timedelta(days=7)
    total = await db.fetchrow("SELECT COUNT(*) as total FROM users")
    daily = await db.fetchrow("SELECT COUNT(*) FROM users WHERE created_at >= $1", today)
    weekly = await db.fetchrow("SELECT COUNT(*) FROM users WHERE created_at >= $1", week_ago)
    top = await db.fetchrow('''
        SELECT u.username, COUNT(r.referred_id) as count 
        FROM referrals r
        JOIN users u ON r.referrer_id = u.user_id
        GROUP BY u.username 
        ORDER BY count DESC 
        LIMIT 1
    ''')
    response = [
        "📈 Статистика:",
        f"👥 Всего: {total['total']}",
        f"🕒 За день: {daily['count']}",
        f"📅 За неделю: {weekly['count']}",
    ]
    if top and top['count'] > 0:
        response.append(f"🏆 Топ рефовод: @{top['username']} ({top['count']})")

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back_to_commands')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text("\n".join(response), reply_markup=reply_markup)
    else:
        await update.message.reply_text("\n".join(response), reply_markup=reply_markup)


@check_admin
async def bots_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bots = await db.fetch('''
        SELECT u.user_id, u.username 
        FROM users u
        LEFT JOIN captcha c ON u.user_id = c.user_id
        WHERE c.passed IS FALSE OR c.passed IS NULL
    ''')
    response = ["🚫 Не прошли капчу:"] if bots else ["🤖 Все прошли капчу!"]
    for bot in bots[:15]:
        response.append(f"ID: {bot['user_id']} | @{bot['username'] or 'нет юзернейма'}")

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back_to_commands')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text("\n".join(response), reply_markup=reply_markup)
    else:
        await update.message.reply_text("\n".join(response), reply_markup=reply_markup)


@check_captcha
@check_subscription
async def leaders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top = await db.fetch('''
        SELECT u.username, COUNT(*) as count 
        FROM referrals r
        JOIN users u ON r.referrer_id = u.user_id
        GROUP BY u.username 
        ORDER BY count DESC 
        LIMIT 15
    ''')
    response = ["🏆 Топ-15 рефереров:"]
    for idx, row in enumerate(top, 1):
        response.append(f"{idx}. @{row['username']} — {row['count']}")

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back_to_commands')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text("\n".join(response), reply_markup=reply_markup)
    else:
        await update.message.reply_text("\n".join(response), reply_markup=reply_markup)


@check_captcha
@check_subscription
async def my_refs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    message = query.message if query else update.message
    user_id = query.from_user.id if query else update.effective_user.id
    if query:
        await query.answer()

    total = await db.fetchrow("SELECT COUNT(*) FROM referrals WHERE referrer_id = $1", user_id)
    refs = await db.fetch('''
        SELECT u.username, r.created_at 
        FROM referrals r
        JOIN users u ON r.referred_id = u.user_id
        WHERE r.referrer_id = $1
        ORDER BY r.created_at DESC
        LIMIT 15
    ''', user_id)

    new_response = [f"👥 Всего рефералов: {total['count']}"]
    if refs:
        new_response.append("Последние 15:")
        for idx, ref in enumerate(refs, 1):
            username = f"@{ref['username']}" if ref['username'] else "Аноним"
            date = ref['created_at'].strftime("%d.%m.%Y")
            new_response.append(f"{idx}. {username} — {date}")
    else:
        new_response.append("😔 Пока нет рефералов")

    new_keyboard = [
        [InlineKeyboardButton("🔗 Моя реферальная ссылка", callback_data='get_ref_link')],
        [InlineKeyboardButton("🔄 Обновить", callback_data='my_refs')],
        [InlineKeyboardButton("🔙 Назад", callback_data='back_to_commands')]
    ]
    new_reply_markup = InlineKeyboardMarkup(new_keyboard)

    try:
        current_text = message.text
        current_markup = message.reply_markup.to_dict() if message.reply_markup else None
        new_text = "\n".join(new_response)
        if new_text != current_text or new_reply_markup.to_dict() != current_markup:
            await message.edit_text(new_text, reply_markup=new_reply_markup)
        else:
            if query:
                await query.answer("✅ Данные актуальны!")
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise e


async def handle_copy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = int(query.data.split('_')[1])
    ref_link = f"https://t.me/romakotto_bot?start={user_id}"

    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data='back_to_commands')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"🔗 *Ваша ссылка:*\n`{ref_link}`\n\nНажмите для копирования",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


async def handle_get_ref_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await send_referral_link(query.message, query.from_user.id)


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling update:", exc_info=context.error)
    if update.callback_query:
        await update.callback_query.answer("⚠️ Произошла ошибка. Попробуйте позже.")
    elif update.message:
        await update.message.reply_text("⚠️ Что-то пошло не так. Повторите попытку.")


async def shutdown(context: ContextTypes.DEFAULT_TYPE):
    await db.close()


if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    app = Application.builder().token(BOT_TOKEN).post_shutdown(shutdown).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_captcha, pattern='^captcha$'))
    app.add_handler(CallbackQueryHandler(check_subscription_callback, pattern='^check_sub$'))
    app.add_handler(CallbackQueryHandler(handle_copy, pattern='^copy_'))
    app.add_handler(CallbackQueryHandler(my_refs, pattern='^my_refs$'))
    app.add_handler(CallbackQueryHandler(handle_get_ref_link, pattern='^get_ref_link$'))
    app.add_handler(CallbackQueryHandler(back_to_commands, pattern='^back_to_commands$'))
    app.add_handler(CallbackQueryHandler(leaders, pattern='^leaders$'))
    app.add_handler(CallbackQueryHandler(stats_command, pattern='^stats$'))
    app.add_handler(CallbackQueryHandler(bots_command, pattern='^bots$'))

    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("bots", bots_command))
    app.add_handler(CommandHandler("leaders", leaders))
    app.add_handler(CommandHandler("my_referrals", my_refs))

    app.add_error_handler(error_handler)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_bot())
    print("Бот запущен...")
    app.run_polling()