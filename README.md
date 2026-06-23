# فیت پز — بات تلگرام

## مراحل راه‌اندازی

### ۱. فایل‌ها را روی GitHub آپلود کن

یک ریپو جدید در GitHub بساز و این فایل‌ها را push کن.
فایل .env را آپلود نکن — داخل Railway وارد می‌شود.

### ۲. در Railway دیپلوی کن

- وارد railway.app شو
- گزینه New Project را بزن
- Deploy from GitHub repo را انتخاب کن
- ریپوی فیت پز را انتخاب کن

### ۳. متغیرهای محیطی را در Railway وارد کن

در بخش Variables این موارد را اضافه کن:

TELEGRAM_TOKEN = توکن بات تلگرام
OPENAI_API_KEY = کلید OpenAI

### ۴. نوع سرویس را تنظیم کن

در Railway روی سرویس کلیک کن و در Settings
نوع آن را از Web Service به Worker تغییر بده.

## ساختار فایل‌ها

- bot.py — کد اصلی بات
- requirements.txt — کتابخانه‌های مورد نیاز
- Procfile — دستور اجرا برای Railway
- .env.example — نمونه متغیرهای محیطی
