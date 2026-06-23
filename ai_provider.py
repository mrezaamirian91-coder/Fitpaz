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

_openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
_gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def _clean_json(text: str) -> str:
    return text.replace("```json", "").replace("```", "").strip()


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
    """تشخیص مواد غذایی از روی عکس. خروجی: (داده‌ی JSON, نام مدلی که جواب داد)"""
    prompt = (
        "این عکس از مواد غذایی است. لیست مواد قابل تشخیص را به فارسی "
        "و به صورت JSON برگردان. فقط JSON بده بدون توضیح اضافه. "
        'فرمت: {"ingredients": ["ماده۱", "ماده۲"], "confidence": "high/medium/low"}'
    )

    if _openai_client:
        try:
            return _call_openai_vision(photo_b64, prompt), "openai"
        except Exception as e:
            logger.warning(f"OpenAI vision failed, falling back to Gemini: {e}")

    if _gemini_client:
        return _call_gemini_vision(photo_b64, prompt), "gemini"

    raise RuntimeError("هیچ سرویس هوش مصنوعی‌ای پیکربندی نشده (نه OpenAI نه Gemini)")


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
