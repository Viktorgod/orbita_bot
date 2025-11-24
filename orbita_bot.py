import asyncio
import os
import json
from datetime import datetime

from aiogram import Bot
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID_ENV = os.getenv("CHAT_ID")
ORBITA_LOGIN = os.getenv("ORBITA_LOGIN")
ORBITA_PASSWORD = os.getenv("ORBITA_PASSWORD")
PLAN_DAY = float(os.getenv("PLAN_DAY", "2000"))

CHECK_INTERVAL = 3600
HISTORY_FILE = "last.json"

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

def save_last(values: dict):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(values, f, ensure_ascii=False)

def load_last() -> dict:
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def create_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1600,900")
    return webdriver.Chrome(options=options)

# ------------------------------------------------------------
# ПАРСИНГ БАЛАНСА
# ------------------------------------------------------------

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
    now = datetime.now()
    today_str = f"{now.day:02d}"
    month_str = f"{now.month:02d}"

    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "table")))
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

    pairs.sort(key=lambda x: x[1], reverse=True)
    total = sum(val for _, val in pairs)

    medals = ["🥇", "🥈", "🥉"]
    lines = [f"📊 Баланс за {today_str}.{month_str}\n"]

    for i, (name, val) in enumerate(pairs):
        medal = medals[i] if i < 3 else "▫️"
        lines.append(f"{medal} {name:<20} — {val}")

    lines.append(f"\n💰 Итого: {total}")

    return "\n".join(lines), dict(pairs)

# ------------------------------------------------------------
# ПАРСИНГ ДЕЙСТВИЙ ПО АДМИНАМ
# ------------------------------------------------------------

def parse_actions_efficiency(driver):
    wait = WebDriverWait(driver, 30)

    # устанавливаем период Сегодня—Сегодня
    today = datetime.today().strftime("%Y-%m-%d")
    period_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='period']")))
    period_input.clear()
    period_input.send_keys(f"{today} - {today}")

    # выбираем metrica = "Сгенерировано действий"
    metric_select = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "select[name='metric']")))
    for option in metric_select.find_elements(By.TAG_NAME, "option"):
        if "Сгенерировано действий" in option.text:
            option.click()
            break

    # жмем ПОИСК
    search_btn = driver.find_element(By.XPATH, "//button[contains(text(), 'Поиск')]")
    search_btn.click()

    wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
    rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")

    admins = {}
    for row in rows:
        cols = row.find_elements(By.TAG_NAME, "td")
        if len(cols) < 4:
            continue

        translator = cols[1].text.strip()
        admin = cols[2].text.strip()
        actions24 = cols[3].text.strip()

        try:
            num = float(actions24.replace(",", "."))
        except:
            num = 0.0

        if admin not in admins:
            admins[admin] = 0.0
        admins[admin] += num

    return admins

# ------------------------------------------------------------
# ДАННЫЕ
# ------------------------------------------------------------

def login_and_get_data():
    driver = create_driver()
    wait = WebDriverWait(driver, 30)
    try:
        driver.get("https://orbita.life/login")
        email_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email'], input[name='email'], input[name='login']")))
        pwd_input = driver.find_element(By.CSS_SELECTOR, "input[type='password'], input[name='password']")
        email_input.clear()
        email_input.send_keys(ORBITA_LOGIN)
        pwd_input.clear()
        pwd_input.send_keys(ORBITA_PASSWORD)
        btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], button.btn-primary")
        btn.click()
        wait.until(lambda d: "login" not in d.current_url.lower())

        driver.get("https://orbita.life")
        balance_text, balance_values = parse_balance_table(driver)

        driver.get("https://orbita.life/statistics/efficiency/operators/")
        admins_actions = parse_actions_efficiency(driver)

        combined_data = {
            "balance": balance_values,
            "admins": admins_actions
        }

        return balance_text, combined_data

    finally:
        driver.quit()

# ------------------------------------------------------------
# TELEGRAM
# ------------------------------------------------------------

async def send_long(bot: Bot, chat_id: int, text: str):
    if len(text) <= 4000:
        await bot.send_message(chat_id, text)
        return
    for i in range(0, len(text), 4000):
        await bot.send_message(chat_id, text[i : i + 4000])

# ------------------------------------------------------------
# MAIN LOOP
# ------------------------------------------------------------

async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    try:
        while True:
            try:
                balance_text, combined_data = login_and_get_data()

                last_values = load_last()
                last_balances = last_values.get("balance", {})
                last_admins = last_values.get("admins", {})

                total_balance_diff = 0
                for name, val in combined_data["balance"].items():
                    old = last_balances.get(name, val)
                    total_balance_diff += round(val - old, 2)

                total_actions_diff = 0
                for admin, val in combined_data["admins"].items():
                    old = last_admins.get(admin, val)
                    total_actions_diff += round(val - old, 2)

                save_last(combined_data)

                if total_balance_diff > 0:
                    total_balance_text = f"💰 Общий прирост баланса за час: +{total_balance_diff}"
                elif total_balance_diff < 0:
                    total_balance_text = f"💰 Общий спад баланса за час: {total_balance_diff}"
                else:
                    total_balance_text = f"💰 Баланс без изменений за час"

                if total_actions_diff > 0:
                    total_actions_text = f"🕹 Общий прирост действий за час: +{total_actions_diff}"
                elif total_actions_diff < 0:
                    total_actions_text = f"🕹 Общий спад действий за час: {total_actions_diff}"
                else:
                    total_actions_text = f"🕹 Действия без изменений за час"

                sorted_admins = sorted(combined_data["admins"].items(), key=lambda x: x[1], reverse=True)

                admins_lines = []
                for admin, total in sorted_admins:
                    old = last_admins.get(admin, total)
                    diff = round(total - old, 2)
                    admins_lines.append(f"{admin} — {total} ({diff:+})")

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                full_text = (
                    f"⏰ Обновление ORBITA ({now_str})\n\n"
                    f"{total_balance_text}\n"
                    f"{total_actions_text}\n\n"
                    f"{balance_text}\n\n"
                    "🕹 Действия по администраторам (за сегодня):\n" + "\n".join(admins_lines)
                )

                await send_long(bot, CHAT_ID, full_text)

            except Exception as e:
                await bot.send_message(CHAT_ID, f"❌ Error:\n{e}")

            await asyncio.sleep(CHECK_INTERVAL)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())





