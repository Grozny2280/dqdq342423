import aiosqlite
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import pytz
from config import TIMEZONE

DB_PATH = "pvz_bot.db"
TZ = pytz.timezone(TIMEZONE)


def now_msk() -> datetime:
    """Возвращает текущее время в MSK"""
    return datetime.now(TZ)


async def init_db():
    """Инициализация базы данных"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица сотрудников
        await db.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                telegram_id INTEGER PRIMARY KEY,
                full_name TEXT NOT NULL,
                wb_employee_id TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                approved INTEGER DEFAULT 0
            )
        """)
        
        # Таблица смен
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                photo_open_id TEXT,
                duration_minutes INTEGER,
                FOREIGN KEY (telegram_id) REFERENCES employees (telegram_id)
            )
        """)
        
        # Таблица перерывов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS breaks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                photo_id TEXT,
                duration_minutes INTEGER,
                shift_id INTEGER,
                FOREIGN KEY (telegram_id) REFERENCES employees (telegram_id),
                FOREIGN KEY (shift_id) REFERENCES shifts (id)
            )
        """)
        
        # Индексы для ускорения запросов
        await db.execute("CREATE INDEX IF NOT EXISTS idx_shifts_telegram_id ON shifts(telegram_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_shifts_closed_at ON shifts(closed_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_breaks_telegram_id ON breaks(telegram_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_breaks_ended_at ON breaks(ended_at)")
        
        await db.commit()


# ============= РАБОТА С СОТРУДНИКАМИ =============

async def register_employee(telegram_id: int, full_name: str, wb_employee_id: str) -> bool:
    """
    Регистрация нового сотрудника
    
    Args:
        telegram_id: ID пользователя в Telegram
        full_name: ФИО сотрудника
        wb_employee_id: ID сотрудника в Wildberries
    
    Returns:
        bool: True если регистрация успешна, False если пользователь уже существует
    """
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO employees (telegram_id, full_name, wb_employee_id, registered_at, approved) VALUES (?, ?, ?, ?, 0)",
                (telegram_id, full_name, wb_employee_id, now_msk().isoformat())
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def is_employee_exists(telegram_id: int) -> bool:
    """
    Проверка существования сотрудника
    
    Args:
        telegram_id: ID пользователя в Telegram
    
    Returns:
        bool: True если сотрудник существует
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM employees WHERE telegram_id = ?", (telegram_id,)) as cursor:
            return await cursor.fetchone() is not None


async def is_approved(telegram_id: int) -> bool:
    """
    Проверка одобрения сотрудника
    
    Args:
        telegram_id: ID пользователя в Telegram
    
    Returns:
        bool: True если сотрудник одобрен
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT approved FROM employees WHERE telegram_id = ?", (telegram_id,)) as cursor:
            row = await cursor.fetchone()
            return row is not None and row[0] == 1


async def approve_employee(telegram_id: int) -> bool:
    """
    Одобрение сотрудника
    
    Args:
        telegram_id: ID пользователя в Telegram
    
    Returns:
        bool: True если успешно
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE employees SET approved = 1 WHERE telegram_id = ?", (telegram_id,))
        await db.commit()
        return True


async def get_employee(telegram_id: int) -> Optional[Dict]:
    """
    Получение данных сотрудника
    
    Args:
        telegram_id: ID пользователя в Telegram
    
    Returns:
        Dict: Данные сотрудника или None
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT telegram_id, full_name, wb_employee_id, registered_at, approved FROM employees WHERE telegram_id = ?",
            (telegram_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "telegram_id": row[0],
                    "full_name": row[1],
                    "wb_employee_id": row[2],
                    "registered_at": row[3],
                    "approved": row[4]
                }
            return None


