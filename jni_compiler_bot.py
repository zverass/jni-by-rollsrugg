"""
🔥 JNI COMPILER BOT - Полнофункциональный Telegram бот для компиляции JNI
Разработано для @rollsrug_inc

Функции:
- Проверка подписки на канал
- Система лимитов (5 компиляций в день)
- Выбор NDK версии
- Загрузка архивов и управление заказами
- Админ-панель для обработки заказов
- Автоматический сброс лимитов в 12:00 МСК
"""

import logging
import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import sqlite3

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    ReplyKeyboardMarkup, ReplyKeyboardRemove, ChatMember
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)
from telegram.constants import ChatAction
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ===================== КОНФИГУРАЦИЯ =====================

# ⚠️ ИЗМЕНИ ЭТИ ЗНАЧЕНИЯ НА СВОИ
BOT_TOKEN = "8653726246:AAEfDkFz6pVvBEPd-lIg_iT_oP8CkQYCG7M"  # Получи от @BotFather
ADMIN_ID = 7988195484  # Твой Telegram ID
CHANNEL_ID = -1003617149620  # ID канала (можно узнать через бота @userinfobot)
CHANNEL_USERNAME = "@rollsrug_inc"  # Публичное имя канала

# Настройки хранилища
DB_NAME = "jni_compiler.db"
UPLOADS_DIR = Path("uploads")
RESULTS_DIR = Path("results")

# Лимиты
DAILY_LIMIT = 5  # Компиляций в день
MAX_FILE_SIZE = 140 * 1024 * 1024  # 140 МБ в байтах
RESET_HOUR = 12  # Сброс лимитов в 12:00 МСК (UTC+3)
RESET_MINUTE = 0

# Версии NDK
NDK_VERSIONS = {
    "ndk-r25c": "🔥 Новый движок",
    "ndk-r21e": "⚙️ Средний вариант",
    "ndk-r16b": "🛠️ Старый движок"
}

# Состояния для ConversationHandler
START, CHOOSE_NDK, UPLOAD_FILE = range(3)

# ===================== ЛОГИРОВАНИЕ =====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ===================== ИНИЦИАЛИЗАЦИЯ ПАПОК =====================
UPLOADS_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

# ===================== БАЗА ДАННЫХ =====================

class Database:
    """Класс для работы с SQLite базой данных"""
    
    def __init__(self, db_path: str = DB_NAME):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """Инициализация таблиц БД"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Таблица пользователей и лимитов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                used_today INTEGER DEFAULT 0,
                last_reset TEXT DEFAULT CURRENT_TIMESTAMP,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                is_subscribed BOOLEAN DEFAULT 0
            )
        ''')
        
        # Таблица заказов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                ndk_version TEXT NOT NULL,
                file_id TEXT,
                file_name TEXT,
                status TEXT DEFAULT 'waiting',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT,
                result_file_id TEXT,
                rejection_reason TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
    
    def get_user(self, user_id: int) -> Optional[dict]:
        """Получить информацию о пользователе"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                'user_id': row[0],
                'username': row[1],
                'used_today': row[2],
                'last_reset': row[3],
                'is_subscribed': row[5]
            }
        return None
    
    def create_user(self, user_id: int, username: str) -> None:
        """Создать новую запись пользователя"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO users (user_id, username) VALUES (?, ?)',
            (user_id, username)
        )
        conn.commit()
        conn.close()
    
    def increment_usage(self, user_id: int) -> None:
        """Увеличить счетчик использований"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET used_today = used_today + 1 WHERE user_id = ?',
            (user_id,)
        )
        conn.commit()
        conn.close()
    
    def reset_all_limits(self) -> None:
        """Сбросить лимиты всех пользователей"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET used_today = 0')
        conn.commit()
        conn.close()
        logger.info("🔄 Лимиты сброшены для всех пользователей")
    
    def set_subscription_status(self, user_id: int, is_subscribed: bool) -> None:
        """Обновить статус подписки"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET is_subscribed = ? WHERE user_id = ?',
            (is_subscribed, user_id)
        )
        conn.commit()
        conn.close()
    
    def create_order(self, user_id: int, username: str, ndk_version: str, 
                    file_id: str, file_name: str) -> int:
        """Создать новый заказ на компиляцию"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO orders (user_id, username, ndk_version, file_id, file_name)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, ndk_version, file_id, file_name))
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return order_id
    
    def get_orders(self, status: str = 'waiting') -> list:
        """Получить заказы по статусу"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM orders WHERE status = ? ORDER BY created_at', (status,))
        rows = cursor.fetchall()
        conn.close()
        
        orders = []
        for row in rows:
            orders.append({
                'id': row[0],
                'user_id': row[1],
                'username': row[2],
                'ndk_version': row[3],
                'file_id': row[4],
                'file_name': row[5],
                'status': row[6],
                'created_at': row[7]
            })
        return orders
    
    def get_order(self, order_id: int) -> Optional[dict]:
        """Получить информацию о конкретном заказе"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                'id': row[0],
                'user_id': row[1],
                'username': row[2],
                'ndk_version': row[3],
                'file_id': row[4],
                'file_name': row[5],
                'status': row[6],
                'created_at': row[7]
            }
        return None
    
    def update_order_status(self, order_id: int, status: str, 
                           result_file_id: str = None, rejection_reason: str = None) -> None:
        """Обновить статус заказа"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if status == 'done':
            cursor.execute('''
                UPDATE orders 
                SET status = ?, result_file_id = ?, completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (status, result_file_id, order_id))
        elif status == 'rejected':
            cursor.execute('''
                UPDATE orders 
                SET status = ?, rejection_reason = ?, completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (status, rejection_reason, order_id))
        else:
            cursor.execute('UPDATE orders SET status = ? WHERE id = ?', (status, order_id))
        
        conn.commit()
        conn.close()

