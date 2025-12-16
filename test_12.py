"""
Тестовое задание №12 (ПЦ МАСТ)
Мониторинг списка умерших на Википедии и отправка email-уведомлений.

"""

import os
import json
import time
import re
import smtplib
import ssl
from pathlib import Path
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Optional, Tuple
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# =============== КОНФИГУРАЦИЯ ===============
YEAR="2025"
MONTH="December"
CONFIG = {
    "WIKI_URL": "https://en.wikipedia.org/wiki/Deaths_in_"+YEAR+"#"+MONTH,
    "CHECK_INTERVAL_SEC": 20, #300, #(5 минут) 3600+ (1 час). для отладки 20 сек между группами по 5
    "DATA_FILE": "seen_deaths.json",
    "EMAIL": {
        "SMTP_SERVER": "smtp.gmail.com",
        "PORT": 587,
        "SENDER": os.getenv("EMAIL_SENDER", "your@gmail.com"),
        "PASSWORD": os.getenv("EMAIL_PASSWORD", "your_app_password"),
        "RECIPIENT": os.getenv("EMAIL_RECIPIENT", "recipient@example.com"),
    },
}
SEEN = [] # список, в котором помещаются просмотренные записи для отсутствия дублирования


def get_wikipedia_page(url: str) -> Optional[str]:
    """Загружает HTML страницы. Возвращает None при ошибке."""
    try:
        headers = {"User-Agent": "WikiDeathMonitor/1.0 (alex@example.com)"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        # logger.error(f"Ошибка загрузки {url}: {e}")
        return None

def get_first_paragraph_clean(url: str) -> str:
    """ Процедура поиска первого абзаца информации о человеке """
    # Получаем HTML
    headers = {"User-Agent": "WikiMonitor/1.0 (contact@example.com)"}
    html = requests.get(url, headers=headers).text
    soup = BeautifulSoup(html, "html.parser")

    # Удаляем шум: инфобоксы, навигацию, шаблоны
    for tag in soup.select(".infobox, .navbox, .hatnote, .metadata, .mw-editsection"):
        tag.decompose()

    # Находим первый <p> с текстом
    p = soup.select_one("#mw-content-text p")
    if not p:
        return "(Первый абзац не найден)"

    text = p.get_text()

    # Очистка от вики-разметки и спецсимволов
    text = re.sub(r"\[\[[^\]]+\|([^\]]+)\]\]", r"\1", text)  # [[A|B]] → B
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)  # [[A]] → A
    text = re.sub(r"\{\{[^}]*\}\}", "", text)  # {{...}} →
    text = re.sub(r"'''|''", "", text)  # жирный/курсив → обычный
    text = re.sub(r"[\u0300-\u036f]", "", text)  # ударения →
    text = re.sub(r"\[\d*\]", '', text)
    text = re.sub(r"\s+", " ", text).strip()  # лишние пробелы → один

    return text

def another_first(page):
    """ Если первая процедура не выдала результат,
    то используется эта процедура получения первого абзаца """
    resp = requests.get(
        f"https://en.wikipedia.org/api/rest_v1/page/summary/{page}",
        headers={"User-Agent": "WikiMonitor/1.0 (contact@example.com)"}
        ).json()
    return resp["extract"]

