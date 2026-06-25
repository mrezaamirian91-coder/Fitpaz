"""
لایه‌ی یکپارچه برای صحبت با سرویس‌های هوش مصنوعی.

منطق: اول OpenAI رو امتحان می‌کنه. اگه به هر دلیلی خطا بده
(بی‌پولی، قطعی، محدودیت نرخ و...) خودکار می‌ره سراغ Gemini (رایگان).
این یعنی وقتی OpenAI شارژ نداره، بات بدون توقف از Gemini استفاده می‌کنه،
و وقتی شارژ داشت، خودش برمی‌گرده به OpenAI - بدون نیاز به تغییر دستی کد.
"""

import os
import json
import base64
import logging
import time

from openai import OpenAI
from google import genai
from google.genai import types as genai_types

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

OPENAI_MODEL = "gpt-4o"
GEMINI_MODEL = "gemini-2.5-flash"
IMAGE_MODEL = "gpt-image-1"

# لحظه‌ی "وای" - عکس واقعی غذا. پیش‌فرض خاموشه (پلن رایگان) چون هزینه دارد و رایگان نیست.
# وقتی آماده بودی هزینه‌ش رو بپذیری، فقط توی Railway مقدار ENABLE_RECIPE_IMAGES رو true کن - بدون نیاز به تغییر کد.
ENABLE_RECIPE_IMAGES = os.environ.get("ENABLE_RECIPE_IMAGES", "false").lower() == "true"

_openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
_gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def _clean_json(text: str) -> str:
    return text.replace("```json", "").replace("```", "").strip()


def _safe_int(value, default: int = 0) -> int:
    """عدد کالری رو حتی اگه مدل به‌جای عدد، رشته یا اعداد فارسی برگردونه، امن تبدیل می‌کنه."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        persian_to_english = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
        cleaned = "".join(ch for ch in value.translate(persian_to_english) if ch.isdigit())
        if cleaned:
            return int(cleaned)
    return default


def _normalize_vision_data(data: dict) -> dict:
    """اطمینان از اینکه هر آیتم name/quantity/calories تمیز و قابل‌اعتماد داره."""
    items = data.get("items", [])
    normalized = []
    for item in items:
        entry = {
            "name": str(item.get("name", "")).strip(),
            "quantity": str(item.get("quantity", "")).strip(),
            "calories": _safe_int(item.get("calories", 0)),
            "protein_g": _safe_int(item.get("protein_g", 0)),
            "carbs_g": _safe_int(item.get("carbs_g", 0)),
            "fat_g": _safe_int(item.get("fat_g", 0)),
        }
        box = item.get("box_2d")
        if isinstance(box, list) and len(box) == 4:
            entry["box_2d"] = box
        normalized.append(entry)
    data["items"] = normalized

    totals = data.get("totals") or {}
    if totals:
        data["totals"] = {
            "calories": _safe_int(totals.get("calories", 0)),
            "protein_g": _safe_int(totals.get("protein_g", 0)),
            "carbs_g": _safe_int(totals.get("carbs_g", 0)),
            "fat_g": _safe_int(totals.get("fat_g", 0)),
        }
    elif normalized:
        data["totals"] = {
            "calories": sum(i["calories"] for i in normalized),
            "protein_g": sum(i["protein_g"] for i in normalized),
            "carbs_g": sum(i["carbs_g"] for i in normalized),
            "fat_g": sum(i["fat_g"] for i in normalized),
        }
    return data


WOW_VISION_PROMPT = """این عکس غذا یا مواد غذایی است — ممکن است مواد خام باشد یا یک وعده/بشقاب آماده.
هر ماده یا بخش قابل‌شناسایی را با مقدار تخمینی، کالری و ماکرو (گرم) برگردان.
برای غذاهای ترکیبی ایرانی (مثل قورمه‌سبزی با برنج)، بخش‌های منطقی جدا کن (خورش، برنج، سالاد و...).

موقعیت هر آیتم روی عکس را با box_2d بده: [ymin, xmin, ymax, xmax] نرمال‌شده ۰ تا ۱۰۰۰.
اگر موقعیت دقیق ممکن نیست، box_2d را حذف کن (فقط همان آیتم).

فقط JSON بدون متن اضافه:
{
  "photo_type": "raw_ingredients یا prepared_meal",
  "items": [
    {
      "name": "نام فارسی",
      "quantity": "مقدار تخمینی",
      "calories": عدد_صحیح,
      "protein_g": عدد_صحیح,
      "carbs_g": عدد_صحیح,
      "fat_g": عدد_صحیح,
      "box_2d": [ymin, xmin, ymax, xmax]
    }
  ],
  "totals": {
    "calories": عدد_صحیح,
    "protein_g": عدد_صحیح,
    "carbs_g": عدد_صحیح,
    "fat_g": عدد_صحیح
  },
  "confidence": "high یا medium یا low"
}

