import os
import json
import base64
import logging
import httpx
from datetime import date

logger = logging.getLogger(__name__)

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")

PARSE_PROMPT = """Проаналізуй зображення касового чеку. Поверни ТІЛЬКИ валідний JSON без markdown та без зайвого тексту.

Формат відповіді:
{
  "store": "назва магазину",
  "date": "YYYY-MM-DD або null",
  "time": "HH:MM або null",
  "items": [{"name": "назва", "qty": 1, "price": 9.99}],
  "total": 19.98
}

Правила перекладу назв товарів:
- Загальні назви продуктів (фрукти, овочі, м'ясо, напої, хліб, молоко тощо) — перекладай УКРАЇНСЬКОЮ
- Румунські назви продуктів (lapte=молоко, pâine=хліб, carne=м'ясо, bere=пиво, apă=вода, ouă=яйця, unt=масло, zahăr=цукор, ulei=олія, brânză=сир тощо) — перекладай УКРАЇНСЬКОЮ
- Назви брендів, торгові марки (Philips, Samsung, Coca-Cola, Pepsi, Carlsberg, Tymbark тощо) — ЗАЛИШАЙ як є
- Якщо назва містить і бренд і тип товару — залиш бренд, тип переклади: "Carlsberg bere" → "Carlsberg пиво"
- Якщо сумніваєшся — залиш оригінал (краще бренд ніж неправильний переклад)

Правила підрахунку:
- qty — кількість одиниць цього товару (якщо один товар пробито 3 рази — qty=3, price=ціна за одиницю)
- total — ФІНАЛЬНА сума після знижок (шукай TOTAL/ИТОГ/РАЗОМ/SUMA/TOTAL DE PLATĂ)
- Поля яких не видно — null
- Товари без ціни не включай
- Повертай ТІЛЬКИ JSON, нічого більше"""


def _clean_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1
        end   = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text  = "\n".join(lines[start:end])
    data = json.loads(text)
    if "total" not in data:
        raise ValueError("відсутнє поле 'total' у відповіді OCR")
    if not data.get("date"):
        data["date"] = str(date.today())
    if not isinstance(data.get("items"), list):
        data["items"] = []
    for item in data["items"]:
        item["price"] = float(item.get("price") or 0)
        item["qty"]   = int(item.get("qty") or 1)
        if item["qty"] < 1:
            item["qty"] = 1
    data["total"] = float(data["total"])
    return data


async def parse_receipt(image_bytes: bytes) -> dict | None:
    """Розпізнати чек через Mistral Pixtral."""
    if not MISTRAL_API_KEY:
        raise ValueError(
            "❌ MISTRAL_API_KEY не встановлено!\n"
            "Додай його у файл .env:\n"
            "MISTRAL_API_KEY=твій_ключ\n\n"
            "Отримай безкоштовно на console.mistral.ai"
        )

    mime    = "image/png" if image_bytes[:4] == b'\x89PNG' else "image/jpeg"
    b64data = base64.standard_b64encode(image_bytes).decode()

    payload = {
        "model": "pixtral-12b-2409",
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{b64data}"}},
                {"type": "text", "text": PARSE_PROMPT},
            ]
        }]
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {MISTRAL_API_KEY}",
                         "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.TimeoutException:
        raise ValueError("⏱ Час очікування вийшов (>60с). Перевір інтернет-з'єднання.")
    except httpx.ConnectError:
        raise ValueError("🌐 Немає з'єднання з Mistral API. Перевір інтернет.")

    if resp.status_code == 401:
        raise ValueError("🔑 Невірний MISTRAL_API_KEY. Перевір ключ у .env")
    if resp.status_code == 429:
        raise ValueError("⚠️ Ліміт запитів Mistral вичерпано. Спробуй за хвилину.")
    if resp.status_code == 413:
        raise ValueError("📷 Фото занадто велике. Спробуй стисніше зображення.")
    if resp.status_code != 200:
        raise ValueError(f"❌ Mistral відповів помилкою {resp.status_code}:\n{resp.text[:200]}")

    result = resp.json()
    text   = result["choices"][0]["message"]["content"]

    try:
        data = _clean_json(text)
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        raise ValueError(
            f"⚠️ Mistral не зміг розпізнати чек як JSON.\n"
            f"Причина: {e}\n"
            f"Спробуй чіткіше фото або інший ракурс."
        )

    logger.info(f"OCR: Mistral ✓  store={data.get('store')}  total={data.get('total')}  items={len(data.get('items',[]))}")
    return data