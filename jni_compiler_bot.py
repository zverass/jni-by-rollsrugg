"""
🚀 JNI COMPILER BOT - УПРОЩЕННАЯ ВЕРСИЯ
Максимальное логирование + без проверки подписки
Разработано для @rollsrug_inc
"""

import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    ChatMember
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
BOT_TOKEN = "8598894267:AAG8tUv3nDG1gjrPqLudy7lUc9FNlr8al5s"
ADMIN_ID = 7988195484
CHANNEL_USERNAME = "@rollsrug_inc"

# Настройки
DB_NAME = "jni_compiler.db"
UPLOADS_DIR = Path("uploads")
RESULTS_DIR = Path("results")

# Лимиты
DAILY_LIMIT = 5
MAX_FILE_SIZE = 140 * 1024 * 1024
RESET_HOUR = 12
RESET_MINUTE = 0

# Версии NDK
NDK_VERSIONS = {
    "ndk-r25c": "🔥 Новый движок",
    "ndk-r21e": "⚙️ Средний вариант",
    "ndk-r16b": "🛠️ Старый движок"
}

# Состояния
START, CHOOSE_NDK, UPLOAD_FILE = range(3)

# ===================== ЛОГИРОВАНИЕ =====================

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.FileHandler('bot_simple.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

logger.info("=" * 80)
logger.info("🚀 ИНИЦИАЛИЗАЦИЯ БОТА JNI COMPILER (УПРОЩЕННАЯ ВЕРСИЯ)")
logger.info("=" * 80)

# Создаем директории
UPLOADS_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)
logger.info(f"✅ Директории созданы: {UPLOADS_DIR}, {RESULTS_DIR}")

# ===================== БАЗА ДАННЫХ =====================