# ===================== ИНИЦИАЛИЗАЦИЯ БД =====================
db = Database()

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================

async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Проверить, подписан ли пользователь на канал
    """
    try:
        # Пробуем проверить подписку используя username канала
        channel_check = CHANNEL_USERNAME.lstrip('@')
        logger.info(f"🔍 Проверка подписки пользователя {user_id} на канал @{channel_check}")
        
        member = await context.bot.get_chat_member(f"@{channel_check}", user_id)
        
        # Используем правильные константы ChatMember
        is_subscribed = member.status in [
            ChatMember.MEMBER, 
            ChatMember.ADMINISTRATOR, 
            ChatMember.CREATOR
        ]
        
        logger.info(f"✅ Статус подписки {user_id}: {member.status} -> {'Подписан' if is_subscribed else 'Не подписан'}")
        
        db.set_subscription_status(user_id, is_subscribed)
        return is_subscribed
    except Exception as e:
        logger.error(f"❌ Ошибка проверки подписки для {user_id}: {e}")
        logger.error(f"   Детали: попытка проверить канал @{CHANNEL_USERNAME.lstrip('@')}")
        return False

def get_user_status_text(user_id: int) -> str:
    """Получить текст статуса лимитов пользователя"""
    user = db.get_user(user_id)
    
    if not user:
        return "❓ Пользователь не найден"
    
    used = user['used_today']
    status = "✅ Доступно" if used < DAILY_LIMIT else "❌ Лимит исчерпан"
    
    return (
        f"Лимиты компиляций 📊\n\n"
        f"· Статус: {status}\n"
        f"· Использовано: {used}/{DAILY_LIMIT}\n\n"
        f"Следующий сброс: ⏰ 12:00 МСК"
    )

def get_start_keyboard():
    """Создать клавиатуру с кнопкой компиляции"""
    keyboard = [
        [InlineKeyboardButton("🚀 Компилировать JNI", callback_data="compile")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_ndk_keyboard():
    """Создать клавиатуру выбора NDK"""
    keyboard = [
        [InlineKeyboardButton("🔥 ndk-r25c", callback_data="ndk_r25c")],
        [InlineKeyboardButton("⚙️ ndk-r21e", callback_data="ndk_r21e")],
        [InlineKeyboardButton("🛠️ ndk-r16b", callback_data="ndk_r16b")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_subscription_keyboard():
    """Создать клавиатуру для подписки"""
    keyboard = [
        [InlineKeyboardButton("🔔 Подписаться", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")],
        [InlineKeyboardButton("✅ Я подписался", callback_data="check_subscription")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    """Создать админ клавиатуру"""
    keyboard = [
        [InlineKeyboardButton("📋 Открыть заказы", callback_data="admin_orders")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ===================== ОБРАБОТЧИКИ КОМАНД =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /start - приветствие и информация о боте"""
    user = update.effective_user
    
    # Создать пользователя в БД если его там нет
    if not db.get_user(user.id):
        db.create_user(user.id, user.username or "Unknown")
    
    # Проверить подписку
    is_subscribed = await check_subscription(user.id, context)
    
    if not is_subscribed:
        await update.message.reply_text(
            f"🔐 Для использования бота подпишись на канал {CHANNEL_USERNAME}",
            reply_markup=get_subscription_keyboard()
        )
        return ConversationHandler.END
    
    # Основное сообщение
    start_text = (
        "Привет! 😎\n\n"
        "Ты в лучшем компиляторе JNI от @rollsrug_inc. 🔥\n\n"
        f"{get_user_status_text(user.id)}\n\n"
        "---\n\n"
        "Чтобы бот корректно работал, от тебя нужен всего один архив 📦 "
        "со всеми папками твоего JNI.\n\n"
        "Поддерживаемые форматы: 🗜️ .zip, .7z\n\n"
        "Архив должен быть уже настроен под твой IP. 🌐\n\n"
        "Просто нажми кнопку «Компилировать JNI» 🚀, выбери NDK, "
        "отправь готовый архив — и мы приступим к компиляции. ⚙️"
    )
    
    await update.message.reply_text(
        start_text,
        reply_markup=get_start_keyboard()
    )
    
    return START

