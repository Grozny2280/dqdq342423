import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional
import pytz
import re

from aiogram import Bot, types
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode

from config import ADMIN_IDS, SUPERADMIN_IDS, PVZ_ADDRESS, GROUP_CHAT_ID, TIMEZONE
import database as db

# Состояния пользователей
user_states: Dict[int, str] = {}
user_data: Dict[int, Dict] = {}

TZ = pytz.timezone(TIMEZONE)

# Для предотвращения дублирования уведомлений
_last_notification = {}


def now_msk() -> datetime:
    return datetime.now(TZ)


def fmt_datetime(dt_str: str) -> str:
    """Форматирование даты и времени"""
    dt = datetime.fromisoformat(dt_str)
    return dt.strftime("%d.%m.%Y %H:%M")


def fmt_duration(minutes: int) -> str:
    """Форматирование длительности"""
    if minutes is None:
        return "0ч 0м"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}ч {mins}м"


async def notify_admins(bot: Bot, message: str, photo_id: str = None, notification_key: str = None):
    """Уведомление всех администраторов без дублирования"""
    if notification_key:
        current_time = datetime.now().timestamp()
        if notification_key in _last_notification:
            if current_time - _last_notification[notification_key] < 2:
                return
        _last_notification[notification_key] = current_time
    
    # Получаем уникальных админов
    all_admin_ids = list(set(ADMIN_IDS + SUPERADMIN_IDS))
    
    for admin_id in all_admin_ids:
        try:
            if photo_id:
                await bot.send_photo(admin_id, photo_id, caption=message)
            else:
                await bot.send_message(admin_id, message)
        except Exception as e:
            print(f"Не удалось отправить уведомление админу {admin_id}: {e}")
    
    if GROUP_CHAT_ID:
        try:
            if photo_id:
                await bot.send_photo(GROUP_CHAT_ID, photo_id, caption=message)
            else:
                await bot.send_message(GROUP_CHAT_ID, message)
        except Exception as e:
            print(f"Не удалось отправить уведомление в группу: {e}")


# ============= КЛАВИАТУРЫ =============

