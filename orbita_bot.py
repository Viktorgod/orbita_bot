import asyncio
import os
import json
from datetime import datetime

from aiogram import Bot, Dispatcher, types, Router
from aiogram.filters import Command

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ---------------- БАЗОВЫЕ НАСТРОЙКИ ----------------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID_ENV = os.getenv("CHAT_ID")
ORBITA_LOGIN = os.getenv("ORBITA_LOGIN")
ORBITA_PASSWORD = os.getenv("ORBITA_PASSWORD")
PLAN_DAY = float(os.getenv("PLAN_DAY", "2000"))  # общий план на день

CHECK_INTERVAL = 10  # 1 час

HISTORY_FILE = "last.json"             # для почасового прироста
PLANS_FILE = "plans.json"              # индивидуальные планы
MONTH_HISTORY_FILE = "history_month.json"  # история для месячных планов
BOT_STATE_FILE = "bot_state.json"      # состояние запущен/остановлен

ADMIN_ID = 1593390747  # твой Telegram ID

BOT_RUNNING = False     # будет загружено из файла
PLANS = {}              # планы из plans.json


def validate_env():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN not set")
    if not CHAT_ID_ENV:
        raise RuntimeError("CHAT_ID not set")
    try:
        chat_id_int = int(CHAT_ID_ENV)
    except ValueError:
        raise RuntimeError("CHAT_ID must be int")
    if not ORBITA_LOGIN or not ORBITA_PASSWORD:
        raise RuntimeError("ORBITA_LOGIN or ORBITA_PASSWORD missing")
    return chat_id_int


CHAT_ID = validate_env()


# ---------------- РАБОТА С ФАЙЛАМИ ----------------

def save_last(values: dict):
    """Сохраняем последние значения (для почасового прироста)."""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(values, f, ensure_ascii=False)