class Database:
    """Управление SQLite базой данных"""
    
    def __init__(self, db_path: str = DB_NAME):
        logger.info(f"📊 Инициализация БД: {db_path}")
        self.db_path = db_path
        self.init_db()
        logger.info(f"✅ БД готова")
    
    def init_db(self):
        """Инициализация таблиц БД"""
        logger.debug("🔧 Создание таблиц БД")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                used_today INTEGER DEFAULT 0,
                last_reset TEXT DEFAULT CURRENT_TIMESTAMP,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        logger.debug("✅ Таблица users создана")
        
        # Таблица заказов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                ndk_version TEXT,
                file_id TEXT,
                filename TEXT,
                status TEXT DEFAULT 'waiting',
                created_at TEXT,
                completed_at TEXT,
                result_file_id TEXT,
                rejection_reason TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        logger.debug("✅ Таблица orders создана")
        
        conn.commit()
        conn.close()
    
    def get_user(self, user_id):
        """Получить данные пользователя"""
        logger.debug(f"🔍 Получение данных пользователя {user_id}")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        user = cursor.fetchone()
        conn.close()
        if user:
            logger.debug(f"✅ Пользователь {user_id} найден")
        else:
            logger.debug(f"⚠️ Пользователь {user_id} не найден в БД")
        return user
    
    def add_user(self, user_id, username):
        """Добавить нового пользователя"""
        logger.info(f"➕ Добавление пользователя {user_id} (@{username})")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO users (user_id, username, last_reset)
                VALUES (?, ?, ?)
            ''', (user_id, username, datetime.now().isoformat()))
            conn.commit()
            logger.info(f"✅ Пользователь {user_id} добавлен в БД")
        except sqlite3.IntegrityError:
            logger.debug(f"ℹ️ Пользователь {user_id} уже в БД")
        finally:
            conn.close()
    
    def check_and_reset_limit(self, user_id):
        """Проверить и сбросить лимит если нужно"""
        logger.debug(f"⏰ Проверка лимита пользователя {user_id}")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        user = self.get_user(user_id)
        if not user:
            logger.debug(f"❌ Пользователь {user_id} не найден")
            return 0
        
        used = user[2]
        last_reset = datetime.fromisoformat(user[3])
        now = datetime.now()
        hours_passed = (now - last_reset).total_seconds() / 3600
        
        logger.debug(f"   Использовано сегодня: {used}/5")
        logger.debug(f"   Часов прошло: {hours_passed:.1f}")
        
        if hours_passed > 24:
            logger.info(f"🔄 Сброс лимита для пользователя {user_id}")
            cursor.execute('''
                UPDATE users 
                SET used_today = 0, last_reset = ?
                WHERE user_id = ?
            ''', (now.isoformat(), user_id))
            conn.commit()
            conn.close()
            logger.debug(f"✅ Лимит сброшен")
            return 0
        
        conn.close()
        return used
    
    def increment_usage(self, user_id):
        """Увеличить счетчик использования"""
        logger.info(f"📈 Увеличение счетчика использования для {user_id}")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE users 
            SET used_today = used_today + 1
            WHERE user_id = ?
        ''', (user_id,))
        
        conn.commit()
        conn.close()
        logger.debug(f"✅ Счетчик увеличен")
    
    def create_order(self, user_id, username, ndk_version, file_id, filename):
        """Создать заказ на компиляцию"""
        logger.info(f"🆕 Создание заказа: пользователь={user_id}, NDK={ndk_version}, файл={filename}")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO orders (user_id, username, ndk_version, file_id, filename, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username, ndk_version, file_id, filename, datetime.now().isoformat()))
        
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        logger.info(f"✅ Заказ #{order_id} создан")
        return order_id
    
    def get_all_orders(self):
        """Получить все заказы"""
        logger.debug("📋 Получение всех заказов")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM orders ORDER BY created_at DESC')
        orders = cursor.fetchall()
        conn.close()
        logger.debug(f"✅ Получено заказов: {len(orders)}")
        return orders
    
    def get_order(self, order_id):
        """Получить заказ по ID"""
        logger.debug(f"🔍 Получение заказа #{order_id}")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
        order = cursor.fetchone()
        conn.close()
        return order
    
    def update_order_status(self, order_id, status, result_file_id=None, rejection_reason=None):
        """Обновить статус заказа"""
        logger.info(f"📝 Обновление заказа #{order_id}: статус={status}")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if status == 'done':
            cursor.execute('''
                UPDATE orders 
                SET status = ?, result_file_id = ?, completed_at = ?
                WHERE id = ?
            ''', (status, result_file_id, datetime.now().isoformat(), order_id))
        elif status == 'rejected':
            cursor.execute('''
                UPDATE orders 
                SET status = ?, rejection_reason = ?, completed_at = ?
                WHERE id = ?
            ''', (status, rejection_reason, datetime.now().isoformat(), order_id))
        else:
            cursor.execute('UPDATE orders SET status = ? WHERE id = ?', (status, order_id))
        
        conn.commit()
        conn.close()
        logger.debug(f"✅ Заказ #{order_id} обновлен")

# Инициализируем БД
db = Database()
logger.info("✅ Объект БД инициализирован")

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================

def get_user_status_text(user_id: int) -> str:
    """Получить текст статуса лимитов пользователя"""
    logger.debug(f"📊 Получение статуса лимитов для {user_id}")
    
    user = db.get_user(user_id)
    if not user:
        logger.warning(f"⚠️ Пользователь {user_id} не найден при получении статуса")
        return "❓ Пользователь не найден"
    
    used = user[2]
    status = "✅ Доступно" if used < DAILY_LIMIT else "❌ Лимит исчерпан"
    
    text = (
        f"Лимиты компиляций 📊\n\n"
        f"· Статус: {status}\n"
        f"· Использовано: {used}/{DAILY_LIMIT}\n\n"
        f"Следующий сброс: ⏰ 12:00 МСК"
    )
    
    logger.debug(f"✅ Статус получен: {used}/{DAILY_LIMIT}")
    return text

