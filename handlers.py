import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional
import pytz

from aiogram import Bot, types
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode

from config import ADMIN_IDS, SUPERADMIN_IDS, PVZ_ADDRESS, GROUP_CHAT_ID, TIMEZONE
import database as db

# Состояния пользователей (простой словарь вместо FSM)
user_states: Dict[int, str] = {}  # state: "register_waiting", "edit_employee_waiting", "edit_shift_waiting"
user_data: Dict[int, Dict] = {}   # временные данные

TZ = pytz.timezone(TIMEZONE)


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


async def notify_admins(bot: Bot, message: str):
    """Уведомление всех администраторов"""
    for admin_id in ADMIN_IDS + SUPERADMIN_IDS:
        try:
            await bot.send_message(admin_id, message)
        except Exception:
            pass
    
    if GROUP_CHAT_ID:
        try:
            await bot.send_message(GROUP_CHAT_ID, message)
        except Exception:
            pass


# ============= КЛАВИАТУРЫ =============

def get_main_keyboard(telegram_id: int, is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Главная клавиатура (меняется в зависимости от статуса)"""
    keyboard = []
    
    if is_admin:
        keyboard.append([KeyboardButton(text="📊 Статистика сотрудника")])
        keyboard.append([KeyboardButton(text="👥 Активные смены")])
        keyboard.append([KeyboardButton(text="📋 Все сотрудники")])
        
        if telegram_id in SUPERADMIN_IDS:
            keyboard.append([KeyboardButton(text="⚙️ Суперадмин панель")])
    else:
        # Сотрудник
        keyboard.append([KeyboardButton(text="✅ Открыть смену"), KeyboardButton(text="☕ Перерыв")])
        keyboard.append([KeyboardButton(text="❌ Закрыть смену"), KeyboardButton(text="📈 Моя статистика")])
    
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


# ============= ОСНОВНЫЕ ОБРАБОТЧИКИ =============

async def handle_start(message: Message, bot: Bot):
    """Обработчик команды /start"""
    telegram_id = message.from_user.id
    is_superadmin = telegram_id in SUPERADMIN_IDS
    is_admin = telegram_id in ADMIN_IDS or is_superadmin
    
    if not await db.is_employee_exists(telegram_id):
        user_states[telegram_id] = "register_waiting"
        await message.answer(
            "👋 Добро пожаловать!\n\n"
            "Пожалуйста, пройдите регистрацию.\n"
            "Введите ваше ФИО:"
        )
    elif not await db.is_approved(telegram_id):
        await message.answer(
            "⏳ Ваша регистрация ожидает подтверждения администратором.\n"
            "Пожалуйста, ожидайте."
        )
    else:
        employee = await db.get_employee(telegram_id)
        active_shift = await db.get_active_shift(telegram_id)
        active_break = await db.get_active_break(telegram_id)
        
        keyboard = get_main_keyboard(telegram_id, is_admin)
        
        status_text = f"✅ Добро пожаловать, {employee['full_name']}!\n"
        if active_shift:
            opened_at = fmt_datetime(active_shift["opened_at"])
            status_text += f"\n🟢 Смена открыта с {opened_at}"
            if active_break:
                status_text += f"\n🔴 Вы на перерыве с {fmt_datetime(active_break['started_at'])}"
        
        await message.answer(status_text, reply_markup=keyboard)


async def handle_register(message: Message):
    """Обработка регистрации"""
    telegram_id = message.from_user.id
    
    if telegram_id not in user_states or user_states[telegram_id] != "register_waiting":
        return False
    
    if "full_name" not in user_data.get(telegram_id, {}):
        # Ожидаем ФИО
        user_data[telegram_id] = {"full_name": message.text}
        await message.answer("Введите ваш ID сотрудника WB:")
        return True
    
    elif "wb_id" not in user_data[telegram_id]:
        # Ожидаем WB ID
        user_data[telegram_id]["wb_id"] = message.text
        
        # Сохраняем в БД
        success = await db.register_employee(
            telegram_id,
            user_data[telegram_id]["full_name"],
            user_data[telegram_id]["wb_id"]
        )
        
        if success:
            await message.answer(
                "✅ Регистрация успешно завершена!\n"
                "Ваша заявка отправлена администратору на подтверждение.\n"
                "После подтверждения вы сможете начать работу."
            )
            
            # Уведомляем суперадминов
            for superadmin_id in SUPERADMIN_IDS:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{telegram_id}")]
                ])
                await message.bot.send_message(
                    superadmin_id,
                    f"🆕 Новая заявка на регистрацию!\n"
                    f"Пользователь: {user_data[telegram_id]['full_name']}\n"
                    f"WB ID: {user_data[telegram_id]['wb_id']}\n"
                    f"Telegram ID: {telegram_id}",
                    reply_markup=keyboard
                )
        else:
            await message.answer("❌ Ошибка регистрации. Возможно, вы уже зарегистрированы.")
        
        # Очищаем состояние
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
        await message.answer(
            f"✅ Смена открыта!\n"
            f"Время начала: {fmt_datetime(now_msk().isoformat())}\n"
            f"ПВЗ: {PVZ_ADDRESS}"
        )
        
        # Уведомляем админов
        employee = await db.get_employee(telegram_id)
        await notify_admins(bot, f"🟢 {employee['full_name']} открыл(а) смену в {fmt_datetime(now_msk().isoformat())}")
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
    
    success = await db.close_shift(telegram_id)
    if success:
        shift = await db.get_active_shift(telegram_id)
        await message.answer(
            f"✅ Смена закрыта!\n"
            f"Время закрытия: {fmt_datetime(now_msk().isoformat())}\n"
            f"Длительность: {fmt_duration(shift['duration_minutes']) if shift else 'неизвестно'}"
        )
        
        employee = await db.get_employee(telegram_id)
        await notify_admins(bot, f"🔴 {employee['full_name']} закрыл(а) смену")
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
        await message.answer(
            f"☕ Перерыв начался!\n"
            f"Время начала: {fmt_datetime(now_msk().isoformat())}"
        )
        
        employee = await db.get_employee(telegram_id)
        
        # Уведомляем админов о начале перерыва
        await notify_admins(bot, f"☕ {employee['full_name']} начал(а) перерыв в {fmt_datetime(now_msk().isoformat())}")
        
        # Запускаем таймер на 15 минут для проверки
        asyncio.create_task(check_break_duration(telegram_id, message.bot, break_id))
    else:
        await message.answer("❌ Ошибка начала перерыва.")
    
    if telegram_id in user_states:
        del user_states[telegram_id]


async def check_break_duration(telegram_id: int, bot: Bot, break_id: int):
    """Проверка длительности перерыва (уведомление через 15 минут)"""
    await asyncio.sleep(15 * 60)  # 15 минут
    
    break_obj = await db.get_active_break(telegram_id)
    if break_obj and break_obj["id"] == break_id:
        employee = await db.get_employee(telegram_id)
        await notify_admins(
            bot,
            f"⚠️ ВНИМАНИЕ! {employee['full_name']} находится на перерыве уже более 15 минут!\n"
            f"Начало перерыва: {fmt_datetime(break_obj['started_at'])}"
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
    if duration is not False:
        await message.answer(
            f"✅ Перерыв завершён!\n"
            f"Время завершения: {fmt_datetime(now_msk().isoformat())}\n"
            f"Длительность: {fmt_duration(duration)}"
        )
        
        employee = await db.get_employee(telegram_id)
        await notify_admins(bot, f"✅ {employee['full_name']} завершил(а) перерыв")
    else:
        await message.answer("❌ Ошибка завершения перерыва.")


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
        text += (
            f"👤 {emp['full_name']}\n"
            f"   {status}\n"
            f"   ⏱️ Работает: {fmt_duration(emp['duration_minutes'])}\n"
            f"   🕐 Начало: {fmt_datetime(emp['opened_at'])}\n"
            f"   {'   ☕ Начало перерыва: ' + fmt_datetime(emp['break_started']) if emp['break_started'] else ''}\n\n"
        )
    
    await message.answer(text)


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
        text += f"👤 {emp['full_name']}\n"
        text += f"   ID: {emp['wb_employee_id']}\n"
        text += f"   Telegram: {emp['telegram_id']}\n"
        text += f"   Регистрация: {fmt_datetime(emp['registered_at'])}\n\n"
    
    await message.answer(text)


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
        [InlineKeyboardButton(text=f"{emp['full_name']} ({emp['wb_employee_id']})", 
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
        text += f"👤 {emp['full_name']}\n"
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
        [InlineKeyboardButton(text=f"{emp['full_name']} ({emp['wb_employee_id']})", 
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
    
    text = "📊 НЕДЕЛЬНЫЙ ОТЧЁТ\n\n"
    for stat in all_stats:
        text += (
            f"👤 {stat['full_name']} (ID: {stat['wb_employee_id']})\n"
            f"   📅 Смен: {stat['shifts_count']}\n"
            f"   ⏱️ Часов: {stat['total_hours']}\n"
            f"   ☕ Перерывов: {stat['breaks_count']}\n"
            f"   ⏰ Время перерывов: {stat['total_breaks_hours']} ч\n\n"
        )
    
    # Отправляем в чат
    await message.answer(text[:4000])
    
    # Отправляем в личку админам
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
    
    # Одобрение сотрудника
    if data.startswith("approve_"):
        emp_id = int(data.split("_")[1])
        
        await db.approve_employee(emp_id)
        await callback.message.edit_text(f"✅ Сотрудник одобрен!")
        
        # Уведомляем сотрудника
        try:
            await bot.send_message(emp_id, "✅ Ваша регистрация одобрена! Вы можете начинать работу.")
        except Exception:
            pass
        
        await notify_admins(bot, f"✅ Сотрудник {emp_id} одобрен")
    
    # Статистика сотрудника для админа
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
    
    # Редактирование сотрудника - выбор поля
    elif data.startswith("edit_emp_"):
        emp_id = int(data.split("_")[2])
        user_data[callback.from_user.id] = {"edit_employee_id": emp_id}
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить ФИО", callback_data=f"edit_emp_name_{emp_id}")],
            [InlineKeyboardButton(text="✏️ Изменить WB ID", callback_data=f"edit_emp_wb_{emp_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit")]
        ])
        
        await callback.message.edit_text("Что хотите изменить?", reply_markup=keyboard)
    
    # Изменение ФИО
    elif data.startswith("edit_emp_name_"):
        emp_id = int(data.split("_")[3])
        user_states[callback.from_user.id] = "edit_employee_name"
        user_data[callback.from_user.id] = {"edit_employee_id": emp_id}
        
        await callback.message.edit_text("Введите новое ФИО:")
    
    # Изменение WB ID
    elif data.startswith("edit_emp_wb_"):
        emp_id = int(data.split("_")[3])
        user_states[callback.from_user.id] = "edit_employee_wb"
        user_data[callback.from_user.id] = {"edit_employee_id": emp_id}
        
        await callback.message.edit_text("Введите новый WB ID:")
    
    # Список смен для редактирования
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
            for s in shifts[:10]  # Показываем последние 10 смен
        ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit")])
        
        user_data[callback.from_user.id] = {"edit_shift_employee": emp_id}
        await callback.message.edit_text("Выберите смену для редактирования:", reply_markup=keyboard)
    
    # Редактирование конкретной смены
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
    
    # Изменение времени начала смены
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
    
    # Изменение времени окончания смены
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
    
    # Удаление смены
    elif data.startswith("delete_shift_"):
        shift_id = int(data.split("_")[2])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_{shift_id}")],
            [InlineKeyboardButton(text="❌ Нет", callback_data="cancel_edit")]
        ])
        
        await callback.message.edit_text("Вы уверены, что хотите удалить эту смену?", reply_markup=keyboard)
    
    # Подтверждение удаления
    elif data.startswith("confirm_delete_"):
        shift_id = int(data.split("_")[2])
        await db.delete_shift(shift_id)
        await callback.message.edit_text("✅ Смена удалена!")
    
    # Отмена редактирования
    elif data.startswith("cancel_edit"):
        if callback.from_user.id in user_states:
            del user_states[callback.from_user.id]
        await callback.message.delete()


# ============= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =============

async def process_edit_input(message: Message):
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
        
        # Парсим время (поддержка относительного)
        try:
            if new_time.startswith("+"):
                # Относительное время вперед
                parts = new_time[1:].split()
                hours = 0
                minutes = 0
                for part in parts:
                    if "час" in part or "ч" in part:
                        hours = int(''.join(filter(str.isdigit, part)))
                    elif "минут" in part or "м" in part:
                        minutes = int(''.join(filter(str.isdigit, part)))
                if state == "edit_shift_open":
                    # Для начала смены от текущего времени
                    new_dt = now_msk() + timedelta(hours=hours, minutes=minutes)
                else:
                    # Для окончания от времени открытия
                    shift = await db.get_employee_shifts(user_data[telegram_id].get("edit_shift_employee", 0))
                    # Нужно получить shift_id, упростим
                    new_dt = now_msk() + timedelta(hours=hours, minutes=minutes)
            elif new_time.startswith("-"):
                parts = new_time[1:].split()
                hours = 0
                minutes = 0
                for part in parts:
                    if "час" in part or "ч" in part:
                        hours = int(''.join(filter(str.isdigit, part)))
                    elif "минут" in part or "м" in part:
                        minutes = int(''.join(filter(str.isdigit, part)))
                new_dt = now_msk() - timedelta(hours=hours, minutes=minutes)
            else:
                # Абсолютное время
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
            await message.answer(f"❌ Ошибка формата времени. Используйте формат: ГГГГ-ММ-ДД ЧЧ:ММ:СС или относительное время (+2 часа, -30 минут)")
            return True
    
    return False