async def compile_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик кнопки 'Компилировать JNI'"""
    query = update.callback_query
    await query.answer()
    
    # Проверить подписку еще раз
    if not await check_subscription(query.from_user.id, context):
        await query.edit_message_text(
            f"🔐 Подпишись на {CHANNEL_USERNAME} для использования",
            reply_markup=get_subscription_keyboard()
        )
        return ConversationHandler.END
    
    # Проверить лимит
    user = db.get_user(query.from_user.id)
    if user['used_today'] >= DAILY_LIMIT:
        await query.edit_message_text(
            "❌ Вы достигли лимита, следующее обновление в 12:00 МСК"
        )
        return ConversationHandler.END
    
    ndk_text = (
        "Теперь выбери, какой NDK подходит для твоего JNI: 🤔\n\n"
        "· 🔥 Новый движок — ndk-r25c\n"
        "· ⚙️ Средний вариант — ndk-r21e\n"
        "· 🛠️ Старый движок — ndk-r16b\n\n"
        "Выбирай тот вариант, под который писался твой JNI. ✅"
    )
    
    await query.edit_message_text(
        ndk_text,
        reply_markup=get_ndk_keyboard()
    )
    
    return CHOOSE_NDK

async def ndk_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик выбора NDK версии"""
    query = update.callback_query
    await query.answer()
    
    # Сохранить выбранную NDK версию
    ndk_version = query.data.replace("ndk_", "").replace("_", "-")
    context.user_data['ndk_version'] = ndk_version
    
    await query.edit_message_text(
        f"✅ Ты выбрал: **{ndk_version}**\n\n"
        f"Теперь отправь архив (.zip или .7z) до 140 МБ 📦",
        parse_mode='Markdown'
    )
    
    return UPLOAD_FILE

