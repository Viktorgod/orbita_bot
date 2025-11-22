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
CHAT_ID_ENV = os.getenv("CHAT_ID")
ORBITA_LOGIN = os.getenv("ORBITA_LOGIN")
ORBITA_PASSWORD = os.getenv("ORBITA_PASSWORD")

CHECK_INTERVAL = 10  # 1 hour

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
    now = datetime.now()
    today_str = f"{now.day:02d}"
    month_str = f"{now.month:02d}"

    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.TAG_NAME, "table"))
    )
    table = driver.find_element(By.TAG_NAME, "table")
    today_col = find_today_column(table)
    if today_col is None:
        return f"❌ Column for {today_str}.{month_str} not found"

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
        except:
            num_value = 0.0
        pairs.append((name, num_value))

    if not pairs:
        return f"No data for {today_str}.{month_str}"

    pairs.sort(key=lambda x: x[1], reverse=True)
    lines = [f"📊 Баланс за {today_str}.{month_str}
"]
    lines += [f"{n}: {v}" for n, v in pairs]
    return "
".join(lines)

def login_and_get_balance_text():
    driver = create_driver()
    wait = WebDriverWait(driver, 30)
    try:
        driver.get("https://orbita.life/login")
        email_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email'], input[name='email'], input[name='login']")))
        pwd_input = driver.find_element(By.CSS_SELECTOR, "input[type='password'], input[name='password']")
        email_input.clear(); email_input.send_keys(ORBITA_LOGIN)
        pwd_input.clear(); pwd_input.send_keys(ORBITA_PASSWORD)
        btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], button.btn-primary")
        btn.click()
        wait.until(lambda d: "login" not in d.current_url.lower())
        driver.get("https://orbita.life")
        return parse_balance_table(driver)
    finally:
        driver.quit()

async def send_long(bot: Bot, chat_id: int, text: str):
    if len(text) <= 4000:
        await bot.send_message(chat_id, text)
        return
    for i in range(0, len(text), 4000):
        await bot.send_message(chat_id, text[i:i+4000])

async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    try:
        while True:
            try:
                t = login_and_get_balance_text()
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                msg = f"⏰ Update ({now_str})

{t}"
                await send_long(bot, CHAT_ID, msg)
            except Exception as e:
                await bot.send_message(CHAT_ID, f"❌ Error:
{e}")
            await asyncio.sleep(CHECK_INTERVAL)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