async def get_all_employees(include_unapproved: bool = False) -> List[Dict]:
    """
    Получение списка всех сотрудников
    
    Args:
        include_unapproved: Включать ли неодобренных сотрудников
    
    Returns:
        List[Dict]: Список сотрудников
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if include_unapproved:
            query = "SELECT telegram_id, full_name, wb_employee_id, registered_at, approved FROM employees ORDER BY registered_at"
        else:
            query = "SELECT telegram_id, full_name, wb_employee_id, registered_at, approved FROM employees WHERE approved = 1 ORDER BY registered_at"
        
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "telegram_id": row[0],
                    "full_name": row[1],
                    "wb_employee_id": row[2],
                    "registered_at": row[3],
                    "approved": row[4]
                }
                for row in rows
            ]


async def update_employee(telegram_id: int, full_name: str = None, wb_employee_id: str = None) -> bool:
    """
    Обновление данных сотрудника
    
    Args:
        telegram_id: ID пользователя в Telegram
        full_name: Новое ФИО (опционально)
        wb_employee_id: Новый WB ID (опционально)
    
    Returns:
        bool: True если успешно
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if full_name:
            await db.execute("UPDATE employees SET full_name = ? WHERE telegram_id = ?", (full_name, telegram_id))
        if wb_employee_id:
            await db.execute("UPDATE employees SET wb_employee_id = ? WHERE telegram_id = ?", (wb_employee_id, telegram_id))
        await db.commit()
        return True


# ============= РАБОТА СО СМЕНАМИ =============

async def get_active_shift(telegram_id: int) -> Optional[Dict]:
    """
    Получение активной смены сотрудника
    
    Args:
        telegram_id: ID пользователя в Telegram
    
    Returns:
        Dict: Данные активной смены или None
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, telegram_id, opened_at, closed_at, photo_open_id, duration_minutes FROM shifts WHERE telegram_id = ? AND closed_at IS NULL",
            (telegram_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "id": row[0],
                    "telegram_id": row[1],
                    "opened_at": row[2],
                    "closed_at": row[3],
                    "photo_open_id": row[4],
                    "duration_minutes": row[5]
                }
            return None


async def open_shift(telegram_id: int, photo_id: str) -> int:
    """
    Открытие новой смены
    
    Args:
        telegram_id: ID пользователя в Telegram
        photo_id: ID фото в Telegram
    
    Returns:
        int: ID созданной смены
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO shifts (telegram_id, opened_at, photo_open_id) VALUES (?, ?, ?)",
            (telegram_id, now_msk().isoformat(), photo_id)
        )
        await db.commit()
        return cursor.lastrowid


async def close_shift(telegram_id: int) -> Optional[int]:
    """
    Закрытие активной смены
    
    Args:
        telegram_id: ID пользователя в Telegram
    
    Returns:
        int: Длительность смены в минутах или None
    """
    shift = await get_active_shift(telegram_id)
    if not shift:
        return None
    
    opened_at = datetime.fromisoformat(shift["opened_at"])
    closed_at = now_msk()
    duration = int((closed_at - opened_at).total_seconds() / 60)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE shifts SET closed_at = ?, duration_minutes = ? WHERE id = ?",
            (closed_at.isoformat(), duration, shift["id"])
        )
        await db.commit()
    return duration


async def get_shifts_stats(telegram_id: int, days: int = 7) -> Dict:
    """
    Получение статистики по сменам за период
    
    Args:
        telegram_id: ID пользователя в Telegram
        days: Количество дней (по умолчанию 7)
    
    Returns:
        Dict: Статистика смен
    """
    start_date = now_msk() - timedelta(days=days)
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT opened_at, closed_at, duration_minutes FROM shifts WHERE telegram_id = ? AND opened_at >= ? AND closed_at IS NOT NULL",
            (telegram_id, start_date.isoformat())
        ) as cursor:
            rows = await cursor.fetchall()
            
            shifts_count = len(rows)
            total_hours = sum(row[2] for row in rows if row[2]) / 60 if rows else 0
            
            return {
                "shifts_count": shifts_count,
                "total_hours": round(total_hours, 2),
                "shifts": rows
            }


# ============= РАБОТА С ПЕРЕРЫВАМИ =============