مقادیر باید واقع‌بینانه و متناسب با اندازه‌ی دیده‌شده در عکس باشند."""


def _call_openai_vision(photo_b64: str, prompt: str) -> dict:
    response = _openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{photo_b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        max_tokens=500,
    )
    return json.loads(_clean_json(response.choices[0].message.content))


def _call_gemini_vision(photo_b64: str, prompt: str, retries: int = 3) -> dict:
    image_bytes = base64.b64decode(photo_b64)
    last_err = None
    for attempt in range(retries):
        try:
            response = _gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    prompt,
                ],
                config=genai_types.GenerateContentConfig(response_mime_type="application/json"),
            )
            return json.loads(_clean_json(response.text))
        except Exception as e:
            last_err = e
            if attempt < retries - 1 and _is_retryable_gemini_error(e):
                wait = 1.5 * (attempt + 1)
                logger.warning(f"Gemini vision retry {attempt + 1}/{retries} after {wait}s: {e}")
                time.sleep(wait)
                continue
            raise
    raise last_err


def _call_openai_text(prompt: str, max_tokens: int = 1500) -> dict:
    response = _openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    return json.loads(_clean_json(response.choices[0].message.content))


def _is_retryable_gemini_error(exc: Exception) -> bool:
    msg = str(exc).upper()
    return "503" in msg or "UNAVAILABLE" in msg or "429" in msg or "RESOURCE_EXHAUSTED" in msg


def _call_gemini_text(prompt: str, retries: int = 3) -> dict:
    last_err = None
    for attempt in range(retries):
        try:
            response = _gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(response_mime_type="application/json"),
            )
            return json.loads(_clean_json(response.text))
        except Exception as e:
            last_err = e
            if attempt < retries - 1 and _is_retryable_gemini_error(e):
                wait = 1.5 * (attempt + 1)
                logger.warning(f"Gemini text retry {attempt + 1}/{retries} after {wait}s: {e}")
                time.sleep(wait)
                continue
            raise
    raise last_err


def analyze_photo_with_positions(photo_b64: str) -> tuple[dict, str]:
    """تشخیص غذا/مواد با موقعیت روی عکس و ماکرو — فقط Gemini (برای واو مومنت بصری)."""
    if not _gemini_client:
        raise RuntimeError("GEMINI_API_KEY برای تشخیص موقعیت روی عکس لازم است")

    data = _call_gemini_vision(photo_b64, WOW_VISION_PROMPT)
    return _normalize_vision_data(data), "gemini"


def analyze_photo(photo_b64: str) -> tuple[dict, str]:
    """تشخیص مواد غذایی از روی عکس با مقدار و کالری هر ماده.
    خروجی: (داده‌ی JSON, نام مدلی که جواب داد)"""
    prompt = (
        "این عکس از مواد غذایی است. "
        "هر ماده‌ای که می‌بینی را با مقدار تخمینی و کالری همان مقدار به فارسی برگردان. "
        "فقط JSON بده بدون توضیح اضافه. "
        "فرمت دقیق:\n"
        '{"items": [{"name": "نام ماده", "quantity": "تعداد یا مقدار", "calories": عدد_صحیح}], '
        '"confidence": "high/medium/low"}\n\n'
        "مثال:\n"
        '{"items": [{"name": "تخم‌مرغ", "quantity": "۲ عدد", "calories": 140}, '
        '{"name": "قارچ", "quantity": "۵ عدد متوسط", "calories": 50}], "confidence": "high"}\n\n'
        "مقادیر کالری باید واقع‌بینانه و بر اساس مقدار تخمینی در عکس باشد."
    )

    data = None
    provider = None

    if _openai_client:
        try:
            data, provider = _call_openai_vision(photo_b64, prompt), "openai"
        except Exception as e:
            logger.warning(f"OpenAI vision failed, falling back to Gemini: {e}")

    if data is None and _gemini_client:
        data, provider = _call_gemini_vision(photo_b64, prompt), "gemini"

    if data is None:
        raise RuntimeError("هیچ سرویس هوش مصنوعی‌ای پیکربندی نشده (نه OpenAI نه Gemini)")

    return _normalize_vision_data(data), provider


def generate_recipes(prompt: str) -> tuple[dict, str]:
    """تولید پیشنهاد غذا — اول Gemini (رایگان)، بعد OpenAI."""
    if _gemini_client:
        try:
            return _call_gemini_text(prompt), "gemini"
        except Exception as e:
            logger.warning(f"Gemini recipe generation failed, falling back to OpenAI: {e}")

    if _openai_client:
        try:
            return _call_openai_text(prompt), "openai"
        except Exception as e:
            logger.warning(f"OpenAI recipe generation failed: {e}")

    raise RuntimeError("سرویس هوش مصنوعی در دسترس نیست. چند دقیقه دیگه دوباره امتحان کن.")


def generate_recipe_image(recipe_name: str) -> bytes | None:
    """ساخت یک عکس واقعی و وسوسه‌انگیز از غذای پیشنهادی - لحظه‌ی "وای".

    این فیچر پشت فلگ ENABLE_RECIPE_IMAGES است. تا وقتی این فلگ روی Railway به true
    تغییر نکنه، این تابع همیشه None برمی‌گردونه و هیچ درخواست/هزینه‌ای ایجاد نمی‌شه
    (یعنی همین الان توی "پلن رایگان" هستیم، ولی کد آماده‌ی روشن شدنه)."""
    if not ENABLE_RECIPE_IMAGES:
        return None

    if not _openai_client:
        logger.warning("ENABLE_RECIPE_IMAGES فعاله ولی OPENAI_API_KEY تنظیم نشده.")
        return None

    try:
        prompt = (
            f"یک عکس فوق‌العاده واقعی و وسوسه‌انگیز از غذای ایرانی «{recipe_name}»، "
            "به سبک عکاسی حرفه‌ای رستورانی، نور طبیعی و گرم، روی میز چوبی، از زاویه‌ی کمی از بالا، "
            "بدون متن و بدون لوگو روی عکس."
        )
        result = _openai_client.images.generate(
            model=IMAGE_MODEL,
            prompt=prompt,
            size="1024x1024",
            quality="low",
        )
        return base64.b64decode(result.data[0].b64_json)
    except Exception as e:
        logger.warning(f"Recipe image generation failed: {e}")
        return None
