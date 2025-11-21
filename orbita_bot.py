import asyncio
import os
from datetime import datetime

from aiogram import Bot
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
ORBITA_LOGIN = os.getenv("ORBITA_LOGIN")
ORBITA_PASSWORD = os.getenv("ORBITA_PASSWORD")

CHECK_INTERVAL = 10


def create_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1600,900")
    driver = webdriver.Chrome(options=options)
    return driver


def find_today_column(table):
    today_str = f"{datetime.now().day:02d}"

    rows = table.find_elements(By.TAG_NAME, "tr")

    for row in rows:
        ths = row.find_elements(By.TAG_NAME, "th")
        if not ths:
            continue

        for idx, th in enumerate(ths):
            aria = th.get_attribute("aria-label") or ""
            txt = th.text.strip()

            if aria.startswith(today_str + ":"):
                return idx

            if txt == today_str:
                return idx

    return None


def parse_balance_table(driver):
    now = datetime.now()
    today_str = f"{now.day:02d}"
    month_str = f"{now.month:02d}"

    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.TAG_NAME, "table"))
    )

    table = driver.find_element(By.TAG_NAME, "table")

    today_col = find_today_column(table)
    if today_col is None:
        return f"❌ Не найден столбец с датой {today_str}"

    rows = table.find_elements(By.TAG_NAME, "tr")

    pairs = []

    for row in rows:
        ths = row.find_elements(By.TAG_NAME, "th")
        if not ths:
            continue

        name = ths[0].text.strip()
        lname = name.lower()

        if not name:
            continue
        if "всего" in lname or "итого" in lname or "администратор" in lname:
            continue
        if name[0].isdigit():
            continue
        if len(name.split()) < 2:
            continue

        tds = row.find_elements(By.TAG_NAME, "td")

        if len(tds) <= today_col:
            continue

        value = tds[today_col].text.strip() or "0"

        try:
            numeric_value = float(value.replace(",", "."))
        except:
            numeric_value = 0

        pairs.append((name, numeric_value))

    pairs.sort(key=lambda x: x[1], reverse=True)

    result = [f"📊 Баланс за {today_str}.{month_str}\n"]
    for name, val in pairs:
        result.append(f"{name}: {val}")

    return "\n".join(result)


def login_and_get_balance_text():
    driver = create_driver()
    wait = WebDriverWait(driver, 20)

    try:
        driver.get("https://orbita.life/login")

        email_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                "input[type='email'], input[name='email'], input[name='login']"))
        )
        password_input = driver.find_element(
            By.CSS_SELECTOR,
            "input[type='password'], input[name='password']"
        )

        email_input.clear()
        email_input.send_keys(ORBITA_LOGIN)
        password_input.clear()
        password_input.send_keys(ORBITA_PASSWORD)

        login_btn = driver.find_element(
            By.CSS_SELECTOR,
            "button[type='submit'], button.btn-primary"
        )
        login_btn.click()

        wait.until(lambda d: "login" not in d.current_url.lower())

        driver.get("https://orbita.life")

        return parse_balance_table(driver)

    finally:
        driver.quit()


async def send_long(bot: Bot, chat_id: int, text: str):
    max_len = 4000
    if len(text) <= max_len:
        await bot.send_message(chat_id, text)
        return

    for i in range(0, len(text), max_len):
        await bot.send_message(chat_id, text[i:i + max_len])


async def main_loop():
    bot = Bot(token=TELEGRAM_TOKEN)

    while True:
        try:
            balance = login_and_get_balance_text()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            msg = f"⏰ Обновление ORBITA ({now_str})\n\n{balance}"

            await send_long(bot, CHAT_ID, msg)

        except Exception as e:
            await bot.send_message(CHAT_ID, f"❌ Ошибка:\n{e}")

        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main_loop())
