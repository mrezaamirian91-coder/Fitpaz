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
        normalized.append({
            "name": str(item.get("name", "")).strip(),
            "quantity": str(item.get("quantity", "")).strip(),
            "calories": _safe_int(item.get("calories", 0)),
        })
    data["items"] = normalized
    return data


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


def _call_gemini_vision(photo_b64: str, prompt: str) -> dict:
    image_bytes = base64.b64decode(photo_b64)
    response = _gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            prompt,
        ],
        config=genai_types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return json.loads(_clean_json(response.text))


def _call_openai_text(prompt: str, max_tokens: int = 1500) -> dict:
    response = _openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    return json.loads(_clean_json(response.choices[0].message.content))


def _call_gemini_text(prompt: str) -> dict:
    response = _gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return json.loads(_clean_json(response.text))


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
    """تولید پیشنهاد غذا. خروجی: (داده‌ی JSON, نام مدلی که جواب داد)"""
    if _openai_client:
        try:
            return _call_openai_text(prompt), "openai"
        except Exception as e:
            logger.warning(f"OpenAI recipe generation failed, falling back to Gemini: {e}")

    if _gemini_client:
        return _call_gemini_text(prompt), "gemini"

    raise RuntimeError("هیچ سرویس هوش مصنوعی‌ای پیکربندی نشده (نه OpenAI نه Gemini)")


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