def load_last() -> dict:
    """Загружаем последние значения (для расчёта прироста)."""
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_plans() -> dict:
    """
    Загружаем индивидуальные планы.
    Ожидаемый формат plans.json:
    {
      "Имя Фамилия": { "day": 400, "month": 12000 },
      "Другой Админ": { "day": 500, "month": 15000 }
    }
    """
    if not os.path.exists(PLANS_FILE):
        return {}
    try:
        with open(PLANS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except Exception:
        return {}


def load_month_history() -> dict:
    """Загружаем историю по дням для расчёта месячных планов."""
    if not os.path.exists(MONTH_HISTORY_FILE):
        return {}
    try:
        with open(MONTH_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except Exception:
        return {}


def save_month_history(current_values: dict):
    """
    Сохраняем значения за текущий день для месячной статистики.
    Для каждого админа храним максимальное значение за день (обновляем каждый час).
    Формат:
    {
      "2025-01-22": {
        "Имя": 430.0,
        "Имя2": 370.0
      }
    }
    """
    today = datetime.now().strftime("%Y-%m-%d")
    history = load_month_history()
    day_data = history.get(today, {})

    for name, val in current_values.items():
        try:
            val_f = float(val)
        except Exception:
            val_f = 0.0
        prev = day_data.get(name, 0.0)
        try:
            prev_f = float(prev)
        except Exception:
            prev_f = 0.0
        if val_f > prev_f:
            day_data[name] = val_f

    history[today] = day_data

    with open(MONTH_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False)


def calculate_month_totals() -> dict:
    """
    Считаем суммарный результат за текущий календарный месяц для каждого админа.
    Складываем дневные максимумы текущего месяца.
    Возвращает dict: { "Имя": суммарный_результат_за_месяц, ... }
    """
    now = datetime.now()
    ym_prefix = now.strftime("%Y-%m-")  # типа "2025-01-"
    history = load_month_history()
    totals = {}

    for day, data in history.items():
        if not isinstance(data, dict):
            continue
        if not day.startswith(ym_prefix):
            continue
        for name, val in data.items():
            try:
                v = float(val)
            except Exception:
                v = 0.0
            totals[name] = totals.get(name, 0.0) + v

    return totals


def load_bot_state() -> bool:
    """Загружаем состояние бота (запущен/остановлен)."""
    if not os.path.exists(BOT_STATE_FILE):
        return False
    try:
        with open(BOT_STATE_FILE, "r", encoding="utf-8") as f:
            state = f.read().strip()
            return state == "1"
    except Exception:
        return False


def save_bot_state(running: bool):
    """Сохраняем состояние бота."""
    try:
        with open(BOT_STATE_FILE, "w", encoding="utf-8") as f:
            f.write("1" if running else "0")
    except Exception:
        pass


PLANS = load_plans()
BOT_RUNNING = load_bot_state()


# ---------------- SELENIUM ----------------

def create_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1600,900")
    return webdriver.Chrome(options=options)


def find_today_column(table):
    today_str = f"{datetime.now().day:02d}"
    rows = table.find_elements(By.TAG_NAME, "tr")
    for row in rows:
        ths = row.find_elements(By.TAG_NAME, "th")
        for idx, th in enumerate(ths):
            aria = th.get_attribute("aria-label") or ""
            txt = th.text.strip()
            if aria.startswith(today_str + ":") or txt == today_str:
                return idx
    return None


def parse_balance_table(driver):
    """
    Возвращает:
    - текст с балансом, общим планом и индивидуальными дневными планами
    - dict с текущими значениями по каждому админу: {имя: значение}
    """
    now = datetime.now()
    today_str = f"{now.day:02d}"
    month_str = f"{now.month:02d}"

    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.TAG_NAME, "table"))
    )
    table = driver.find_element(By.TAG_NAME, "table")
    today_col = find_today_column(table)
    if today_col is None:
        return f"❌ Column for {today_str}.{month_str} not found", {}

    rows = table.find_elements(By.TAG_NAME, "tr")
    pairs = []

    for row in rows:
        ths = row.find_elements(By.TAG_NAME, "th")
        if not ths:
            continue
        name = ths[0].text.strip()
        lname = name.lower()
        if (
            not name
            or "всего" in lname
            or "итого" in lname
            or "администратор" in lname
            or name[0].isdigit()
            or len(name.split()) < 2
        ):
            continue
        tds = row.find_elements(By.TAG_NAME, "td")
        if len(tds) <= today_col:
            continue
        value = tds[today_col].text.strip() or "0"
        try:
            num_value = float(value.replace(",", "."))
        except Exception:
            num_value = 0.0
        pairs.append((name, num_value))

    if not pairs:
        return f"No data for {today_str}.{month_str}", {}

    # сортируем по убыванию
    pairs.sort(key=lambda x: x[1], reverse=True)
    total = sum(val for _, val in pairs)

    # общий план на день
    if PLAN_DAY > 0:
        left = round(PLAN_DAY - total, 2)
        percent = round(total / PLAN_DAY * 100, 1)
    else:
        left = 0.0
        percent = 0.0

    medals = ["🥇", "🥈", "🥉"]
    lines = [f"📊 Баланс за {today_str}.{month_str}\n"]

    for i, (name, val) in enumerate(pairs):
        medal = medals[i] if i < 3 else "▫️"
        lines.append(f"{medal} {name:<20} — {val}")

    lines.append(f"\n💰 Итого: {total}")

    # общий план на день
    if PLAN_DAY > 0:
        lines.append("")
        lines.append("🎯 Общий план на день:")
        lines.append(f"📌 План: {PLAN_DAY}")
        lines.append(f"📊 Выполнено: {total} ({percent}%)")
        if left > 0:
            lines.append(f"⏳ Осталось: {left}")
        else:
            lines.append("🏆 План выполнен!")

    # индивидуальные планы на день (только у кого есть план)
    lines.append("")
    lines.append("🎯 Индивидуальные планы на день:")
    for name, val in pairs:
        if name not in PLANS:
            continue
        plan_info = PLANS.get(name)
        day_plan = 0.0
        if isinstance(plan_info, dict):
            try:
                day_plan = float(plan_info.get("day", 0) or 0)
            except Exception:
                day_plan = 0.0
        elif isinstance(plan_info, (int, float, str)):
            try:
                day_plan = float(plan_info)
            except Exception:
                day_plan = 0.0

        if day_plan > 0:
            percent_day = round(val / day_plan * 100, 1)
            left_day = round(day_plan - val, 2)
            if left_day <= 0:
                lines.append(
                    f"🏆 {name}: {val}/{day_plan} ({percent_day}%) — план выполнен!"
                )
            else:
                lines.append(
                    f"⏳ {name}: {val}/{day_plan} ({percent_day}%), осталось {left_day}"
                )

    return "\n".join(lines), dict(pairs)