async def file_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик загрузки файла архива"""
    user = update.effective_user
    
    # Проверить, что это документ
    if not update.message.document:
        await update.message.reply_text(
            "❌ Пожалуйста, отправь файл архива (.zip или .7z)"
        )
        return UPLOAD_FILE
    
    document = update.message.document
    file_name = document.file_name
    
    # Проверить расширение файла
    if not file_name.lower().endswith(('.zip', '.7z')):
        await update.message.reply_text(
            "❌ Поддерживаются только .zip и .7z\n"
            "Отправь архив правильного формата"
        )
        return UPLOAD_FILE
    
    # Проверить размер файла
    if document.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(
            f"❌ Файл слишком большой!\n"
            f"Максимум: 140 МБ\n"
            f"Твой файл: {document.file_size / 1024 / 1024:.1f} МБ"
        )
        return UPLOAD_FILE
    
    # Создать заказ в БД
    ndk_version = context.user_data.get('ndk_version', 'unknown')
    order_id = db.create_order(
        user.id,
        user.username or "Unknown",
        ndk_version,
        document.file_id,
        file_name
    )
    
    # Увеличить счетчик использований
    db.increment_usage(user.id)
    
    # Отправить подтверждение пользователю
    await update.message.reply_text(
        "✅ Архив принят! Ожидайте компиляцию, обычно это занимает до 30 минут ⏳\n\n"
        f"📦 Номер заказа: **#{order_id}**\n"
        f"🛠️ NDK: {ndk_version}\n"
        f"📄 Файл: {file_name}",
        parse_mode='Markdown',
        reply_markup=get_start_keyboard()
    )
    
    # Отправить уведомление админу
    order_text = (
        f"🆕 Новый заказ на компиляцию!\n\n"
        f"📦 ID заказа: #{order_id}\n"
        f"👤 Пользователь: @{user.username}\n"
        f"🛠️ NDK: {ndk_version}\n"
        f"📄 Файл: {file_name}\n"
        f"⏰ Время: {datetime.now().strftime('%H:%M:%S')}"
    )
    
    try:
        await context.bot.send_message(
            ADMIN_ID,
            order_text,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления админу: {e}")
    
    logger.info(f"✅ Создан заказ #{order_id} от {user.username}")
    
    return ConversationHandler.END

async def check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик кнопки 'Я подписался'"""
    query = update.callback_query
    user = query.from_user
    
    try:
        # Подтверждаем нажатие кнопки
        await query.answer("⏳ Проверяю подписку...", show_alert=False)
        logger.info(f"👤 {user.username} ({user.id}) нажал кнопку 'Я подписался'")
        
        # Проверяем подписку
        is_subscribed = await check_subscription(user.id, context)
        
        if is_subscribed:
            logger.info(f"✅ {user.username} подписан! Доступ предоставлен")
            await query.edit_message_text(
                "✅ Спасибо за подписку! Теперь ты можешь использовать бота.\n\n"
                "Нажми /start чтобы начать"
            )
        else:
            logger.warning(f"❌ {user.username} не подписан на канал")
            await query.answer(
                f"❌ Ты ещё не подписан на {CHANNEL_USERNAME}\n\n"
                "Подпишись и попробуй снова!",
                show_alert=True
            )
            await query.edit_message_text(
                f"❌ Подписка не найдена на {CHANNEL_USERNAME}\n\n"
                "Убедись что ты подписан на канал и нажми кнопку снова",
                reply_markup=get_subscription_keyboard()
            )
    except Exception as e:
        logger.error(f"❌ Ошибка в check_subscription_callback: {e}")
        await query.answer("❌ Произошла ошибка при проверке подписки", show_alert=True)

# ===================== АДМИН ФУНКЦИИ =====================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /admin - доступ к админ панели"""
    user = update.effective_user
    
    # Проверить, что это админ
    if user.id != ADMIN_ID:
        await update.message.reply_text("❌ У тебя нет доступа к админ панели")
        logger.warning(f"⚠️ Попытка доступа к админ панели от {user.username} ({user.id})")
        return
    
    admin_text = (
        "👑 Добро пожаловать в админ панель!\n\n"
        "Здесь ты можешь управлять заказами на компиляцию."
    )
    
    await update.message.reply_text(
        admin_text,
        reply_markup=get_admin_keyboard()
    )