async def get_active_break(telegram_id: int) -> Optional[Dict]:
    """
    Получение активного перерыва сотрудника
    
    Args:
        telegram_id: ID пользователя в Telegram
    
    Returns:
        Dict: Данные активного перерыва или None
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, telegram_id, started_at, ended_at, photo_id, duration_minutes, shift_id FROM breaks WHERE telegram_id = ? AND ended_at IS NULL",
            (telegram_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "id": row[0],
                    "telegram_id": row[1],
                    "started_at": row[2],
                    "ended_at": row[3],
                    "photo_id": row[4],
                    "duration_minutes": row[5],
                    "shift_id": row[6]
                }
            return None


async def start_break(telegram_id: int, photo_id: str) -> Optional[int]:
    """
    Начало перерыва
    
    Args:
        telegram_id: ID пользователя в Telegram
        photo_id: ID фото в Telegram
    
    Returns:
        int: ID созданного перерыва или None
    """
    shift = await get_active_shift(telegram_id)
    if not shift:
        return None
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO breaks (telegram_id, started_at, photo_id, shift_id) VALUES (?, ?, ?, ?)",
            (telegram_id, now_msk().isoformat(), photo_id, shift["id"])
        )
        await db.commit()
        return cursor.lastrowid


async def end_break(telegram_id: int) -> Optional[int]:
    """
    Завершение активного перерыва
    
    Args:
        telegram_id: ID пользователя в Telegram
    
    Returns:
        int: Длительность перерыва в минутах или None
    """
    break_obj = await get_active_break(telegram_id)
    if not break_obj:
        return None
    
    started_at = datetime.fromisoformat(break_obj["started_at"])
    ended_at = now_msk()
    duration = int((ended_at - started_at).total_seconds() / 60)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE breaks SET ended_at = ?, duration_minutes = ? WHERE id = ?",
            (ended_at.isoformat(), duration, break_obj["id"])
        )
        await db.commit()
    return duration


async def get_breaks_stats(telegram_id: int, days: int = 7) -> Dict:
    """
    Получение статистики по перерывам за период
    
    Args:
        telegram_id: ID пользователя в Telegram
        days: Количество дней (по умолчанию 7)
    
    Returns:
        Dict: Статистика перерывов
    """
    start_date = now_msk() - timedelta(days=days)
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT started_at, ended_at, duration_minutes FROM breaks WHERE telegram_id = ? AND started_at >= ? AND ended_at IS NOT NULL",
            (telegram_id, start_date.isoformat())
        ) as cursor:
            rows = await cursor.fetchall()
            
            breaks_count = len(rows)
            total_breaks_minutes = sum(row[2] for row in rows if row[2]) if rows else 0
            
            return {
                "breaks_count": breaks_count,
                "total_breaks_hours": round(total_breaks_minutes / 60, 2),
                "breaks": rows
            }


async def get_week_stats(telegram_id: int) -> Dict:
    """
    Получение полной статистики за неделю
    
    Args:
        telegram_id: ID пользователя в Telegram
    
    Returns:
        Dict: Полная статистика (смены и перерывы)
    """
    shifts = await get_shifts_stats(telegram_id, 7)
    breaks = await get_breaks_stats(telegram_id, 7)
    
    return {
        "shifts_count": shifts["shifts_count"],
        "total_hours": shifts["total_hours"],
        "breaks_count": breaks["breaks_count"],
        "total_breaks_hours": breaks["total_breaks_hours"]
    }


async def get_all_week_stats() -> List[Dict]:
    """
    Получение статистики за неделю по всем сотрудникам
    
    Returns:
        List[Dict]: Список статистики по всем сотрудникам
    """
    employees = await get_all_employees(include_unapproved=False)
    stats = []
    
    for emp in employees:
        emp_stats = await get_week_stats(emp["telegram_id"])
        stats.append({
            "telegram_id": emp["telegram_id"],
            "full_name": emp["full_name"],
            "wb_employee_id": emp["wb_employee_id"],
            **emp_stats
        })
    
    return stats


# ============= ДЛЯ АДМИНОВ =============

async def get_active_employees() -> List[Dict]:
    """
    Получение списка сотрудников с активными сменами
    
    Returns:
        List[Dict]: Список сотрудников с активными сменами
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT s.telegram_id, e.full_name, s.opened_at, b.id as break_id, b.started_at as break_started "
            "FROM shifts s "
            "JOIN employees e ON s.telegram_id = e.telegram_id "
            "LEFT JOIN breaks b ON b.telegram_id = s.telegram_id AND b.ended_at IS NULL "
            "WHERE s.closed_at IS NULL"
        ) as cursor:
            rows = await cursor.fetchall()
            
            result = []
            for row in rows:
                opened_at = datetime.fromisoformat(row[2])
                duration = int((now_msk() - opened_at).total_seconds() / 60)
                
                result.append({
                    "telegram_id": row[0],
                    "full_name": row[1],
                    "opened_at": row[2],
                    "duration_minutes": duration,
                    "on_break": row[3] is not None,
                    "break_started": row[4]
                })
            
            return result


