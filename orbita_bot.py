import asyncio
import os
from datetime import datetime

from aiogram import Bot
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ================== НАСТРОЙКИ И ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ==================

TELEGRAM_TOKEN = os.getenv("8358247520:AAFndGUOPZy6wQypQfLBY0mkvBfFYOk3IqA")
CHAT_ID_ENV = os.getenv("-5070917129")
ORBITA_LOGIN = os.getenv("Gospodinov_TOP")
ORBITA_PASSWORD = os.getenv("CCDabhG9BF")

# раз в час
CHECK_INTERVAL = 10


def validate_env():
    """Проверяем, что все нужные переменные окружения заданы."""
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не задан в переменных окружения")

    if not CHAT_ID_ENV:
        raise RuntimeError("CHAT_ID не задан в переменных окружения")

    try:
        chat_id_int = int(CHAT_ID_ENV)
    except ValueError:
        raise RuntimeError(f"CHAT_ID должен быть числом, сейчас: {CHAT_ID_ENV!r}")

    if not ORBITA_LOGIN or not ORBITA_PASSWORD:
        raise RuntimeError("ORBITA_LOGIN или ORBITA_PASSWORD не заданы")

    return chat_id_int


CHAT_ID = validate_env()


# ================== SELENIUM / CHROMIUM ==================

def create_driver() -> webdriver.Chrome:
    """Создаём headless Chromium/Chrome для Railway."""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1600,900")

    # На Railway CHROME_BIN устанавливается в Dockerfile
    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin

    driver = webdriver.Chrome(options=options)
    return driver


def find_today_column(table) -> int | None:
    """
    Ищем индекс колонки для сегодняшней даты.
    В шапке таблицы даты лежат в <th>, число в aria-label:
    aria-label="21: активировать для сортировки столбца..."
    """
    today_str = f"{datetime.now().day:02d}"

    rows = table.find_elements(By.TAG_NAME, "tr")

    for row in rows:
        ths = row.find_elements(By.TAG_NAME, "th")
        if not ths:
            continue

        for idx, th in enumerate(ths):
            aria = th.get_attribute("aria-label") or ""
            txt = th.text.strip()

            # aria-label имеет вид "21: ..."
            if aria.startswith(today_str + ":"):
                return idx

            # запасной вариант — дата прямо в тексте
            if txt == today_str:
                return idx

    return None


def parse_balance_table(driver) -> str:
    """
    Парсим таблицу Баланс:
    - находим колонку с сегодняшней датой
    - собираем сотрудников и их значения
    - сортируем по убыванию
    - возвращаем готовый текст
    """
    now = datetime.now()
    today_str = f"{now.day:02d}"
    month_str = f"{now.month:02d}"

    # ждём появления таблицы
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.TAG_NAME, "table"))
    )
    table = driver.find_element(By.TAG_NAME, "table")

    # 1. колонка сегодняшнего дня
    today_col = find_today_column(table)
    if today_col is None:
        return f"❌ Не найден столбец с датой {today_str}.{month_str}"

    rows = table.find_elements(By.TAG_NAME, "tr")

    # 2. собираем пары (имя, число)
    pairs: list[tuple[str, float]] = []

    for row in rows:
        # имена в <th>
        ths = row.find_elements(By.TAG_NAME, "th")
        if not ths:
            continue

        name = ths[0].text.strip()
        if not name:
            continue

        lname = name.lower()

        # пропускаем строки 'всего', 'итого', админов и т.п.
        if "всего" in lname or "итого" in lname or "администратор" in lname:
            continue

        # иногда в шапке могут быть числа
        if name[0].isdigit():
            continue

        # простая проверка, что похоже на ФИО
        if len(name.split()) < 2:
            continue

        # значения — в <td>
        tds = row.find_elements(By.TAG_NAME, "td")
        if len(tds) <= today_col:
            continue

        value_text = tds[today_col].text.strip() or "0"

        # пробуем привести к числу
        try:
            num_value = float(value_text.replace(",", "."))
        except ValueError:
            # если вдруг не число — считаем нулём
            num_value = 0.0

        pairs.append((name, num_value))

    if not pairs:
        return f"ℹ Нет данных по сотрудникам за {today_str}.{month_str}"

    # сортируем по убыванию
    pairs.sort(key=lambda x: x[1], reverse=True)

    # формируем вывод
    lines = [f"📊 Баланс за {today_str}.{month_str}\n"]
    for name, val in pairs:
        # можно форматировать до 2 знаков после запятой
        lines.append(f"{name}: {val}")

    return "\n".join(lines)


def login_and_get_balance_text() -> str:
    """
    Логинимся на orbita.life, открываем главную страницу и
    возвращаем текст отчёта по балансу.
    """
    driver = create_driver()
    wait = WebDriverWait(driver, 30)

    try:
        # страница логина
        driver.get("https://orbita.life/login")

        # поля логина и пароля
        email_input = wait.until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    "input[type='email'], input[name='email'], input[name='login']",
                )
            )
        )
        password_input = driver.find_element(
            By.CSS_SELECTOR, "input[type='password'], input[name='password']"
        )

        email_input.clear()
        email_input.send_keys(ORBITA_LOGIN)

        password_input.clear()
        password_input.send_keys(ORBITA_PASSWORD)

        # кнопка входа
        login_button = driver.find_element(
            By.CSS_SELECTOR, "button[type='submit'], button.btn-primary"
        )
        login_button.click()

        # ждём, пока уйдём с /login
        wait.until(lambda d: "login" not in d.current_url.lower())

        # после логина сразу открываем главную (если не попали туда автоматически)
        driver.get("https://orbita.life")

        # парсим таблицу
        return parse_balance_table(driver)

    finally:
        driver.quit()


# ================== TELEGRAM / AIROGRAM ==================

async def send_long(bot: Bot, chat_id: int, text: str):
    """Отправляет длинные сообщения частями, чтобы не упираться в лимит Telegram."""
    max_len = 4000
    if len(text) <= max_len:
        await bot.send_message(chat_id, text)
        return

    for i in range(0, len(text), max_len):
        await bot.send_message(chat_id, text[i : i + max_len])


async def main():
    bot = Bot(token=TELEGRAM_TOKEN)

    try:
        while True:
            try:
                balance_text = login_and_get_balance_text()
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                full_text = f"⏰ Обновление ORBITA ({now_str})\n\n{balance_text}"

                await send_long(bot, CHAT_ID, full_text)

            except Exception as e:
                # Ловим любые ошибки, чтобы бот не падал
                err_text = f"❌ Ошибка при парсинге ORBITA:\n{e}"
                try:
                    await bot.send_message(CHAT_ID, err_text)
                except Exception:
                    # Если даже сюда не можем отправить — просто печатаем в лог
                    print(err_text)

            # ждём час до следующего обновления
            await asyncio.sleep(CHECK_INTERVAL)

    finally:
        # корректно закрываем сессию бота
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
