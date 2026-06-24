import os
import base64
import logging
import random
from datetime import time as dt_time, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
import db
import ai_provider

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANCHOR_TIME_UTC = os.environ.get("ANCHOR_TIME_UTC", "14:30")  # پیش‌فرض ~۱۸:۰۰ به وقت ایران
SHARE_DELAY_HOURS = float(os.environ.get("SHARE_DELAY_HOURS", "2"))


def _get_or_assign_share_variant(user_id: int, user: dict) -> str:
    """تست A/B زمان‌بندی دعوت اشتراک‌گذاری - هر کاربر یه بار تصادفی انتخاب می‌شه
    و از اون به بعد همیشه همون واریانت رو می‌بینه."""
    variant = user.get("share_variant")
    if not variant:
        variant = random.choice(["immediate", "delayed"])
        db.update_user(user_id, share_variant=variant)
    return variant


async def _edit_message(query, text, reply_markup=None, parse_mode="Markdown"):
    """ادیت امن پیام فعلی - چه پیام متنی باشه چه عکس با کپشن (وقتی فیچر عکس روشنه)."""
    if query.message.photo:
        await query.edit_message_caption(caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
    else:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)


# ---------- شروع و پروفایل ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    # اگر کاربر قبلاً پروفایل کامل دارد و مدتی غایب بوده -> نقطه شکست
    days_gone = db.days_since_last_active(user_id)

    if user["step"] == "ready" and days_gone >= 7:
        await handle_long_absence_return(update, context, days_gone)
        return

    if user["step"] == "ready" and days_gone < 7:
        # بازگشت بدون قضاوت - مستقیم برو سراغ فلو روزانه
        await update.message.reply_text(
            "خوش اومدی! 👋\n\nاز مواد غذایی که الان داری عکس بفرست 📸"
        )
        return

    # کاربر جدید -> شروع پروفایل
    db.update_user(user_id, step="ask_goal")

    keyboard = [
        [
            InlineKeyboardButton("کاهش وزن 🔽", callback_data="goal_lose"),
            InlineKeyboardButton("عضله‌سازی 💪", callback_data="goal_muscle"),
        ],
        [
            InlineKeyboardButton("حفظ وزن ⚖️", callback_data="goal_maintain"),
            InlineKeyboardButton("فقط غذا بپزم 🍳", callback_data="goal_none"),
        ],
    ]
    await update.message.reply_text(
        "سلام! 👋\n\nخوش اومدی به فیت پز.\n\nاول بگو هدفت چیه؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_long_absence_return(update: Update, context: ContextTypes.DEFAULT_TYPE, days_gone: int):
    """پله ۸ از فلو - نقطه شکست برای غیبت بیشتر از ۷ روز.
    بدون قضاوت، فقط کالیبره دوباره."""
    user_id = update.effective_user.id
    db.update_user(user_id, step="ask_goal")

    keyboard = [
        [
            InlineKeyboardButton("کاهش وزن 🔽", callback_data="goal_lose"),
            InlineKeyboardButton("عضله‌سازی 💪", callback_data="goal_muscle"),
        ],
        [
            InlineKeyboardButton("حفظ وزن ⚖️", callback_data="goal_maintain"),
            InlineKeyboardButton("فقط غذا بپزم 🍳", callback_data="goal_none"),
        ],
    ]
    await update.message.reply_text(
        "خوش اومدی 👋\n\n"
        "تو یه چرخه بزرگ این چند روز تقریباً دیده نمی‌شه.\n"
        "از امروز ادامه بدیم.\n\n"
        "بگو هدفت الان چیه؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    goal_map = {
        "goal_lose": "کاهش وزن",
        "goal_muscle": "عضله‌سازی",
        "goal_maintain": "حفظ وزن",
        "goal_none": "بدون هدف خاص",
    }
    goal = goal_map.get(query.data, "بدون هدف خاص")
    db.update_user(user_id, goal=goal, step="ask_restriction")

    keyboard = [
        [
            InlineKeyboardButton("وگان 🌱", callback_data="rest_vegan"),
            InlineKeyboardButton("بدون گلوتن 🚫", callback_data="rest_gluten"),
        ],
        [
            InlineKeyboardButton("بدون لبنیات 🥛", callback_data="rest_dairy"),
            InlineKeyboardButton("محدودیتی ندارم ✅", callback_data="rest_none"),
        ],
    ]
    await query.edit_message_text(
        f"هدفت: {goal} ✅\n\nمحدودیت غذایی داری؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = db.get_user(user_id)

    restriction_map = {
        "rest_vegan": "وگان",
        "rest_gluten": "بدون گلوتن",
        "rest_dairy": "بدون لبنیات",
        "rest_none": None,
    }
    restriction = restriction_map.get(query.data)
    restrictions = user["restrictions"]
    if restriction and restriction not in restrictions:
        restrictions.append(restriction)

    db.update_user(user_id, restrictions=restrictions, step="ask_anchor_permission")

    keyboard = [
        [
            InlineKeyboardButton("بله، یادم بده ✅", callback_data="anchor_yes"),
            InlineKeyboardButton("نه، فعلاً نه ❌", callback_data="anchor_no"),
        ]
    ]
    await query.edit_message_text(
        "عالیه! تقریباً تمومه 🎉\n\n"
        "می‌خوای هر روز یه پیام کوتاه بفرستم و بپرسم چی خونه داری؟\n"
        "(هر وقت خواستی می‌تونی خاموشش کنی)",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_anchor_permission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پله ۵ از فلو - گرفتن اجازه صریح برای لنگر روزانه. حفظ مالکیت فردی کاربر."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    allow = 1 if query.data == "anchor_yes" else 0
    db.update_user(user_id, allow_daily_anchor=allow, step="ready")

    user = db.get_user(user_id)
    restrictions_text = "، ".join(user["restrictions"]) if user["restrictions"] else "ندارم"

    keyboard = [[InlineKeyboardButton("📸 عکس بگیر از مواد", callback_data="take_photo")]]
    await query.edit_message_text(
        f"پروفایلت آماده شد 🎉\n\n"
        f"هدف: {user['goal']}\n"
        f"محدودیت: {restrictions_text}\n\n"
        f"حالا از مواد غذایی که داری عکس بفرست تا بهت بگم چی بپزی! 📸",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ---------- پردازش عکس و پیشنهاد غذا ----------

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    # اگه منتظر عکس غذای پخته‌شده (پاسخ به دعوت اشتراک‌گذاری) بودیم، این عکس رو جدا پردازش کن
    pending_invite = db.get_pending_share_invite(user_id)
    if pending_invite:
        photo_file_id = update.message.photo[-1].file_id
        db.mark_share_responded(pending_invite["id"], photo_file_id=photo_file_id)
        await update.message.reply_text(
            "چه عکس قشنگی! 😍\n\n"
            "به‌زودی همینجا یه کارت باحال هم برات می‌سازیم که بتونی به اشتراک بگذاری. "
            "فعلاً همین لذت بردن از غذات کافیه 🍽️"
        )
        return

    wait_msg = await update.message.reply_text("داریم مواد رو بررسی می‌کنیم... 🔍")

    try:
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        photo_b64 = base64.b64encode(photo_bytes).decode("utf-8")

        await wait_msg.edit_text("چند تا ایده جالب داریم برات... 🍽️")

        vision_data, vision_provider = ai_provider.analyze_photo(photo_b64)
        items = vision_data.get("items", [])
        confidence = vision_data.get("confidence", "high")

        # سازگاری با فرمت قدیمی (ingredients) در صورت وجود
        if not items and "ingredients" in vision_data:
            items = [{"name": ing, "quantity": "", "calories": 0} for ing in vision_data["ingredients"]]

        if not items:
            await wait_msg.edit_text("نتونستم مواد غذایی رو تشخیص بدم. یه عکس واضح‌تر بفرست 📸")
            return

        total_calories = sum(item.get("calories", 0) for item in items)

        # ذخیره‌ی ریز مواد تشخیص‌داده‌شده برای استفاده‌ی بعدی در فلوها و تحلیل‌های محصول
        db.log_ingredients(user_id, items, provider=vision_provider)

        goal_text = user.get("goal") or "بدون هدف خاص"
        restrictions = "، ".join(user.get("restrictions", [])) or "ندارم"
        cuisine_line = f"\n- سبک غذایی ترجیحی: {user['cuisine']}" if user.get("cuisine") else ""

        items_breakdown = "\n".join(
            f"- {item['name']}" + (f" ({item['quantity']})" if item["quantity"] else "") + f": {item['calories']} کالری"
            for item in items
        )

        recipe_prompt = f"""تو یک آشپز هوشمند ایرانی هستی.

اطلاعات کاربر:
- هدف: {goal_text}
- محدودیت غذایی: {restrictions}{cuisine_line}

مواد موجود (به همراه کالری تخمینی هرکدوم در حالت خام):
{items_breakdown}

جمع کالری مواد خام: {total_calories} کالری

سه پیشنهاد غذایی بده که با این مواد بشه پخت، به ترتیب اولویت با توجه به هدف کاربر.
کالری نهایی هر غذای پیشنهادی باید با توجه به همین مواد و روش پخت محاسبه شود و با جمع کالری مواد خام هم‌خوان باشد
(لزوماً برابر نیست، چون روغن یا افزودنی‌های پخت می‌تواند کالری را تغییر دهد، اما باید منطقی و قابل توجیه باشد).
پاسخ را ONLY به صورت JSON برگردان بدون هیچ متن اضافی:

{{
  "recipes": [
    {{
      "name": "اسم غذا",
      "calories": عدد,
      "protein": عدد,
      "time": عدد,
      "difficulty": "آسان یا متوسط",
      "why_good": "یک جمله چرا برای این کاربر مناسبه",
      "steps": ["مرحله ۱", "مرحله ۲", "مرحله ۳"]
    }}
  ]
}}"""

        recipes_data, recipe_provider = ai_provider.generate_recipes(recipe_prompt)
        recipes = recipes_data.get("recipes", [])

        new_usage_count = user["usage_count"] + 1
        new_streak = user["streak"] + 1
        db.update_user(user_id, usage_count=new_usage_count, streak=new_streak)

        if recipes:
            db.log_recipe(user_id, recipes[0]["name"], recipes[0].get("calories", 0), provider=recipe_provider)

        # نمایش ریز کالری هر ماده
        items_text = ""
        for item in items:
            qty = f" ({item['quantity']})" if item.get("quantity") else ""
            cal = f" — {item['calories']} کالری" if item.get("calories") else ""
            items_text += f"• {item['name']}{qty}{cal}\n"

        total_cal_text = f"\n🔥 *جمع تخمینی: {total_calories} کالری*" if total_calories > 0 else ""

        message = f"✅ *مواد تشخیص داده شد:*\n\n{items_text}{total_cal_text}\n\n🍽️ *پیشنهادهای غذایی:*\n\n"

        keyboard = []
        for i, recipe in enumerate(recipes[:3]):
            message += (
                f"*{i+1}. {recipe['name']}*\n"
                f"🔥 {recipe['calories']} کالری | 💪 {recipe['protein']}گ پروتئین | ⏱ {recipe['time']} دقیقه\n"
                f"✨ {recipe['why_good']}\n\n"
            )
            keyboard.append([InlineKeyboardButton(f"دستور پخت {recipe['name']} 👨‍🍳", callback_data=f"recipe_{i}")])

        context.user_data["last_recipes"] = recipes
        context.user_data["last_recipe_provider"] = recipe_provider

        if new_usage_count >= 3 and not user["is_subscribed"]:
            keyboard.append([InlineKeyboardButton("🌟 اشتراک ویژه", callback_data="subscribe")])

        await wait_msg.edit_text(message, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

        # پله ۷ از فلو - پیشنهاد مرخصی بعد از هر ۸ روز پشت‌سرهم
        if new_streak > 0 and new_streak % 8 == 0:
            await update.message.reply_text(
                "🎉 ترکوندی!\n\n"
                f"{new_streak} روز پشت سر هم استفاده کردی.\n\n"
                "فردا مرخصی داری — هر چی دوست داری بخور! 😄\n"
                "پس‌فردا دوباره برمی‌گردیم."
            )

        if confidence == "low":
            await update.message.reply_text("⚠️ مطمئن نبودم از بعضی مواد. اگه چیزی اشتباهه بگو.")

    except Exception as e:
        logger.error(f"Error processing photo: {e}")
        await wait_msg.edit_text("مشکلی پیش اومد. دوباره امتحان کن 🙏")


# ---------- جزییات دستور پخت، فیدبک، اشتراک ----------

async def handle_recipe_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data.startswith("recipe_"):
        index = int(query.data.split("_")[1])
        recipes = context.user_data.get("last_recipes", [])

        if index < len(recipes):
            recipe = recipes[index]
            steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(recipe["steps"]))
            message = (
                f"👨‍🍳 *{recipe['name']}*\n\n"
                f"🔥 کالری: {recipe['calories']}\n"
                f"💪 پروتئین: {recipe['protein']}گ\n"
                f"⏱ زمان: {recipe['time']} دقیقه\n"
                f"📊 سختی: {recipe['difficulty']}\n\n"
                f"*مراحل پخت:*\n{steps_text}"
            )
            keyboard = [[
                InlineKeyboardButton("👍 خوب بود", callback_data=f"fb_good_{index}"),
                InlineKeyboardButton("👎 دوست نداشتم", callback_data=f"fb_bad_{index}"),
            ]]

            # برای استفاده‌ی بعدی توی فلوی فیدبک، تا متن دستور پخت گم نشه
            context.user_data["last_recipe_detail_text"] = message

            # لحظه‌ی "وای" - تا فعال نشه (ENABLE_RECIPE_IMAGES) چیزی تولید نمی‌شه و هزینه‌ای ایجاد نمی‌شه
            image_bytes = ai_provider.generate_recipe_image(recipe["name"])
            if image_bytes:
                await query.message.reply_photo(
                    photo=image_bytes,
                    caption=message,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            else:
                await query.edit_message_text(message, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "subscribe":
        await query.edit_message_text(
            "🌟 *اشتراک ویژه فیت پز*\n\n"
            "با اشتراک ماهانه دریافت می‌کنی:\n\n"
            "✅ پیشنهاد غذای نامحدود\n"
            "✅ رژیم ماهانه شخصی‌سازی شده\n"
            "✅ برنامه ورزش ماهانه\n"
            "✅ پیگیری پیشرفت وزن\n\n"
            "به زودی فعال می‌شه... 🚀",
            parse_mode="Markdown",
        )

    elif query.data.startswith("fb_"):
        parts = query.data.split("_")
        fb_type = parts[1]
        index = int(parts[2]) if len(parts) > 2 else 0
        recipes = context.user_data.get("last_recipes", [])
        recipe_name = recipes[index]["name"] if index < len(recipes) else "نامشخص"
        provider_used = context.user_data.get("last_recipe_provider", "unknown")
        db.log_feedback(user_id, recipe_name, fb_type, provider=provider_used)

        user = db.get_user(user_id)
        has_profile = user.get("goal") is not None

        if fb_type == "good":
            base_text = "ممنون! خوشحالم که پسندیدی 😊"
        else:
            base_text = "فهمیدم! دفعه بهتر می‌شه 💪"

        # دستور پخت همچنان روی صفحه می‌مونه - پاک نمی‌شه
        previous_text = context.user_data.get("last_recipe_detail_text", "")
        combined_text = f"{previous_text}\n\n———\n\n{base_text}"

        # تست A/B دعوت به اشتراک‌گذاری - فقط برای فیدبک مثبت (لحظه‌ی "وای")
        if fb_type == "good":
            variant = _get_or_assign_share_variant(user_id, user)
            if variant == "immediate":
                db.log_share_invite(user_id, "immediate")
                combined_text += (
                    "\n\n🎉 وقتی پختیش، عکس غذای واقعی‌ای که درست کردی رو برام بفرست، "
                    "می‌خوام ببینم چی شد!"
                )
            elif variant == "delayed" and context.job_queue is not None:
                context.job_queue.run_once(
                    send_delayed_share_prompt,
                    when=timedelta(hours=SHARE_DELAY_HOURS),
                    data={"user_id": user_id},
                    name=f"share_delay_{user_id}_{index}",
                )

        if not has_profile:
            # قلاب به پروفایل - برای کسی که هنوز پروفایل نساخته
            keyboard = [[
                InlineKeyboardButton("بله، بهترش کن! ✨", callback_data="start_profile"),
                InlineKeyboardButton("نه، ممنون", callback_data="skip_profile"),
            ]]
            await _edit_message(
                query,
                f"{combined_text}\n\n"
                "💡 اگه بگم هدفت چیه (مثلاً کاهش وزن یا عضله‌سازی)، "
                "پیشنهادهام رو دقیق‌تر و شخصی‌تر می‌کنم.\n\n"
                "می‌خوای امتحان کنیم؟",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            # ادامه‌ی فلو بعد از تجربه اول - برای کسی که پروفایل داره
            keyboard = [[
                InlineKeyboardButton("📸 عکس جدید بفرستم", callback_data="take_photo"),
                InlineKeyboardButton("بیشتر بگم درباره خودم 🎯", callback_data="deepen_profile"),
            ]]
            await _edit_message(
                query,
                f"{combined_text}\n\nمی‌خوای الان چیکار کنیم؟",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    elif query.data == "take_photo":
        await _edit_message(query, "📸 عکس از مواد غذایی‌ات بفرست!\n\nسعی کن همه مواد توی عکس دیده بشن.")

    elif query.data == "deepen_profile":
        # ادامه‌ی فلو بعد از تجربه اول - شخصی‌سازی عمیق‌تر
        keyboard = [
            [
                InlineKeyboardButton("ایرانی 🍚", callback_data="cuisine_iranian"),
                InlineKeyboardButton("مدیترانه‌ای 🥗", callback_data="cuisine_mediterranean"),
            ],
            [
                InlineKeyboardButton("فست‌فود سالم 🍔", callback_data="cuisine_fastfood"),
                InlineKeyboardButton("فرقی نمی‌کنه 🤷", callback_data="cuisine_any"),
            ],
        ]
        await _edit_message(
            query,
            "عالیه! یه سوال کوچیک —\n\nبیشتر دوست داری چه سبک غذایی پیشنهاد بدم؟",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif query.data == "start_profile":
        # کاربر قبول کرد پروفایل بسازه — شروع فلوی پروفایل
        user_id = query.from_user.id
        db.update_user(user_id, step="ask_goal")
        keyboard = [
            [
                InlineKeyboardButton("کاهش وزن 🔽", callback_data="goal_lose"),
                InlineKeyboardButton("عضله‌سازی 💪", callback_data="goal_muscle"),
            ],
            [
                InlineKeyboardButton("حفظ وزن ⚖️", callback_data="goal_maintain"),
                InlineKeyboardButton("فقط غذا بپزم 🍳", callback_data="goal_none"),
            ],
        ]
        await _edit_message(
            query,
            "عالیه! یه سوال سریع —\n\nهدف اصلیت چیه؟",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif query.data == "skip_profile":
        await _edit_message(
            query,
            "باشه! هر وقت خواستی /start رو بزن تا پروفایلت رو بسازیم. 📸\n\nتا اون موقع هر وقت عکس فرستادی کمکت می‌کنم."
        )


# ---------- شخصی‌سازی عمیق‌تر (مسیر فلو بعد از تجربه اول) ----------

async def handle_cuisine_preference(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    cuisine_map = {
        "cuisine_iranian": "ایرانی",
        "cuisine_mediterranean": "مدیترانه‌ای",
        "cuisine_fastfood": "فست‌فود سالم",
        "cuisine_any": None,
    }
    cuisine = cuisine_map.get(query.data)
    db.update_user(user_id, cuisine=cuisine)

    await _edit_message(
        query,
        "ثبت شد! ✅\n\nاز این به بعد پیشنهادهام رو با این سبک هم تنظیم می‌کنم.\n\nهر وقت خواستی دوباره عکس بفرست. 📸"
    )


# ---------- دستور پیشرفت ----------

async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پله ۹ از فلو - پیشرفت قابل دیدن."""
    user_id = update.effective_user.id
    stats = db.get_weekly_stats(user_id)
    user = db.get_user(user_id)

    if stats["count"] == 0:
        await update.message.reply_text("هنوز داده‌ای برای این هفته ثبت نشده. یه عکس بفرست شروع کنیم 📸")
        return

    await update.message.reply_text(
        f"📊 *پیشرفت این هفته‌ات*\n\n"
        f"🍽️ {stats['count']} بار از پیشنهادهامون استفاده کردی\n"
        f"🔥 میانگین کالری: {stats['avg_calories']}\n"
        f"🔥 استریک فعلی: {user['streak']} روز\n",
        parse_mode="Markdown",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    if user["step"] in ("new",):
        await start(update, context)
    else:
        await update.message.reply_text("📸 عکس از مواد غذایی‌ات بفرست تا بهت بگم چی بپزی!")


# ---------- لنگر روزانه ----------

async def send_daily_anchor(context: ContextTypes.DEFAULT_TYPE):
    """پله ۶ از فلو - یادآوری ملایم روزانه، فقط برای کسایی که اجازه‌ی صریح دادن."""
    user_ids = db.get_all_active_users_for_reminder()
    for user_id in user_ids:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="سلام! 👋\n\nامروز چی خونه داری؟ یه عکس بفرست تا بهت بگم چی بپزی 🍽️",
            )
        except Exception as e:
            logger.warning(f"ارسال لنگر روزانه به {user_id} ناموفق بود: {e}")


# ---------- تست A/B دعوت اشتراک‌گذاری (کارت ویروسی) ----------

async def send_delayed_share_prompt(context: ContextTypes.DEFAULT_TYPE):
    """واریانت تأخیری تست A/B - چند ساعت بعد از فیدبک مثبت پیگیری می‌کنه."""
    user_id = context.job.data["user_id"]
    db.log_share_invite(user_id, "delayed")
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="چطور شد؟ 😋\n\nعکس غذای پخته‌شده رو نشونم بده، می‌خوام ببینم چی از آب دراومده!",
        )
    except Exception as e:
        logger.warning(f"ارسال پیام تأخیری اشتراک‌گذاری به {user_id} ناموفق بود: {e}")


def main():
    db.init_db()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("progress", progress))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_goal, pattern="^goal_"))
    app.add_handler(CallbackQueryHandler(handle_restriction, pattern="^rest_"))
    app.add_handler(CallbackQueryHandler(handle_anchor_permission, pattern="^anchor_"))
    app.add_handler(CallbackQueryHandler(handle_cuisine_preference, pattern="^cuisine_"))
    app.add_handler(CallbackQueryHandler(handle_recipe_detail))

    if app.job_queue is not None:
        anchor_hour, anchor_minute = map(int, ANCHOR_TIME_UTC.split(":"))
        app.job_queue.run_daily(send_daily_anchor, time=dt_time(hour=anchor_hour, minute=anchor_minute))
        logger.info(f"زمان‌بند لنگر روزانه فعال شد - هر روز ساعت {ANCHOR_TIME_UTC} UTC")
    else:
        logger.warning(
            "JobQueue در دسترس نیست - لنگر روزانه غیرفعاله. "
            "برای فعال‌سازی، در requirements.txt باید python-telegram-bot[job-queue] نصب شده باشه."
        )

    logger.info("Bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