def login_and_get_balance_text():
    driver = create_driver()
    wait = WebDriverWait(driver, 30)
    try:
        driver.get("https://orbita.life/login")
        email_input = wait.until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    "input[type='email'], input[name='email'], input[name='login']",
                )
            )
        )
        pwd_input = driver.find_element(
            By.CSS_SELECTOR, "input[type='password'], input[name='password']"
        )
        email_input.clear()
        email_input.send_keys(ORBITA_LOGIN)
        pwd_input.clear()
        pwd_input.send_keys(ORBITA_PASSWORD)
        btn = driver.find_element(
            By.CSS_SELECTOR, "button[type='submit'], button.btn-primary"
        )
        btn.click()
        wait.until(lambda d: "login" not in d.current_url.lower())
        driver.get("https://orbita.life")
        return parse_balance_table(driver)
    finally:
        driver.quit()


# ---------------- ПОМОЩНИК ОТПРАВКИ ----------------

async def send_long(bot: Bot, chat_id: int, text: str):
    if len(text) <= 4000:
        await bot.send_message(chat_id, text)
        return
    for i in range(0, len(text), 4000):
        await bot.send_message(chat_id, text[i : i + 4000])


# ---------------- ФОНОВЫЙ ВОРКЕР ----------------

async def worker(bot: Bot):
    global BOT_RUNNING
    while True:
        if not BOT_RUNNING:
            await asyncio.sleep(3)
            continue
        try:
            balance_text, current_values = login_and_get_balance_text()

            # сохраняем историю месяца (максимумы за день, вызываем каждый час)
            save_month_history(current_values)
            month_totals = calculate_month_totals()

            # почасовой прирост
            last_values = load_last()
            growth_lines = []
            total_delta = 0.0

            for name, val in current_values.items():
                old = last_values.get(name, val)
                try:
                    val_f = float(val)
                except Exception:
                    val_f = 0.0
                try:
                    old_f = float(old)
                except Exception:
                    old_f = val_f

                diff = round(val_f - old_f, 2)
                total_delta += diff

                if diff > 0:
                    growth_lines.append(f"📈 {name}: +{diff}")
                elif diff < 0:
                    growth_lines.append(f"📉 {name}: {diff}")
                else:
                    growth_lines.append(f"⏸ {name}: 0")

            save_last(current_values)

            # блок по месячным планам: факт / план (процент), только у кого есть month
            month_lines = ["📅 Планы на месяц:"]
            for name in sorted(current_values.keys()):
                plan_info = PLANS.get(name)
                if not isinstance(plan_info, dict):
                    continue
                month_plan_raw = plan_info.get("month", 0)
                try:
                    month_plan = float(month_plan_raw or 0)
                except Exception:
                    month_plan = 0.0
                if month_plan <= 0:
                    continue

                month_fact = month_totals.get(name, 0.0)
                percent_month = (
                    round(month_fact / month_plan * 100, 1) if month_plan else 0.0
                )
                month_lines.append(
                    f"📅 {name}: {month_fact}/{month_plan} ({percent_month}%)"
                )

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            full_text = (
                f"⏰ Обновление ORBITA ({now_str})\n\n"
                f"{balance_text}\n\n"
                + "\n".join(month_lines)
                + "\n\n"
                f"🧮 Общий прирост за последний час: {total_delta:+}\n\n"
                "📊 Изменения по людям:\n"
                + "\n".join(growth_lines)
            )

            await send_long(bot, CHAT_ID, full_text)

        except Exception as e:
            try:
                await bot.send_message(CHAT_ID, f"❌ Error:\n{e}")
            except Exception:
                pass

        await asyncio.sleep(CHECK_INTERVAL)


