import os
import base64
import logging
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

    wait_msg = await update.message.reply_text("داریم مواد رو بررسی می‌کنیم... 🔍")

    try:
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        photo_b64 = base64.b64encode(photo_bytes).decode("utf-8")

        await wait_msg.edit_text("چند تا ایده جالب داریم برات... 🍽️")

        vision_data, vision_provider = ai_provider.analyze_photo(photo_b64)
        ingredients = vision_data.get("ingredients", [])
        confidence = vision_data.get("confidence", "high")

        if not ingredients:
            await wait_msg.edit_text("نتونستم مواد غذایی رو تشخیص بدم. یه عکس واضح‌تر بفرست 📸")
            return

        goal_text = user.get("goal") or "بدون هدف خاص"
        restrictions = "، ".join(user.get("restrictions", [])) or "ندارم"

        recipe_prompt = f"""تو یک آشپز هوشمند ایرانی هستی.

اطلاعات کاربر:
- هدف: {goal_text}
- محدودیت غذایی: {restrictions}

مواد موجود: {", ".join(ingredients)}

سه پیشنهاد غذایی بده که با این مواد بشه پخت، به ترتیب اولویت با توجه به هدف کاربر.
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

        ingredients_text = " | ".join(ingredients)
        message = f"✅ مواد تشخیص داده شد:\n{ingredients_text}\n\n🍽️ پیشنهادهای غذایی:\n\n"

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

        if fb_type == "good":
            await query.edit_message_text("ممنون! خوشحالم که پسندیدی 😊\n\nهر وقت خواستی دوباره عکس بفرست.")
        else:
            await query.edit_message_text("فهمیدم! دفعه بهتر می‌شه 💪\n\nدوباره عکس بفرست تا گزینه دیگه‌ای پیشنهاد بدم.")

    elif query.data == "take_photo":
        await query.edit_message_text("📸 عکس از مواد غذایی‌ات بفرست!\n\nسعی کن همه مواد توی عکس دیده بشن.")


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
    app.add_handler(CallbackQueryHandler(handle_recipe_detail))

    logger.info("Bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