async def get_main_keyboard(telegram_id: int, bot: Bot = None) -> ReplyKeyboardMarkup:
    """Главная клавиатура (обновляется в зависимости от статуса)"""
    is_superadmin = telegram_id in SUPERADMIN_IDS
    is_admin = telegram_id in ADMIN_IDS or is_superadmin
    
    keyboard = []
    
    # Проверяем, является ли пользователь сотрудником (одобрен)
    is_approved_employee = await db.is_approved(telegram_id)
    
    if is_approved_employee:
        # ПОЛЬЗОВАТЕЛЬ - СОТРУДНИК (включая админов и суперадминов как сотрудников)
        active_shift = await db.get_active_shift(telegram_id)
        active_break = await db.get_active_break(telegram_id)
        
        # Кнопки для работы со сменой
        if active_shift:
            if active_break:
                keyboard.append([KeyboardButton(text="✅ Завершить перерыв")])
                keyboard.append([KeyboardButton(text="❌ Закрыть смену")])
            else:
                keyboard.append([KeyboardButton(text="☕ Начать перерыв")])
                keyboard.append([KeyboardButton(text="❌ Закрыть смену")])
        else:
            keyboard.append([KeyboardButton(text="✅ Открыть смену")])
        
        # Кнопка статистики для всех сотрудников
        keyboard.append([KeyboardButton(text="📈 Моя статистика")])
    
    # Админские кнопки (добавляются отдельно, если пользователь админ)
    if is_admin:
        if is_approved_employee:
            # Разделитель, если есть кнопки сотрудника
            if keyboard:
                keyboard.append([KeyboardButton(text="─" * 20)])
        
        keyboard.append([KeyboardButton(text="📊 Статистика сотрудника")])
        keyboard.append([KeyboardButton(text="👥 Активные смены")])
        keyboard.append([KeyboardButton(text="📋 Все сотрудники")])
        
        if is_superadmin:
            keyboard.append([KeyboardButton(text="⚙️ Суперадмин панель")])
    
    # Если пользователь не одобрен и не админ
    if not is_approved_employee and not is_admin:
        keyboard = [[KeyboardButton(text="⏳ Ожидание подтверждения")]]
    
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_superadmin_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура суперадмина"""
    keyboard = [
        [KeyboardButton(text="👥 Все сотрудники (включая неодобренных)")],
        [KeyboardButton(text="✅ Одобрить сотрудников")],
        [KeyboardButton(text="✏️ Редактировать сотрудника")],
        [KeyboardButton(text="📅 Редактировать смену")],
        [KeyboardButton(text="📊 Недельный отчёт")],
        [KeyboardButton(text="◀️ Назад")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


async def update_user_keyboard(message: Message, bot: Bot):
    """Обновление клавиатуры у пользователя"""
    telegram_id = message.from_user.id
    keyboard = await get_main_keyboard(telegram_id, bot)
    await message.answer("🔄 Меню обновлено", reply_markup=keyboard)


# ============= ОСНОВНЫЕ ОБРАБОТЧИКИ =============

async def handle_start(message: Message, bot: Bot):
    """Обработчик команды /start"""
    telegram_id = message.from_user.id
    
    if not await db.is_employee_exists(telegram_id):
        user_states[telegram_id] = "register_waiting"
        await message.answer(
            "👋 Добро пожаловать!\n\n"
            "Пожалуйста, пройдите регистрацию.\n"
            "Введите ваше ФИО:"
        )
    elif not await db.is_approved(telegram_id):
        # Проверяем, является ли пользователь админом (админы автоматически одобрены)
        is_admin = telegram_id in ADMIN_IDS or telegram_id in SUPERADMIN_IDS
        if is_admin:
            # Автоматически одобряем админов
            await db.approve_employee(telegram_id)
            await message.answer(
                "✅ Вы авторизованы как администратор!\n"
                "Ваша учетная запись автоматически подтверждена."
            )
            await handle_start(message, bot)
            return
        else:
            await message.answer(
                "⏳ Ваша регистрация ожидает подтверждения администратором.\n"
                "Пожалуйста, ожидайте."
            )
            return
    else:
        employee = await db.get_employee(telegram_id)
        active_shift = await db.get_active_shift(telegram_id)
        active_break = await db.get_active_break(telegram_id)
        
        keyboard = await get_main_keyboard(telegram_id, bot)
        
        status_text = f"✅ Добро пожаловать, {employee['full_name']}!\n"
        
        # Определяем роль
        if telegram_id in SUPERADMIN_IDS:
            status_text += f"👑 Роль: Суперадминистратор\n"
        elif telegram_id in ADMIN_IDS:
            status_text += f"👤 Роль: Администратор\n"
        else:
            status_text += f"👤 Роль: Сотрудник\n"
        
        if active_shift:
            opened_at = fmt_datetime(active_shift["opened_at"])
            status_text += f"\n🟢 Смена открыта с {opened_at}"
            if active_break:
                status_text += f"\n🔴 Вы на перерыве с {fmt_datetime(active_break['started_at'])}"
        else:
            status_text += f"\n⚪ Смена закрыта"
        
        await message.answer(status_text, reply_markup=keyboard)


async def handle_register(message: Message, bot: Bot):
    """Обработка регистрации"""
    telegram_id = message.from_user.id
    
    if telegram_id not in user_states or user_states[telegram_id] != "register_waiting":
        return False
    
    if "full_name" not in user_data.get(telegram_id, {}):
        user_data[telegram_id] = {"full_name": message.text}
        await message.answer("Введите ваш ID сотрудника WB:")
        return True
    
    elif "wb_id" not in user_data[telegram_id]:
        user_data[telegram_id]["wb_id"] = message.text
        
        success = await db.register_employee(
            telegram_id,
            user_data[telegram_id]["full_name"],
            user_data[telegram_id]["wb_id"]
        )
        
        if success:
            # Проверяем, является ли пользователь админом (автоодобрение)
            is_admin = telegram_id in ADMIN_IDS or telegram_id in SUPERADMIN_IDS
            
            if is_admin:
                await db.approve_employee(telegram_id)
                await message.answer(
                    "✅ Регистрация успешно завершена!\n"
                    "Вы авторизованы как администратор, учётная запись автоматически подтверждена.\n"
                    "Теперь вы можете начать работу."
                )
            else:
                await message.answer(
                    "✅ Регистрация успешно завершена!\n"
                    "Ваша заявка отправлена администратору на подтверждение.\n"
                    "После подтверждения вы сможете начать работу."
                )
                
                # Отправляем уведомление суперадминам только для обычных сотрудников
                employee_name = user_data[telegram_id]["full_name"]
                employee_wb_id = user_data[telegram_id]["wb_id"]
                
                for superadmin_id in SUPERADMIN_IDS:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{telegram_id}")]
                    ])
                    await bot.send_message(
                        superadmin_id,
                        f"🆕 Новая заявка на регистрацию!\n"
                        f"Пользователь: {employee_name}\n"
                        f"WB ID: {employee_wb_id}\n"
                        f"Telegram ID: {telegram_id}",
                        reply_markup=keyboard
                    )
        else:
            await message.answer("❌ Ошибка регистрации. Возможно, вы уже зарегистрированы.")
        
        del user_states[telegram_id]
        del user_data[telegram_id]
        return True
    
    return False


async def handle_open_shift(message: Message, bot: Bot):
    """Открытие смены с фото"""
    telegram_id = message.from_user.id
    
    if not await db.is_approved(telegram_id):
        await message.answer("❌ Ваша учетная запись не подтверждена.")
        return
    
    active_shift = await db.get_active_shift(telegram_id)
    if active_shift:
        await message.answer("❌ У вас уже открыта смена! Сначала закройте её.")
        return
    
    await message.answer(
        f"📸 Пожалуйста, отправьте фото ПВЗ ({PVZ_ADDRESS})\n"
        "Фото необходимо для подтверждения начала смены."
    )
    user_states[telegram_id] = "waiting_shift_photo"


async def handle_shift_photo(message: Message, bot: Bot):
    """Обработка фото для открытия смены"""
    telegram_id = message.from_user.id
    
    if not message.photo:
        await message.answer("❌ Пожалуйста, отправьте фото.")
        return
    
    photo_id = message.photo[-1].file_id
    
    shift_id = await db.open_shift(telegram_id, photo_id)
    if shift_id:
        open_time = now_msk()
        await message.answer(
            f"✅ Смена открыта!\n"
            f"Время начала: {fmt_datetime(open_time.isoformat())}\n"
            f"ПВЗ: {PVZ_ADDRESS}"
        )
        
        # Отправляем уведомление админам (исключая самого пользователя, если он админ)
        employee = await db.get_employee(telegram_id)
        
        # Не отправляем уведомление самому себе, если он админ
        is_admin = telegram_id in ADMIN_IDS or telegram_id in SUPERADMIN_IDS
        if not is_admin:
            await notify_admins(
                bot, 
                f"🟢 СОТРУДНИК ОТКРЫЛ СМЕНУ\n"
                f"👤 {employee['full_name']}\n"
                f"🆔 WB ID: {employee['wb_employee_id']}\n"
                f"🕐 Время: {fmt_datetime(open_time.isoformat())}\n"
                f"📍 {PVZ_ADDRESS}",
                photo_id,
                notification_key=f"shift_open_{telegram_id}_{open_time.timestamp()}"
            )
        else:
            # Для админов просто логгируем в консоль или отправляем в группу
            if GROUP_CHAT_ID:
                await bot.send_message(
                    GROUP_CHAT_ID,
                    f"🟢 АДМИНИСТРАТОР {employee['full_name']} открыл смену\n"
                    f"🕐 Время: {fmt_datetime(open_time.isoformat())}"
                )
        
        # Обновляем клавиатуру
        await update_user_keyboard(message, bot)
    else:
        await message.answer("❌ Ошибка открытия смены.")
    
    if telegram_id in user_states:
        del user_states[telegram_id]


async def handle_close_shift(message: Message, bot: Bot):
    """Закрытие смены"""
    telegram_id = message.from_user.id
    
    if not await db.is_approved(telegram_id):
        await message.answer("❌ Ваша учетная запись не подтверждена.")
        return
    
    active_shift = await db.get_active_shift(telegram_id)
    if not active_shift:
        await message.answer("❌ У вас нет открытой смены.")
        return
    
    active_break = await db.get_active_break(telegram_id)
    if active_break:
        await message.answer("❌ Вы находитесь на перерыве. Сначала завершите перерыв.")
        return
    
    duration = await db.close_shift(telegram_id)
    if duration:
        close_time = now_msk()
        await message.answer(
            f"✅ Смена закрыта!\n"
            f"Время закрытия: {fmt_datetime(close_time.isoformat())}\n"
            f"Длительность: {fmt_duration(duration)}"
        )
        
        # Уведомляем админов
        employee = await db.get_employee(telegram_id)
        is_admin = telegram_id in ADMIN_IDS or telegram_id in SUPERADMIN_IDS
        
        if not is_admin:
            await notify_admins(
                bot,
                f"🔴 СОТРУДНИК ЗАКРЫЛ СМЕНУ\n"
                f"👤 {employee['full_name']}\n"
                f"🆔 WB ID: {employee['wb_employee_id']}\n"
                f"🕐 Время закрытия: {fmt_datetime(close_time.isoformat())}\n"
                f"⏱️ Длительность: {fmt_duration(duration)}",
                notification_key=f"shift_close_{telegram_id}_{close_time.timestamp()}"
            )
        else:
            if GROUP_CHAT_ID:
                await bot.send_message(
                    GROUP_CHAT_ID,
                    f"🔴 АДМИНИСТРАТОР {employee['full_name']} закрыл смену\n"
                    f"🕐 Время: {fmt_datetime(close_time.isoformat())}\n"
                    f"⏱️ Длительность: {fmt_duration(duration)}"
                )
        
        # Обновляем клавиатуру
        await update_user_keyboard(message, bot)
    else:
        await message.answer("❌ Ошибка закрытия смены.")


async def handle_break_start(message: Message, bot: Bot):
    """Начало перерыва с фото"""
    telegram_id = message.from_user.id
    
    if not await db.is_approved(telegram_id):
        await message.answer("❌ Ваша учетная запись не подтверждена.")
        return
    
    active_shift = await db.get_active_shift(telegram_id)
    if not active_shift:
        await message.answer("❌ У вас нет открытой смены.")
        return
    
    active_break = await db.get_active_break(telegram_id)
    if active_break:
        await message.answer("❌ Вы уже на перерыве!")
        return
    
    await message.answer("📸 Отправьте фото для подтверждения начала перерыва:")
    user_states[telegram_id] = "waiting_break_photo"


async def handle_break_photo(message: Message, bot: Bot):
    """Обработка фото для начала перерыва"""
    telegram_id = message.from_user.id
    
    if not message.photo:
        await message.answer("❌ Пожалуйста, отправьте фото.")
        return
    
    photo_id = message.photo[-1].file_id
    
    break_id = await db.start_break(telegram_id, photo_id)
    if break_id:
        break_start_time = now_msk()
        await message.answer(
            f"☕ Перерыв начался!\n"
            f"Время начала: {fmt_datetime(break_start_time.isoformat())}"
        )
        
        employee = await db.get_employee(telegram_id)
        is_admin = telegram_id in ADMIN_IDS or telegram_id in SUPERADMIN_IDS
        
        if not is_admin:
            await notify_admins(
                bot,
                f"☕ СОТРУДНИК НАЧАЛ ПЕРЕРЫВ\n"
                f"👤 {employee['full_name']}\n"
                f"🆔 WB ID: {employee['wb_employee_id']}\n"
                f"🕐 Время: {fmt_datetime(break_start_time.isoformat())}",
                photo_id,
                notification_key=f"break_start_{telegram_id}_{break_start_time.timestamp()}"
            )
        else:
            if GROUP_CHAT_ID:
                await bot.send_message(
                    GROUP_CHAT_ID,
                    f"☕ АДМИНИСТРАТОР {employee['full_name']} начал перерыв\n"
                    f"🕐 Время: {fmt_datetime(break_start_time.isoformat())}"
                )
        
        # Обновляем клавиатуру
        await update_user_keyboard(message, bot)
        
        # Запускаем таймер на 15 минут
        asyncio.create_task(check_break_duration(telegram_id, bot, break_id, break_start_time))
    else:
        await message.answer("❌ Ошибка начала перерыва.")
    
    if telegram_id in user_states:
        del user_states[telegram_id]


async def check_break_duration(telegram_id: int, bot: Bot, break_id: int, break_start_time: datetime):
    """Проверка длительности перерыва"""
    await asyncio.sleep(15 * 60)
    
    break_obj = await db.get_active_break(telegram_id)
    if break_obj and break_obj["id"] == break_id:
        employee = await db.get_employee(telegram_id)
        current_duration = int((now_msk() - break_start_time).total_seconds() / 60)
        
        is_admin = telegram_id in ADMIN_IDS or telegram_id in SUPERADMIN_IDS
        
        if not is_admin:
            await notify_admins(
                bot,
                f"⚠️ ВНИМАНИЕ! ПЕРЕРЫВ БОЛЕЕ 15 МИНУТ\n"
                f"👤 {employee['full_name']}\n"
                f"🆔 WB ID: {employee['wb_employee_id']}\n"
                f"🕐 Начало перерыва: {fmt_datetime(break_start_time.isoformat())}\n"
                f"⏱️ Длительность: {fmt_duration(current_duration)}"
            )
        else:
            if GROUP_CHAT_ID:
                await bot.send_message(
                    GROUP_CHAT_ID,
                    f"⚠️ ВНИМАНИЕ! АДМИНИСТРАТОР {employee['full_name']} на перерыве более 15 минут!\n"
                    f"⏱️ Длительность: {fmt_duration(current_duration)}"
                )


async def handle_break_end(message: Message, bot: Bot):
    """Завершение перерыва"""
    telegram_id = message.from_user.id
    
    if not await db.is_approved(telegram_id):
        await message.answer("❌ Ваша учетная запись не подтверждена.")
        return
    
    active_break = await db.get_active_break(telegram_id)
    if not active_break:
        await message.answer("❌ У вас нет активного перерыва.")
        return
    
    duration = await db.end_break(telegram_id)
    if duration is not None:
        end_time = now_msk()
        await message.answer(
            f"✅ Перерыв завершён!\n"
            f"Время завершения: {fmt_datetime(end_time.isoformat())}\n"
            f"Длительность: {fmt_duration(duration)}"
        )
        
        employee = await db.get_employee(telegram_id)
        is_admin = telegram_id in ADMIN_IDS or telegram_id in SUPERADMIN_IDS
        
        if not is_admin:
            await notify_admins(
                bot,
                f"✅ СОТРУДНИК ЗАВЕРШИЛ ПЕРЕРЫВ\n"
                f"👤 {employee['full_name']}\n"
                f"🆔 WB ID: {employee['wb_employee_id']}\n"
                f"🕐 Время завершения: {fmt_datetime(end_time.isoformat())}\n"
                f"⏱️ Длительность: {fmt_duration(duration)}",
                notification_key=f"break_end_{telegram_id}_{end_time.timestamp()}"
            )
        else:
            if GROUP_CHAT_ID:
                await bot.send_message(
                    GROUP_CHAT_ID,
                    f"✅ АДМИНИСТРАТОР {employee['full_name']} завершил перерыв\n"
                    f"⏱️ Длительность: {fmt_duration(duration)}"
                )
        
        # Обновляем клавиатуру
        await update_user_keyboard(message, bot)
    else:
        await message.answer("❌ Ошибка завершения перерыва.")


# ... остальные функции (handle_my_stats, handle_active_shifts и т.д.) остаются без изменений

async def handle_my_stats(message: Message):
    """Просмотр своей статистики"""
    telegram_id = message.from_user.id
    
    if not await db.is_approved(telegram_id):
        await message.answer("❌ Ваша учетная запись не подтверждена.")
        return
    
    stats = await db.get_week_stats(telegram_id)
    employee = await db.get_employee(telegram_id)
    
    text = (
        f"📊 Статистика за неделю для {employee['full_name']}:\n\n"
        f"📅 Количество смен: {stats['shifts_count']}\n"
        f"⏱️ Отработанные часы: {stats['total_hours']} ч\n"
        f"☕ Количество перерывов: {stats['breaks_count']}\n"
        f"⏰ Время перерывов: {stats['total_breaks_hours']} ч"
    )
    
    await message.answer(text)


async def handle_active_shifts(message: Message):
    """Просмотр активных смен (для админов)"""
    telegram_id = message.from_user.id
    
    if telegram_id not in ADMIN_IDS and telegram_id not in SUPERADMIN_IDS:
        await message.answer("❌ Нет доступа.")
        return
    
    active_employees = await db.get_active_employees()
    
    if not active_employees:
        await message.answer("📭 Нет активных смен.")
        return
    
    text = "👥 Активные смены:\n\n"
    for emp in active_employees:
        status = "🔴 НА ПЕРЕРЫВЕ" if emp["on_break"] else "🟢 РАБОТАЕТ"
        # Добавляем пометку, если это администратор
        is_admin_employee = emp["telegram_id"] in ADMIN_IDS or emp["telegram_id"] in SUPERADMIN_IDS
        admin_mark = " [АДМИН]" if is_admin_employee else ""
        
        text += (
            f"👤 {emp['full_name']}{admin_mark}\n"
            f"   {status}\n"
            f"   ⏱️ Работает: {fmt_duration(emp['duration_minutes'])}\n"
            f"   🕐 Начало: {fmt_datetime(emp['opened_at'])}\n"
        )
        if emp["break_started"]:
            break_duration = int((now_msk() - datetime.fromisoformat(emp["break_started"])).total_seconds() / 60)
            text += f"   ☕ Перерыв: {fmt_duration(break_duration)}\n"
        text += "\n"
    
    await message.answer(text)


# Остальные функции (handle_all_employees, handle_admin_stats, handle_superadmin_panel и т.д.)
# остаются без изменений из предыдущей версии

async def handle_all_employees(message: Message):
    """Список всех сотрудников (для админов)"""
    telegram_id = message.from_user.id
    
    if telegram_id not in ADMIN_IDS and telegram_id not in SUPERADMIN_IDS:
        await message.answer("❌ Нет доступа.")
        return
    
    employees = await db.get_all_employees(include_unapproved=False)
    
    if not employees:
        await message.answer("📭 Нет зарегистрированных сотрудников.")
        return
    
    text = "📋 Список сотрудников:\n\n"
    for emp in employees:
        # Добавляем пометку, если это администратор
        is_admin_employee = emp['telegram_id'] in ADMIN_IDS or emp['telegram_id'] in SUPERADMIN_IDS
        admin_mark = " [АДМИН]" if is_admin_employee else ""
        
        text += f"👤 {emp['full_name']}{admin_mark}\n"
        text += f"   ID: {emp['wb_employee_id']}\n"
        text += f"   Telegram: {emp['telegram_id']}\n"
        text += f"   Регистрация: {fmt_datetime(emp['registered_at'])}\n\n"
    
    await message.answer(text)


# ... остальные функции (handle_admin_stats, handle_superadmin_panel, 
# handle_all_employees_unapproved, handle_approve_employees, 
# handle_edit_employee, handle_edit_shift, handle_week_report,
# handle_callback_query, process_edit_input) остаются как в предыдущей версии

async def handle_admin_stats(message: Message):
    """Просмотр статистики любого сотрудника (для админов)"""
    telegram_id = message.from_user.id
    
    if telegram_id not in ADMIN_IDS and telegram_id not in SUPERADMIN_IDS:
        await message.answer("❌ Нет доступа.")
        return
    
    employees = await db.get_all_employees(include_unapproved=True)
    
    if not employees:
        await message.answer("📭 Нет сотрудников.")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{emp['full_name']} ({emp['wb_employee_id']}){' [АДМИН]' if emp['telegram_id'] in ADMIN_IDS or emp['telegram_id'] in SUPERADMIN_IDS else ''}", 
                              callback_data=f"admin_stats_{emp['telegram_id']}")]
        for emp in employees
    ])
    
    await message.answer("Выберите сотрудника:", reply_markup=keyboard)


async def handle_superadmin_panel(message: Message):
    """Суперадмин панель"""
    telegram_id = message.from_user.id
    
    if telegram_id not in SUPERADMIN_IDS:
        await message.answer("❌ Нет доступа.")
        return
    
    await message.answer("⚙️ Суперадмин панель", reply_markup=get_superadmin_keyboard())


async def handle_all_employees_unapproved(message: Message):
    """Все сотрудники (включая неодобренных)"""
    telegram_id = message.from_user.id
    
    if telegram_id not in SUPERADMIN_IDS:
        await message.answer("❌ Нет доступа.")
        return
    
    employees = await db.get_all_employees(include_unapproved=True)
    
    if not employees:
        await message.answer("📭 Нет зарегистрированных сотрудников.")
        return
    
    text = "📋 Все сотрудники (включая неодобренных):\n\n"
    for emp in employees:
        status = "✅ Одобрен" if emp['approved'] else "⏳ Не одобрен"
        is_admin_employee = emp['telegram_id'] in ADMIN_IDS or emp['telegram_id'] in SUPERADMIN_IDS
        admin_mark = " [АДМИН]" if is_admin_employee else ""
        
        text += f"👤 {emp['full_name']}{admin_mark}\n"
        text += f"   ID: {emp['wb_employee_id']}\n"
        text += f"   Telegram: {emp['telegram_id']}\n"
        text += f"   Статус: {status}\n"
        text += f"   Регистрация: {fmt_datetime(emp['registered_at'])}\n\n"
    
    await message.answer(text)


async def handle_approve_employees(message: Message):
    """Одобрение сотрудников"""
    telegram_id = message.from_user.id
    
    if telegram_id not in SUPERADMIN_IDS:
        await message.answer("❌ Нет доступа.")
        return
    
    unapproved = await db.get_unapproved_employees()
    # Фильтруем админов из списка неодобренных (они уже должны быть одобрены)
    unapproved = [emp for emp in unapproved if emp['telegram_id'] not in ADMIN_IDS and emp['telegram_id'] not in SUPERADMIN_IDS]
    
    if not unapproved:
        await message.answer("📭 Нет неодобренных сотрудников.")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{emp['full_name']} ({emp['wb_employee_id']})", 
                              callback_data=f"approve_{emp['telegram_id']}")]
        for emp in unapproved
    ])
    
    await message.answer("Выберите сотрудника для одобрения:", reply_markup=keyboard)


async def handle_edit_employee(message: Message):
    """Редактирование сотрудника"""
    telegram_id = message.from_user.id
    
    if telegram_id not in SUPERADMIN_IDS:
        await message.answer("❌ Нет доступа.")
        return
    
    employees = await db.get_all_employees(include_unapproved=True)
    
    if not employees:
        await message.answer("📭 Нет сотрудников.")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{emp['full_name']} ({emp['wb_employee_id']}){' [АДМИН]' if emp['telegram_id'] in ADMIN_IDS or emp['telegram_id'] in SUPERADMIN_IDS else ''}", 
                              callback_data=f"edit_emp_{emp['telegram_id']}")]
        for emp in employees
    ])
    
    await message.answer("Выберите сотрудника для редактирования:", reply_markup=keyboard)


async def handle_edit_shift(message: Message):
    """Редактирование смены"""
    telegram_id = message.from_user.id
    
    if telegram_id not in SUPERADMIN_IDS:
        await message.answer("❌ Нет доступа.")
        return
    
    employees = await db.get_all_employees(include_unapproved=False)
    
    if not employees:
        await message.answer("📭 Нет сотрудников.")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{emp['full_name']} ({emp['wb_employee_id']})", 
                              callback_data=f"edit_shift_list_{emp['telegram_id']}")]
        for emp in employees
    ])
    
    await message.answer("Выберите сотрудника, чью смену хотите отредактировать:", reply_markup=keyboard)


async def handle_week_report(message: Message, bot: Bot):
    """Недельный отчёт по всем сотрудникам"""
    telegram_id = message.from_user.id
    
    if telegram_id not in SUPERADMIN_IDS:
        await message.answer("❌ Нет доступа.")
        return
    
    all_stats = await db.get_all_week_stats()
    
    if not all_stats:
        await message.answer("📭 Нет данных за неделю.")
        return
    
    text = "📊 НЕДЕЛЬНЫЙ ОТЧЁТ\n"
    text += f"📅 Период: последние 7 дней\n"
    text += f"{'='*40}\n\n"
    
    total_shifts = 0
    total_hours = 0
    
    for stat in all_stats:
        is_admin_employee = stat['telegram_id'] in ADMIN_IDS or stat['telegram_id'] in SUPERADMIN_IDS
        admin_mark = " [АДМИН]" if is_admin_employee else ""
        
        text += (
            f"👤 {stat['full_name']}{admin_mark} (ID: {stat['wb_employee_id']})\n"
            f"   📅 Смен: {stat['shifts_count']}\n"
            f"   ⏱️ Часов: {stat['total_hours']}\n"
            f"   ☕ Перерывов: {stat['breaks_count']}\n"
            f"   ⏰ Время перерывов: {stat['total_breaks_hours']} ч\n\n"
        )
        total_shifts += stat['shifts_count']
        total_hours += stat['total_hours']
    
    text += f"{'='*40}\n"
    text += f"📊 ИТОГО:\n"
    text += f"📅 Всего смен: {total_shifts}\n"
    text += f"⏱️ Всего часов: {round(total_hours, 2)}\n"
    
    await message.answer(text[:4000])
    
    for admin_id in SUPERADMIN_IDS:
        try:
            await bot.send_message(admin_id, text[:4000])
        except Exception:
            pass


# ============= INLINE CALLBACKS =============

async def handle_callback_query(callback: CallbackQuery, bot: Bot):
    """Обработка инлайн кнопок"""
    await callback.answer()
    data = callback.data
    
    if data.startswith("approve_"):
        emp_id = int(data.split("_")[1])
        
        await db.approve_employee(emp_id)
        await callback.message.edit_text(f"✅ Сотрудник одобрен!")
        
        try:
            employee = await db.get_employee(emp_id)
            await bot.send_message(
                emp_id, 
                f"✅ Ваша регистрация одобрена!\n"
                f"Добро пожаловать в команду, {employee['full_name']}!\n\n"
                f"Теперь вы можете открыть смену через главное меню."
            )
        except Exception:
            pass
        
        await notify_admins(bot, f"✅ Сотрудник {employee['full_name']} (ID: {employee['wb_employee_id']}) одобрен")
    
    elif data.startswith("admin_stats_"):
        emp_id = int(data.split("_")[2])
        stats = await db.get_week_stats(emp_id)
        employee = await db.get_employee(emp_id)
        
        text = (
            f"📊 Статистика для {employee['full_name']}:\n\n"
            f"📅 Количество смен: {stats['shifts_count']}\n"
            f"⏱️ Отработанные часы: {stats['total_hours']} ч\n"
            f"☕ Количество перерывов: {stats['breaks_count']}\n"
            f"⏰ Время перерывов: {stats['total_breaks_hours']} ч"
        )
        
        await callback.message.answer(text)
        await callback.message.delete()
    
    elif data.startswith("edit_emp_"):
        emp_id = int(data.split("_")[2])
        user_data[callback.from_user.id] = {"edit_employee_id": emp_id}
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить ФИО", callback_data=f"edit_emp_name_{emp_id}")],
            [InlineKeyboardButton(text="✏️ Изменить WB ID", callback_data=f"edit_emp_wb_{emp_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit")]
        ])
        
        await callback.message.edit_text("Что хотите изменить?", reply_markup=keyboard)
    
    elif data.startswith("edit_emp_name_"):
        emp_id = int(data.split("_")[3])
        user_states[callback.from_user.id] = "edit_employee_name"
        user_data[callback.from_user.id] = {"edit_employee_id": emp_id}
        
        await callback.message.edit_text("Введите новое ФИО:")
    
    elif data.startswith("edit_emp_wb_"):
        emp_id = int(data.split("_")[3])
        user_states[callback.from_user.id] = "edit_employee_wb"
        user_data[callback.from_user.id] = {"edit_employee_id": emp_id}
        
        await callback.message.edit_text("Введите новый WB ID:")
    
    elif data.startswith("edit_shift_list_"):
        emp_id = int(data.split("_")[3])
        shifts = await db.get_employee_shifts(emp_id)
        
        if not shifts:
            await callback.message.edit_text("У этого сотрудника нет смен.")
            return
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"📅 {fmt_datetime(s['opened_at'])} - {fmt_datetime(s['closed_at']) if s['closed_at'] else 'активна'}",
                callback_data=f"edit_shift_{s['id']}"
            )]
            for s in shifts[:10]
        ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit")])
        
        user_data[callback.from_user.id] = {"edit_shift_employee": emp_id}
        await callback.message.edit_text("Выберите смену для редактирования:", reply_markup=keyboard)
    
    elif data.startswith("edit_shift_"):
        shift_id = int(data.split("_")[2])
        user_data[callback.from_user.id]["edit_shift_id"] = shift_id
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏰ Изменить время начала", callback_data=f"edit_shift_open_{shift_id}")],
            [InlineKeyboardButton(text="⏰ Изменить время окончания", callback_data=f"edit_shift_close_{shift_id}")],
            [InlineKeyboardButton(text="🗑️ Удалить смену", callback_data=f"delete_shift_{shift_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit")]
        ])
        
        await callback.message.edit_text("Что хотите изменить?", reply_markup=keyboard)
    
    elif data.startswith("edit_shift_open_"):
        shift_id = int(data.split("_")[3])
        user_states[callback.from_user.id] = "edit_shift_open"
        user_data[callback.from_user.id]["edit_shift_id"] = shift_id
        
        await callback.message.edit_text(
            "Введите новое время начала смены:\n\n"
            "Формат: ГГГГ-ММ-ДД ЧЧ:ММ:СС\n"
            "Пример: 2025-01-15 09:00:00\n\n"
            "Или используйте относительное время:\n"
            "+2 часа, -30 минут"
        )
    
    elif data.startswith("edit_shift_close_"):
        shift_id = int(data.split("_")[3])
        user_states[callback.from_user.id] = "edit_shift_close"
        user_data[callback.from_user.id]["edit_shift_id"] = shift_id
        
        await callback.message.edit_text(
            "Введите новое время окончания смены:\n\n"
            "Формат: ГГГГ-ММ-ДД ЧЧ:ММ:СС\n"
            "Пример: 2025-01-15 18:00:00\n\n"
            "Или используйте относительное время:\n"
            "+2 часа, -30 минут"
        )
    
    elif data.startswith("delete_shift_"):
        shift_id = int(data.split("_")[2])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_{shift_id}")],
            [InlineKeyboardButton(text="❌ Нет", callback_data="cancel_edit")]
        ])
        
        await callback.message.edit_text("Вы уверены, что хотите удалить эту смену?", reply_markup=keyboard)
    
    elif data.startswith("confirm_delete_"):
        shift_id = int(data.split("_")[2])
        await db.delete_shift(shift_id)
        await callback.message.edit_text("✅ Смена удалена!")
    
    elif data.startswith("cancel_edit"):
        if callback.from_user.id in user_states:
            del user_states[callback.from_user.id]
        await callback.message.delete()


# ============= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =============

async def process_edit_input(message: Message, bot: Bot):
    """Обработка ввода при редактировании"""
    telegram_id = message.from_user.id
    state = user_states.get(telegram_id)
    
    if state == "edit_employee_name":
        emp_id = user_data[telegram_id]["edit_employee_id"]
        await db.update_employee(emp_id, full_name=message.text)
        await message.answer(f"✅ ФИО изменено на: {message.text}")
        del user_states[telegram_id]
        return True
    
    elif state == "edit_employee_wb":
        emp_id = user_data[telegram_id]["edit_employee_id"]
        await db.update_employee(emp_id, wb_employee_id=message.text)
        await message.answer(f"✅ WB ID изменён на: {message.text}")
        del user_states[telegram_id]
        return True
    
    elif state in ["edit_shift_open", "edit_shift_close"]:
        shift_id = user_data[telegram_id]["edit_shift_id"]
        new_time = message.text.strip()
        
        try:
            if new_time.startswith("+"):
                hours = 0
                minutes = 0
                if 'час' in new_time or 'ч' in new_time:
                    hours_match = re.search(r'(\d+)\s*ч', new_time)
                    if hours_match:
                        hours = int(hours_match.group(1))
                if 'минут' in new_time or 'м' in new_time:
                    minutes_match = re.search(r'(\d+)\s*м', new_time)
                    if minutes_match:
                        minutes = int(minutes_match.group(1))
                new_dt = now_msk() + timedelta(hours=hours, minutes=minutes)
            elif new_time.startswith("-"):
                hours = 0
                minutes = 0
                if 'час' in new_time or 'ч' in new_time:
                    hours_match = re.search(r'(\d+)\s*ч', new_time)
                    if hours_match:
                        hours = int(hours_match.group(1))
                if 'минут' in new_time or 'м' in new_time:
                    minutes_match = re.search(r'(\d+)\s*м', new_time)
                    if minutes_match:
                        minutes = int(minutes_match.group(1))
                new_dt = now_msk() - timedelta(hours=hours, minutes=minutes)
            else:
                new_dt = datetime.strptime(new_time, "%Y-%m-%d %H:%M:%S")
                new_dt = TZ.localize(new_dt)
            
            if state == "edit_shift_open":
                await db.update_shift(shift_id, new_open_time=new_dt.isoformat())
                await message.answer(f"✅ Время начала смены изменено на {fmt_datetime(new_dt.isoformat())}")
            else:
                await db.update_shift(shift_id, new_close_time=new_dt.isoformat())
                await message.answer(f"✅ Время окончания смены изменено на {fmt_datetime(new_dt.isoformat())}")
            
            del user_states[telegram_id]
            return True
            
        except Exception as e:
            await message.answer(f"❌ Ошибка формата времени. Используйте формат: ГГГГ-ММ-ДД ЧЧ:ММ:СС или относительное время (+2 часа, -30 минут)\nОшибка: {e}")
            return True
    
    return False