async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать очередь заказов"""
    query = update.callback_query
    await query.answer()
    
    # Проверить права
    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("❌ Нет доступа")
        return
    
    # Получить заказы в ожидании
    waiting_orders = db.get_orders('waiting')
    
    if not waiting_orders:
        await query.edit_message_text(
            "📭 Нет заказов в очереди",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Назад", callback_data="admin_back")
            ]])
        )
        return
    
    # Показать первый заказ в очереди
    order = waiting_orders[0]
    order_text = (
        f"📦 Заказ #{order['id']}\n"
        f"👤 Пользователь: @{order['username']}\n"
        f"🛠️ NDK: {order['ndk_version']}\n"
        f"📄 Файл: {order['file_name']}\n"
        f"⏰ Время создания: {order['created_at']}\n\n"
        f"Всего в очереди: {len(waiting_orders)}"
    )
    
    keyboard = [
        [InlineKeyboardButton("📥 Скачать архив", callback_data=f"download_file_{order['id']}")],
        [InlineKeyboardButton("✅ Отправить результат", callback_data=f"upload_result_{order['id']}")],
        [InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_order_{order['id']}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="admin_back")]
    ]
    
    await query.edit_message_text(
        order_text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    # Сохранить ID заказа для последующих операций
    context.user_data['current_order_id'] = order['id']

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать статистику"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("❌ Нет доступа")
        return
    
    # Подсчитать статистику
    waiting = len(db.get_orders('waiting'))
    done = len(db.get_orders('done'))
    rejected = len(db.get_orders('rejected'))
    
    stats_text = (
        f"📊 Статистика\n\n"
        f"⏳ В ожидании: {waiting}\n"
        f"✅ Завершено: {done}\n"
        f"❌ Отклонено: {rejected}\n"
        f"📈 Всего: {waiting + done + rejected}"
    )
    
    await query.edit_message_text(
        stats_text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Назад", callback_data="admin_back")
        ]])
    )

async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Вернуться в главное меню админ панели"""
    query = update.callback_query
    await query.answer()
    
    admin_text = (
        "👑 Админ панель\n\n"
        "Выбери действие:"
    )
    
    await query.edit_message_text(
        admin_text,
        reply_markup=get_admin_keyboard()
    )

async def download_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Скачать архив заказа"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("❌ Нет доступа")
        return
    
    # Получить ID заказа из callback_data
    order_id = int(query.data.split('_')[2])
    order = db.get_order(order_id)
    
    if not order:
        await query.answer("❌ Заказ не найден", show_alert=True)
        return
    
    try:
        # Скачать файл
        file = await context.bot.get_file(order['file_id'])
        
        # Отправить файл админу
        await context.bot.send_document(
            ADMIN_ID,
            document=order['file_id'],
            caption=f"📦 Архив заказа #{order_id}\n📄 {order['file_name']}"
        )
        
        await query.answer("✅ Файл отправлен в чат", show_alert=False)
        
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания файла: {e}")
        await query.answer("❌ Ошибка скачивания файла", show_alert=True)