def get_start_keyboard():
    """Создать клавиатуру с кнопкой компиляции"""
    logger.debug("🔘 Создание клавиатуры /start")
    keyboard = [
        [InlineKeyboardButton("🚀 Компилировать JNI", callback_data="compile")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_ndk_keyboard():
    """Создать клавиатуру выбора NDK"""
    logger.debug("🔘 Создание клавиатуры выбора NDK")
    keyboard = [
        [InlineKeyboardButton("🔥 ndk-r25c", callback_data="ndk_r25c")],
        [InlineKeyboardButton("⚙️ ndk-r21e", callback_data="ndk_r21e")],
        [InlineKeyboardButton("🛠️ ndk-r16b", callback_data="ndk_r16b")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    """Создать админ клавиатуру"""
    logger.debug("🔘 Создание админ клавиатуры")
    keyboard = [
        [InlineKeyboardButton("📋 Открыть заказы", callback_data="admin_orders")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ===================== КОМАНДЫ ПОЛЬЗОВАТЕЛЯ =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start - ГЛАВНАЯ КОМАНДА"""
    user = update.effective_user
    logger.info("=" * 80)
    logger.info(f"🔵 КОМАНДА /start получена")
    logger.info(f"   👤 user_id: {user.id}")
    logger.info(f"   📛 username: {user.username}")
    logger.info(f"   📝 first_name: {user.first_name}")
    logger.info(f"   🕐 время: {datetime.now()}")
    logger.info("=" * 80)
    
    try:
        logger.debug(f"➕ Добавление пользователя {user.id} в БД")
        db.add_user(user.id, user.username or "Unknown")
        
        logger.debug(f"⏰ Проверка лимита пользователя {user.id}")
        used_today = db.check_and_reset_limit(user.id)
        logger.debug(f"✅ Лимит проверен: {used_today}/5")
        
        # Формируем сообщение
        logger.debug("📝 Формирование сообщения /start")
        start_message = f"""Привет! 😎

Ты в лучшем компиляторе JNI от rollsrug. 🔥

━━━━━━━━━━━━━━━━━━━━

📊 **Лимиты компиляций**

🔹 Статус: {'✅ Доступно' if used_today < DAILY_LIMIT else '❌ Лимит исчерпан'}
🔹 Использовано: {used_today}/5️⃣

⏰ Следующий сброс: 12:00 МСК

━━━━━━━━━━━━━━━━━━━━

📢 Пожалуйста подпишись на канал: {CHANNEL_USERNAME}

Чтобы бот корректно работал, от тебя нужен всего один архив 📦 со всеми папками твоего JNI.

✅ Поддерживаемые форматы: .zip, .7z
✅ Архив должен быть уже настроен под твой IP 🌐

Просто нажми кнопку «Компилировать JNI» 🚀, выбери NDK, отправь готовый архив — и мы приступим к компиляции! ⚙️"""
        
        logger.debug("📤 Отправка сообщения пользователю")
        await update.message.reply_text(
            start_message,
            reply_markup=get_start_keyboard(),
            parse_mode="Markdown"
        )
        
        logger.info(f"✅ Ответ на /start отправлен пользователю {user.id}")
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА в команде /start: {type(e).__name__}: {e}", exc_info=True)
        try:
            await update.message.reply_text(
                f"❌ Произошла ошибка: {str(e)}\n\nПожалуйста напишите админу @rollsrug"
            )
        except Exception as e2:
            logger.error(f"❌ Не удалось отправить сообщение об ошибке: {e2}")

async def compile_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка кнопки 'Компилировать JNI'"""
    query = update.callback_query
    user = query.from_user
    
    logger.info("=" * 80)
    logger.info(f"🟢 КНОПКА 'Компилировать JNI' нажата")
    logger.info(f"   👤 user_id: {user.id}")
    logger.info(f"   📛 username: {user.username}")
    logger.info("=" * 80)
    
    try:
        logger.debug(f"⏰ Проверка лимита для {user.id}")
        used_today = db.check_and_reset_limit(user.id)
        logger.debug(f"   Использовано: {used_today}/5")
        
        if used_today >= DAILY_LIMIT:
            logger.warning(f"⚠️ Лимит достигнут для пользователя {user.id}")
            await query.answer(
                "❌ Вы достигли лимита, следующее обновление в 12:00 МСК",
                show_alert=True
            )
            return ConversationHandler.END
        
        logger.debug(f"✅ Лимит не превышен, показываю выбор NDK")
        await query.edit_message_text(
            "Теперь выбери, какой NDK подходит для твоего JNI: 🤔\n\n"
            "🔹 🔥 Новый движок — ndk-r25c\n"
            "🔹 ⚙️ Средний вариант — ndk-r21e\n"
            "🔹 🛠️ Старый движок — ndk-r16b\n\n"
            "Выбирай тот вариант, под который писался твой JNI. ✅",
            reply_markup=get_ndk_keyboard()
        )
        
        logger.info(f"✅ Меню выбора NDK показано пользователю {user.id}")
        return CHOOSE_NDK
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА в compile_button: {type(e).__name__}: {e}", exc_info=True)
        await query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
        return ConversationHandler.END

async def ndk_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора NDK"""
    query = update.callback_query
    user = query.from_user
    
    logger.info(f"🟢 Выбор NDK: {query.data}")
    
    try:
        ndk_map = {
            'ndk_r25c': 'ndk-r25c',
            'ndk_r21e': 'ndk-r21e',
            'ndk_r16b': 'ndk-r16b'
        }
        
        ndk_version = ndk_map.get(query.data)
        if not ndk_version:
            logger.warning(f"⚠️ Неизвестная версия NDK: {query.data}")
            await query.answer("❌ Неизвестная версия NDK", show_alert=True)
            return ConversationHandler.END
        
        logger.info(f"✅ NDK выбран: {ndk_version}")
        context.user_data['ndk_version'] = ndk_version
        
        await query.edit_message_text(
            f"✅ Выбран: {ndk_version}\n\n"
            "Теперь отправь архив с твоим JNI проектом 📦\n\n"
            "📋 Требования:\n"
            "🔹 Формат: .zip или .7z\n"
            "🔹 Размер: до 140 МБ\n"
            "🔹 Архив уже настроен под твой IP 🌐\n\n"
            "Отправь файл одним сообщением ⬇️"
        )
        
        logger.info(f"✅ Запрос на загрузку архива отправлен {user.id}")
        return UPLOAD_FILE
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА в ndk_chosen: {type(e).__name__}: {e}", exc_info=True)
        await query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
        return ConversationHandler.END

async def file_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка загрузки файла"""
    user = update.effective_user
    
    logger.info("=" * 80)
    logger.info(f"📁 ФАЙЛ ЗАГРУЖЕН")
    logger.info(f"   👤 user_id: {user.id}")
    logger.info("=" * 80)
    
    try:
        if not update.message.document:
            logger.warning(f"⚠️ Получено не документ")
            await update.message.reply_text("❌ Это не файл. Пожалуйста, отправь архив (.zip или .7z)")
            return UPLOAD_FILE
        
        document = update.message.document
        filename = document.file_name
        file_size = document.file_size
        
        logger.debug(f"📄 Файл: {filename}")
        logger.debug(f"📊 Размер: {file_size / 1024 / 1024:.1f} МБ")
        
        # Проверяем расширение
        if not filename.lower().endswith(('.zip', '.7z')):
            logger.warning(f"❌ Неподдерживаемый формат: {filename}")
            await update.message.reply_text(
                "❌ Неподдерживаемый формат!\n\nИспользуй только: .zip или .7z"
            )
            return UPLOAD_FILE
        
        # Проверяем размер
        if file_size > MAX_FILE_SIZE:
            logger.warning(f"❌ Файл слишком большой: {file_size / 1024 / 1024:.1f} МБ")
            await update.message.reply_text(
                "❌ Архив слишком большой!\n\nМаксимум: 140 МБ"
            )
            return UPLOAD_FILE
        
        logger.debug(f"✅ Файл прошел проверку")
        
        # Проверяем лимит еще раз
        logger.debug(f"⏰ Финальная проверка лимита")
        used_today = db.check_and_reset_limit(user.id)
        if used_today >= DAILY_LIMIT:
            logger.warning(f"❌ Лимит превышен при загрузке файла")
            await update.message.reply_text("❌ Вы достигли лимита, следующее обновление в 12:00 МСК")
            return ConversationHandler.END
        
        # Создаем заказ
        logger.debug(f"🆕 Создание заказа")
        ndk_version = context.user_data.get('ndk_version', 'unknown')
        order_id = db.create_order(
            user.id,
            user.username or "Unknown",
            ndk_version,
            document.file_id,
            filename
        )
        
        # Увеличиваем счетчик
        logger.debug(f"📈 Увеличение счетчика использования")
        db.increment_usage(user.id)
        
        logger.info(f"✅ Заказ #{order_id} создан")
        
        # Подтверждение
        await update.message.reply_text(
            f"✅ Архив принят! Ожидайте компиляцию, обычно это занимает до 30 минут\n\n"
            f"📋 Номер заказа: #{order_id}\n"
            f"🛠️ NDK версия: {ndk_version}\n\n"
            f"Мы пришлём результат как только компиляция завершится! 🚀"
        )
        
        # Уведомляем админа
        logger.debug(f"📧 Отправка уведомления админу")
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"🆕 Новый заказ!\n\n"
                f"👤 Пользователь: @{user.username or user.id}\n"
                f"📋 ID заказа: {order_id}\n"
                f"🛠️ NDK: {ndk_version}\n"
                f"📦 Файл: {filename}\n"
                f"📊 Размер: {file_size / 1024 / 1024:.1f} МБ\n\n"
                f"Откройте админку: /admin"
            )
            logger.info(f"✅ Уведомление админу отправлено")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось отправить уведомление админу: {e}")
        
        logger.info(f"✅ Файл обработан полностью")
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА в file_received: {type(e).__name__}: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка при загрузке файла: {str(e)}")
        return UPLOAD_FILE

# ===================== АДМИН ФУНКЦИИ =====================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /admin"""
    user = update.effective_user
    
    logger.info("=" * 80)
    logger.info(f"🟣 КОМАНДА /admin")
    logger.info(f"   👤 user_id: {user.id}")
    logger.info(f"   🔐 ADMIN_ID: {ADMIN_ID}")
    logger.info("=" * 80)
    
    if user.id != ADMIN_ID:
        logger.warning(f"❌ Попытка доступа к админке от {user.id} (не админ)")
        await update.message.reply_text("❌ У тебя нет доступа к админ панели")
        return
    
    logger.info(f"✅ Доступ к админке разрешен для {user.id}")
    
    try:
        all_orders = db.get_all_orders()
        waiting = [o for o in all_orders if o[7] == 'waiting']
        done = [o for o in all_orders if o[7] == 'done']
        rejected = [o for o in all_orders if o[7] == 'rejected']
        
        logger.debug(f"📊 Заказы: всего={len(all_orders)}, в очереди={len(waiting)}, готово={len(done)}, отклонено={len(rejected)}")
        
        admin_text = (
            "👑 Добро пожаловать в админ панель!\n\n"
            f"📊 Статистика:\n"
            f"• Всего заказов: {len(all_orders)}\n"
            f"• В очереди: {len(waiting)}\n"
            f"• Готово: {len(done)}\n"
            f"• Отклонено: {len(rejected)}\n\n"
            "Здесь ты можешь управлять заказами на компиляцию."
        )
        
        await update.message.reply_text(
            admin_text,
            reply_markup=get_admin_keyboard()
        )
        
        logger.info(f"✅ Админ панель показана")
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА в admin_command: {type(e).__name__}: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать очередь заказов"""
    query = update.callback_query
    
    logger.info(f"🟣 Админ: показать заказы")
    
    try:
        all_orders = db.get_all_orders()
        
        if not all_orders:
            logger.debug("ℹ️ Заказов не найдено")
            await query.edit_message_text("📭 Нет заказов")
            return
        
        orders_text = "📋 **Все заказы:**\n\n"
        for order in all_orders[:10]:  # Показываем первые 10
            order_id = order[0]
            username = order[2]
            ndk = order[3]
            status = order[7]
            
            status_emoji = {"waiting": "⏳", "done": "✅", "rejected": "❌"}.get(status, "❓")
            orders_text += f"{status_emoji} #{order_id} | @{username} | {ndk}\n"
        
        logger.debug(f"✅ Список заказов сформирован")
        await query.edit_message_text(orders_text, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА в admin_orders: {type(e).__name__}: {e}", exc_info=True)
        await query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Статистика"""
    query = update.callback_query
    
    logger.info(f"🟣 Админ: статистика")
    
    try:
        all_orders = db.get_all_orders()
        waiting = len([o for o in all_orders if o[7] == 'waiting'])
        done = len([o for o in all_orders if o[7] == 'done'])
        rejected = len([o for o in all_orders if o[7] == 'rejected'])
        
        stats_text = (
            "📊 **СТАТИСТИКА:**\n\n"
            f"📈 Всего заказов: {len(all_orders)}\n"
            f"⏳ В очереди: {waiting}\n"
            f"✅ Готово: {done}\n"
            f"❌ Отклонено: {rejected}"
        )
        
        logger.debug(f"✅ Статистика показана")
        await query.edit_message_text(stats_text, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА в admin_stats: {type(e).__name__}: {e}", exc_info=True)
        await query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

# ===================== ОБРАБОТЧИК ОШИБОК =====================

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик ошибок"""
    logger.error(f"🔴 КРИТИЧЕСКАЯ ОШИБКА: {context.error}", exc_info=True)
    
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "🔴 Произошла критическая ошибка. Пожалуйста напишите админу @rollsrug"
            )
        except Exception as e:
            logger.error(f"❌ Не удалось отправить сообщение об ошибке: {e}")

# ===================== ПЛАНИРОВЩИК =====================

async def reset_daily_limits(app: Application) -> None:
    """Сброс дневных лимитов"""
    logger.info("=" * 80)
    logger.info("🔄 АВТОМАТИЧЕСКИЙ СБРОС ЛИМИТОВ")
    logger.info("=" * 80)
    
    try:
        logger.debug("📊 Получение всех пользователей")
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('UPDATE users SET used_today = 0, last_reset = ?', (datetime.now().isoformat(),))
        
        updated = cursor.rowcount
        conn.commit()
        conn.close()
        
        logger.info(f"✅ Лимиты сброшены для {updated} пользователей")
        
        # Уведомляем админа
        try:
            await app.bot.send_message(
                ADMIN_ID,
                f"🔄 Ежедневный сброс лимитов выполнен\n\n"
                f"🔹 Пользователей обновлено: {updated}\n"
                f"🕐 Время: {datetime.now().strftime('%H:%M:%S')}"
            )
        except Exception as e:
            logger.warning(f"⚠️ Не удалось отправить уведомление админу: {e}")
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА при сбросе лимитов: {type(e).__name__}: {e}", exc_info=True)

# ===================== MAIN =====================

def main():
    """Запуск бота"""
    logger.info("=" * 80)
    logger.info("🚀 ЗАПУСК БОТА")
    logger.info("=" * 80)
    logger.info(f"📌 BOT_TOKEN: {BOT_TOKEN[:20]}...")
    logger.info(f"👤 ADMIN_ID: {ADMIN_ID}")
    logger.info(f"📢 CHANNEL: {CHANNEL_USERNAME}")
    logger.info("=" * 80)
    
    try:
        # Создаем приложение
        logger.debug("🔧 Создание Application")
        app = Application.builder().token(BOT_TOKEN).build()
        logger.debug("✅ Application создан")
        
        # ConversationHandler
        logger.debug("🔧 Создание ConversationHandler")
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
        logger.debug("✅ ConversationHandler создан")
        
        # Добавляем обработчики
        logger.debug("🔧 Добавление обработчиков")
        app.add_handler(conv_handler)
        app.add_handler(CommandHandler("admin", admin_command))
        app.add_handler(CallbackQueryHandler(admin_orders, pattern="^admin_orders$"))
        app.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
        app.add_error_handler(error_handler)
        logger.debug("✅ Все обработчики добавлены")
        
        # Планировщик
        logger.debug("🔧 Создание планировщика")
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            reset_daily_limits,
            CronTrigger(hour=RESET_HOUR, minute=RESET_MINUTE, timezone='Europe/Moscow'),
            args=[app],
            name='reset_limits'
        )
        scheduler.start()
        logger.info(f"✅ Планировщик запущен (сброс в {RESET_HOUR}:{RESET_MINUTE:02d} МСК)")
        
        logger.info("=" * 80)
        logger.info("🚀 БОТ ГОТОВ К ЗАПУСКУ POLLING")
        logger.info("=" * 80)
        
        # Запускаем
        app.run_polling(allowed_updates=["UPDATE", "MESSAGE", "CALLBACK_QUERY"])
        
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПУСКЕ: {type(e).__name__}: {e}", exc_info=True)
        raise

if __name__ == '__main__':
    logger.info("=" * 80)
    logger.info("🚀 ТОЧКА ВХОДА ПРОГРАММЫ")
    logger.info("=" * 80)
    main()