# ---------------- КОМАНДЫ БОТА (ЛИЧКА) ----------------

router = Router()


@router.message(Command("myid"))
async def cmd_myid(message: types.Message):
    if message.chat.type != "private":
        return
    await message.answer(f"🆔 Ваш Telegram ID:\n{message.from_user.id}")


@router.message(Command("plans"))
async def cmd_plans(message: types.Message):
    if message.chat.type != "private":
        return
    if not PLANS:
        await message.answer("❗ Планы ещё не заданы")
        return

    lines = ["📊 Текущие планы:\n"]
    for name, data in PLANS.items():
        if isinstance(data, dict):
            day = data.get("day")
            month = data.get("month")
        else:
            day = data
            month = None
        lines.append(f"{name}: день={day}, месяц={month}")
    await message.answer("\n".join(lines))


def parse_setplan_text(text: str):
    """
    Ожидаемый формат: /setdayplan "Имя Фамилия" 1234
    Возвращает (name, value) или исключение.
    """
    first = text.find('"')
    if first == -1:
        raise ValueError("Нет первой кавычки")
    second = text.find('"', first + 1)
    if second == -1:
        raise ValueError("Нет закрывающей кавычки")
    name = text[first + 1 : second].strip()
    if not name:
        raise ValueError("Пустое имя")
    rest = text[second + 1 :].strip()
    if not rest:
        raise ValueError("Нет числа")
    value_str = rest.split()[0]
    value = float(value_str.replace(",", "."))
    return name, value


@router.message(Command("setdayplan"))
async def cmd_setdayplan(message: types.Message):
    global PLANS
    if message.chat.type != "private":
        return
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет прав на выполнение этой команды")
        return
    try:
        name, value = parse_setplan_text(message.text)
        plan_info = PLANS.get(name, {})
        if not isinstance(plan_info, dict):
            plan_info = {}
        plan_info["day"] = value
        PLANS[name] = plan_info
        with open(PLANS_FILE, "w", encoding="utf-8") as f:
            json.dump(PLANS, f, ensure_ascii=False)
        await message.answer(f"✔ План на день для {name} установлен: {value}")
    except Exception:
        await message.answer(
            "❌ Ошибка команды.\nФормат:\n/setdayplan \"Имя Фамилия\" 400"
        )


@router.message(Command("setmonthplan"))
async def cmd_setmonthplan(message: types.Message):
    global PLANS
    if message.chat.type != "private":
        return
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Недостаточно прав")
        return
    try:
        name, value = parse_setplan_text(message.text)
        plan_info = PLANS.get(name, {})
        if not isinstance(plan_info, dict):
            plan_info = {}
        plan_info["month"] = value
        PLANS[name] = plan_info
        with open(PLANS_FILE, "w", encoding="utf-8") as f:
            json.dump(PLANS, f, ensure_ascii=False)
        await message.answer(f"✔ План на месяц для {name} установлен: {value}")
    except Exception:
        await message.answer(
            "❌ Ошибка команды.\nФормат:\n/setmonthplan \"Имя Фамилия\" 12000"
        )


@router.message(Command("startbot"))
async def cmd_startbot(message: types.Message, bot: Bot):
    global BOT_RUNNING
    if message.chat.type != "private":
        return
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет прав на выполнение этой команды")
        return
    BOT_RUNNING = True
    save_bot_state(True)
    await message.answer("🚀 Бот запущен!")
    try:
        await bot.send_message(CHAT_ID, "Бот работает. Обновление будет через 1 час.")
    except Exception:
        pass


@router.message(Command("stopbot"))
async def cmd_stopbot(message: types.Message):
    global BOT_RUNNING
    if message.chat.type != "private":
        return
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет прав на выполнение этой команды")
        return
    BOT_RUNNING = False
    save_bot_state(False)
    await message.answer("⏹ Бот остановлен")


# ---------------- MAIN ----------------

async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    # фоновый воркер с обновлением баланса
    asyncio.create_task(worker(bot))

    # BOT_RUNNING уже загружен из файла — если там "1", бот сразу начнёт работу
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())