async def upload_result(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запросить результат компиляции (файл .so)"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    order_id = int(query.data.split('_')[2])
    context.user_data['order_for_result'] = order_id
    
    await query.edit_message_text(
        f"📤 Отправь файл результата компиляции для заказа #{order_id}\n\n"
        "Например: libplugin.so"
    )

async def reject_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отклонить заказ"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    order_id = int(query.data.split('_')[2])
    context.user_data['order_for_rejection'] = order_id
    
    await query.edit_message_text(
        f"❌ Введи причину отклонения заказа #{order_id}:"
    )

async def handle_result_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработать результат компиляции от админа"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    # Проверить, что это результат
    if 'order_for_result' not in context.user_data:
        return
    
    if not update.message.document:
        await update.message.reply_text(
            "❌ Пожалуйста, отправь файл"
        )
        return
    
    order_id = context.user_data.pop('order_for_result')
    order = db.get_order(order_id)
    
    if not order:
        await update.message.reply_text("❌ Заказ не найден")
        return
    
    # Обновить статус заказа
    result_file_id = update.message.document.file_id
    db.update_order_status(order_id, 'done', result_file_id)
    
    # Отправить результат пользователю
    try:
        await context.bot.send_document(
            order['user_id'],
            document=result_file_id,
            caption=f"✅ Готово! Ваш результат компиляции\n\n📦 Заказ #{order_id}"
        )
        
        await update.message.reply_text(
            f"✅ Результат отправлен пользователю @{order['username']}"
        )
        
        logger.info(f"✅ Заказ #{order_id} завершен")
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки результата: {e}")
        await update.message.reply_text(f"❌ Ошибка отправки: {e}")

async def handle_rejection_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработать причину отклонения"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if 'order_for_rejection' not in context.user_data:
        return
    
    reason = update.message.text
    order_id = context.user_data.pop('order_for_rejection')
    order = db.get_order(order_id)
    
    if not order:
        await update.message.reply_text("❌ Заказ не найден")
        return
    
    # Обновить статус заказа
    db.update_order_status(order_id, 'rejected', rejection_reason=reason)
    
    # Отправить уведомление пользователю
    try:
        await context.bot.send_message(
            order['user_id'],
            f"❌ Ваш заказ #{order_id} был отклонен\n\n"
            f"📝 Причина: {reason}\n\n"
            f"Пожалуйста, проверьте архив и попробуйте снова.",
            reply_markup=get_start_keyboard()
        )
        
        await update.message.reply_text(
            f"✅ Пользователь @{order['username']} уведомлен об отклонении"
        )
        
        logger.info(f"❌ Заказ #{order_id} отклонен: {reason}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления об отклонении: {e}")
        await update.message.reply_text(f"❌ Ошибка отправки: {e}")

# ===================== АВТОМАТИЧЕСКИЙ СБРОС ЛИМИТОВ =====================

async def reset_daily_limits(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Функция для автоматического сброса лимитов в 12:00 МСК"""
    db.reset_all_limits()
    
    try:
        await context.bot.send_message(
            ADMIN_ID,
            "🔄 Лимиты сброшены для всех пользователей"
        )
    except:
        pass

# ===================== ОБРАБОТКА ОШИБОК =====================

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик ошибок"""
    logger.error(f"Exception while handling an update: {context.error}")

# ===================== MAIN - ЗАПУСК БОТА =====================

def main():
    """Главная функция для запуска бота"""
    
    # Создать приложение
    app = Application.builder().token(BOT_TOKEN).build()
    
    # ВАЖНО: Добавляем обработчик подписки ПЕРЕД ConversationHandler
    # Это гарантирует что кнопка "Я подписался" будет работать всегда
    app.add_handler(CallbackQueryHandler(check_subscription_callback, pattern="^check_subscription$"))
    
    # Создать ConversationHandler для обработки процесса компиляции
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(compile_button, pattern="^compile$")
        ],
        states={
            START: [
                CallbackQueryHandler(compile_button, pattern="^compile$")
            ],
            CHOOSE_NDK: [
                CallbackQueryHandler(ndk_chosen, pattern="^ndk_")
            ],
            UPLOAD_FILE: [
                MessageHandler(filters.Document.ALL, file_received)
            ]
        },
        fallbacks=[CommandHandler("start", start)]
    )
    
    # Добавить обработчики
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("start", start))
    
    # Админ панель
    app.add_handler(CallbackQueryHandler(admin_orders, pattern="^admin_orders$"))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
    app.add_handler(CallbackQueryHandler(admin_back, pattern="^admin_back$"))
    app.add_handler(CallbackQueryHandler(download_file, pattern="^download_file_"))
    app.add_handler(CallbackQueryHandler(upload_result, pattern="^upload_result_"))
    app.add_handler(CallbackQueryHandler(reject_order, pattern="^reject_order_"))
    
    # Обработка файлов от админа
    app.add_handler(MessageHandler(
        filters.Document.ALL & filters.User(ADMIN_ID),
        handle_result_file
    ))
    
    # Обработка текста для отклонения
    app.add_handler(MessageHandler(
        filters.TEXT & filters.User(ADMIN_ID),
        handle_rejection_reason
    ))
    
    # Обработчик ошибок
    app.add_error_handler(error_handler)
    
    # Настроить планировщик для автоматического сброса лимитов
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        reset_daily_limits,
        CronTrigger(hour=RESET_HOUR, minute=RESET_MINUTE, timezone='Europe/Moscow'),
        args=[app],
        name='reset_limits'
    )
    scheduler.start()
    
    logger.info("🚀 Бот запущен!")
    logger.info(f"⏰ Сброс лимитов каждый день в {RESET_HOUR}:{RESET_MINUTE:02d} МСК")
    
    # Запустить бота
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