def extract_deaths_from_list(str):
    """ Поиск умерших с помощью Selenium """
    # Настройка Chrome в headless-режиме (тихо, без окна)
    options = Options()
    options.add_argument("--headless=new")  # современный headless
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=WikiMonitor/1.0 (contact@example.com)")

    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 10)

    try:
        driver.get("https://en.wikipedia.org/wiki/Deaths_in_"+YEAR)

        # Дождаться появления заголовка December
        wait.until(EC.presence_of_element_located((By.ID, MONTH)))

        # Прокрутить к заголовку (иногда нужно для lazy-load)
        dec_header = driver.find_element(By.ID, MONTH)
        driver.execute_script("arguments[0].scrollIntoView();", dec_header)
        time.sleep(1)  # дать отрендериться

        list_items = driver.find_elements(By.TAG_NAME, 'li')
        lst = ['Donate', 'Create account', 'Log in', 'Article', 'Talk', 'Read', 'View source', 'View history',
               'Name, age, country of citizenship at birth, subsequent nationality (if applicable), what subject was noted for, cause of death (if known), and a reference.']

        deaths = []
        hm = 5
        for li in list_items:  # elements:
            if li.text and li.text not in lst:
                if li.text == 'Deaths in January '+YEAR:
                    break
                text = li.text.strip()
                if text and len(text) > 10:  # фильтр пустых
                    text_lst = li.text.split(',')
                    parts = text.split(",", 2)
                    name = parts[0].strip() if len(parts) > 0 else "?"
                    age = parts[1].strip() if len(parts) > 1 else "?"

                    a = li.find_element(By.TAG_NAME, "a")
                    url = a.get_attribute("href") or ""
                    if name not in SEEN:
                        deaths.append({
                            "name": name,
                            "age": age,
                            "url": url,
                            "text": text
                        })
                        SEEN.append(name)
                        # print(f"{SEEN=}")
                        hm -= 1
                        if hm < 1: break

        # print(f"\n Всего найдено: {len(deaths)} записей")
        return deaths
    finally:
        driver.quit()


def get_russian_url_from_html(en_url: str) -> str | None:
    """ Процедура поиска информации о человеке в Wikipedia на русском языке """
    try:
        html = requests.get(en_url, headers={"User-Agent": "WikiMonitor/1.0"}).text
        soup = BeautifulSoup(html, "html.parser")
        ru_link = soup.find("a", hreflang="ru")
        return ru_link["href"] if ru_link else en_url
    except:
        return en_url
def send_email(subject, body):
    """ Процедура отправки письма на основании информации из .env"""

    print(f"{subject=}")
    print(f"{body=}")
    # return # для отладки
    msg = MIMEMultipart()

    sender = CONFIG["EMAIL"]["SENDER"] #sender
    receiver = CONFIG["EMAIL"]["RECIPIENT"] #receiver
    password = CONFIG["EMAIL"]["PASSWORD"]
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = receiver
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender, password)
        text = msg.as_string()
        server.sendmail(sender, receiver, text)
        server.quit()
        print("Письмо успешно отправлено!", subject, text)
    except Exception as e:
        print(f"Ошибка отправки: {e}")


# =============== ОСНОВНОЙ ЦИКЛ ДЕМОНА ===============

def main():
    while True:
        try:

            print(f"{CONFIG['WIKI_URL']=}")
            html = get_wikipedia_page(CONFIG["WIKI_URL"])
            if not html:
                time.sleep(CONFIG["CHECK_INTERVAL_SEC"])
                continue

            current_deaths = extract_deaths_from_list(html)
            print(f"Найдено: {len(current_deaths)}")
            for death in current_deaths:
                # print(death)
                # input('the next')

                ru_link = get_russian_url_from_html(death["url"])
                print('-'*10)
                print(f"{ru_link}")

                # Получаем первый абзац
                intro = get_first_paragraph_clean(ru_link)
                if intro == "":
                    page = death['name']
                    intro = another_first(page)
                print(f"Первый абзац: {intro}")
                # Формируем письмо
                subject = f"Скончался(ась): {death['name']} ({death['age']})"
                body = f"""Новое имя в списке»:

Имя: {death['name']}
Возраст: {death['age']}
Ссылка: {ru_link}

Первый абзац статьи:
«{intro}»

---
С уважением, WikiDeathMonitor.
"""

                send_email(subject, body)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"{e=}")
        print(f'{CONFIG["CHECK_INTERVAL_SEC"]=}')
        time.sleep(CONFIG["CHECK_INTERVAL_SEC"])
    print("The end")

if __name__ == "__main__":
    main()