async def get_unapproved_employees() -> List[Dict]:
    """
    Получение списка неодобренных сотрудников
    
    Returns:
        List[Dict]: Список неодобренных сотрудников
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT telegram_id, full_name, wb_employee_id, registered_at FROM employees WHERE approved = 0"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "telegram_id": row[0],
                    "full_name": row[1],
                    "wb_employee_id": row[2],
                    "registered_at": row[3]
                }
                for row in rows
            ]


async def update_shift(shift_id: int, new_open_time: str = None, new_close_time: str = None) -> bool:
    """
    Обновление времени смены
    
    Args:
        shift_id: ID смены
        new_open_time: Новое время начала (ISO формат)
        new_close_time: Новое время окончания (ISO формат)
    
    Returns:
        bool: True если успешно
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if new_open_time:
            await db.execute("UPDATE shifts SET opened_at = ? WHERE id = ?", (new_open_time, shift_id))
        if new_close_time:
            await db.execute("UPDATE shifts SET closed_at = ? WHERE id = ?", (new_close_time, shift_id))
        
        if new_open_time or new_close_time:
            # Пересчитываем длительность
            if new_open_time and new_close_time:
                duration = int((datetime.fromisoformat(new_close_time) - datetime.fromisoformat(new_open_time)).total_seconds() / 60)
                await db.execute("UPDATE shifts SET duration_minutes = ? WHERE id = ?", (duration, shift_id))
            elif new_open_time:
                async with db.execute("SELECT closed_at FROM shifts WHERE id = ?", (shift_id,)) as cursor:
                    row = await cursor.fetchone()
                    if row and row[0]:
                        duration = int((datetime.fromisoformat(row[0]) - datetime.fromisoformat(new_open_time)).total_seconds() / 60)
                        await db.execute("UPDATE shifts SET duration_minutes = ? WHERE id = ?", (duration, shift_id))
            elif new_close_time:
                async with db.execute("SELECT opened_at FROM shifts WHERE id = ?", (shift_id,)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        duration = int((datetime.fromisoformat(new_close_time) - datetime.fromisoformat(row[0])).total_seconds() / 60)
                        await db.execute("UPDATE shifts SET duration_minutes = ? WHERE id = ?", (duration, shift_id))
        
        await db.commit()
    return True


async def delete_shift(shift_id: int) -> bool:
    """
    Удаление смены и связанных с ней перерывов
    
    Args:
        shift_id: ID смены
    
    Returns:
        bool: True если успешно
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM shifts WHERE id = ?", (shift_id,))
        await db.execute("DELETE FROM breaks WHERE shift_id = ?", (shift_id,))
        await db.commit()
    return True


async def get_employee_shifts(telegram_id: int) -> List[Dict]:
    """
    Получение всех смен сотрудника для редактирования
    
    Args:
        telegram_id: ID пользователя в Telegram
    
    Returns:
        List[Dict]: Список смен сотрудника
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, opened_at, closed_at, duration_minutes FROM shifts WHERE telegram_id = ? ORDER BY opened_at DESC",
            (telegram_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "opened_at": row[1],
                    "closed_at": row[2],
                    "duration_minutes": row[3]
                }
                for row in rows
            ]


async def debug_print_all_data():
    """
    Отладочная функция для вывода всех данных из БД
    """
    async with aiosqlite.connect(DB_PATH) as db:
        print("\n=== Employees ===")
        async with db.execute("SELECT * FROM employees") as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                print(row)
        
        print("\n=== Shifts ===")
        async with db.execute("SELECT * FROM shifts") as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                print(row)
        
        print("\n=== Breaks ===")
        async with db.execute("SELECT * FROM breaks") as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                print(row)
