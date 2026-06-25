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
import image_overlay

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANCHOR_TIME_UTC = os.environ.get("ANCHOR_TIME_UTC", "14:30")  # پیش‌فرض ~۱۸:۰۰ به وقت ایران
SHARE_DELAY_HOURS = float(os.environ.get("SHARE_DELAY_HOURS", "2"))

PHOTO_PROMPT = "📸 عکس غذات رو بفرست (مواد خام یا غذای آماده) تا کالری و ماکروش رو بهت بگم!"


def _get_or_assign_share_variant(user_id: int, user: dict) -> str:
    variant = user.get("share_variant")
    if not variant:
        variant = random.choice(["immediate", "delayed"])
        db.update_user(user_id, share_variant=variant)
    return variant


async def _edit_message(query, text, reply_markup=None, parse_mode="Markdown"):
    if query.message.photo:
        await query.edit_message_caption(caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
    else:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)


def _format_wow_caption(vision_data: dict, untagged: list) -> str:
    extra = image_overlay.format_untagged_lines(untagged)
    if extra:
        return extra
    return "📸 عکس بعدی رو هم بفرست!"


def _build_recipe_prompt(user: dict, items: list, total_calories: int) -> str:
    goal_text = user.get("goal") or "بدون هدف خاص"
    restrictions = "، ".join(user.get("restrictions", [])) or "ندارم"
    cuisine_line = f"\n- سبک غذایی ترجیحی: {user['cuisine']}" if user.get("cuisine") else ""

    items_breakdown = "\n".join(
        f"- {item['name']}" + (f" ({item['quantity']})" if item.get("quantity") else "") + f": {item['calories']} کالری"
        for item in items
    )

    return f"""تو یک آشپز هوشمند ایرانی هستی.

اطلاعات کاربر:
- هدف: {goal_text}
- محدودیت غذایی: {restrictions}{cuisine_line}

مواد/غذای موجود (به همراه کالری تخمینی):
{items_breakdown}

جمع کالری: {total_calories} کالری

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


async def _send_profile_hook(message_target, user_id: int):
    keyboard = [[
        InlineKeyboardButton("بله، دقیق‌ترش کن! ✨", callback_data="start_profile"),
        InlineKeyboardButton("نه، ممنون", callback_data="skip_profile"),
    ]]
    await message_target.reply_text(
        "💡 *این عدد برای یک آدم ناشناسه.*\n\n"
        "بذار بشناسمت تا برات دقیق‌تر و شخصی‌ترش کنم.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _run_recipe_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, user: dict, items: list):
    """فلوی دستور پخت — همان منطق قبلی، فقط بعد از انتخاب کاربر."""
    user_id = update.effective_user.id
    chat = update.effective_chat

    wait_msg = await chat.send_message("چند تا ایده پخت جالب داریم برات... 🍳")

    try:
        total_calories = sum(item.get("calories", 0) for item in items)
        recipe_prompt = _build_recipe_prompt(user, items, total_calories)
        recipes_data, recipe_provider = ai_provider.generate_recipes(recipe_prompt)
        recipes = recipes_data.get("recipes", [])

        if not recipes:
            await wait_msg.edit_text("الان نتونستم ایده پختی پیدا کنم. چند دقیقه دیگه دوباره امتحان کن 🙏")
            return

        if recipes:
            db.log_recipe(user_id, recipes[0]["name"], recipes[0].get("calories", 0), provider=recipe_provider)

        message = "🍽️ *پیشنهادهای غذایی:*\n\n"
        keyboard = []
        for i, recipe in enumerate(recipes[:3]):
            message += (
                f"*{i + 1}. {recipe['name']}*\n"
                f"🔥 {recipe['calories']} کالری | 💪 {recipe['protein']}گ پروتئین | ⏱ {recipe['time']} دقیقه\n"
                f"✨ {recipe['why_good']}\n\n"
            )
            keyboard.append([InlineKeyboardButton(f"دستور پخت {recipe['name']} 👨‍🍳", callback_data=f"recipe_{i}")])

        context.user_data["last_recipes"] = recipes
        context.user_data["last_recipe_provider"] = recipe_provider
        context.user_data["last_items"] = items

        if user["usage_count"] >= 3 and not user["is_subscribed"]:
            keyboard.append([InlineKeyboardButton("🌟 اشتراک ویژه", callback_data="subscribe")])

        await wait_msg.edit_text(message, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Recipe flow failed for user {user_id}: {e}", exc_info=True)
        await wait_msg.edit_text(
            "الان سرویس هوش مصنوعی شلوغه یا در دسترس نیست 😔\n\n"
            "۱۰–۲۰ ثانیه دیگه دوباره «می‌خوای بپزی؟» رو بزن."
        )


# ---------- شروع و پروفایل ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    if context.args and not user.get("entry_source"):
        db.update_user(user_id, entry_source=context.args[0])

    days_gone = db.days_since_last_active(user_id)

    if user["step"] == "ready" and days_gone >= 7:
        await handle_long_absence_return(update, context, days_gone)
        return

    if user["step"] == "ready" and days_gone < 7:
        await update.message.reply_text(f"خوش اومدی! 👋\n\n{PHOTO_PROMPT}")
        return

    db.update_user(user_id, step="awaiting_photo")
    await update.message.reply_text(
        "سلام! 👋\n\n"
        "عکس غذات رو بفرست، تو چند ثانیه کالری و ماکروش رو بهت می‌گم. 📸\n\n"
        "_مواد خام یا غذای آماده — هر دو اوکیه_",
        parse_mode="Markdown",
    )


async def handle_long_absence_return(update: Update, context: ContextTypes.DEFAULT_TYPE, days_gone: int):
    """نقطه شکست — بدون قضاوت، بدون فرم مجدد."""
    await update.message.reply_text(
        "خوش برگشتی 🌿\n\n"
        "بیا از همین‌جا که هستی ادامه بدیم.\n\n"
        f"{PHOTO_PROMPT}"
    )


async def handle_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "age_no":
        await query.edit_message_text(
            "متأسفانه استفاده از فیت‌پز فقط برای افراد ۱۶ سال به بالا امکان‌پذیره. 🙏"
        )
        return

    db.update_user(user_id, age_confirmed=1, step="ask_goal")
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
    await query.edit_message_text(
        "عالیه! ✅\n\nهدف بدنیت چیه؟",
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

    db.update_user(user_id, restrictions=restrictions, step="ask_why_goal")

    keyboard = [
        [
            InlineKeyboardButton("سلامتی بیشتر 💚", callback_data="why_health"),
            InlineKeyboardButton("رسیدن به وزن ایده‌آل ⚖️", callback_data="why_weight"),
        ],
        [
            InlineKeyboardButton("انرژی بیشتر ⚡", callback_data="why_energy"),
            InlineKeyboardButton("رد کن ⏭", callback_data="why_skip"),
        ],
    ]
    await query.edit_message_text(
        "یه سوال کوچیک — چرا این هدف برات مهمه؟\n"
        "(کمک می‌کنه بهتر همراهیت کنم)",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_why_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    why_map = {
        "why_health": "سلامتی بیشتر",
        "why_weight": "رسیدن به وزن ایده‌آل",
        "why_energy": "انرژی بیشتر",
        "why_skip": None,
    }
    why = why_map.get(query.data)
    db.update_user(user_id, why_goal=why, step="ask_anchor_permission")

    keyboard = [
        [
            InlineKeyboardButton("بله، یادم بده ✅", callback_data="anchor_yes"),
            InlineKeyboardButton("نه، فعلاً نه ❌", callback_data="anchor_no"),
        ]
    ]
    await query.edit_message_text(
        "عالیه! تقریباً تمومه 🎉\n\n"
        "می‌خوای هر روز یه پیام کوتاه بفرستم و بپرسم چی خوردی یا چی داری؟\n"
        "(هر وقت خواستی می‌تونی خاموشش کنی)",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_anchor_permission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    allow = 1 if query.data == "anchor_yes" else 0
    db.update_user(user_id, allow_daily_anchor=allow, step="ready")

    user = db.get_user(user_id)
    restrictions_text = "، ".join(user["restrictions"]) if user["restrictions"] else "ندارم"

    await query.edit_message_text(
        f"پروفایلت آماده شد 🎉\n\n"
        f"هدف: {user['goal']}\n"
        f"محدودیت: {restrictions_text}\n\n"
        f"{PHOTO_PROMPT}"
    )


async def handle_profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    db.update_user(user_id, step="ask_age")

    keyboard = [
        [
            InlineKeyboardButton("بله، ۱۶ سال یا بیشتر دارم ✅", callback_data="age_yes"),
            InlineKeyboardButton("نه ❌", callback_data="age_no"),
        ]
    ]
    await _edit_message(
        query,
        "قبل از شروع، یه تأیید کوچیک:\n\nآیا ۱۶ سال یا بیشتر داری؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_skip_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _edit_message(
        query,
        f"باشه! هر وقت خواستی /start بزن تا پروفایلت رو بسازیم.\n\n{PHOTO_PROMPT}",
    )


# ---------- واو مومنت و عکس ----------

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    pending_invite = db.get_pending_share_invite(user_id)
    if pending_invite:
        photo_file_id = update.message.photo[-1].file_id
        db.mark_share_responded(pending_invite["id"], photo_file_id=photo_file_id)
        context.user_data["from_share_invite"] = True

    allowed, rate_msg = db.check_photo_rate_limit(user_id)
    if not allowed:
        await update.message.reply_text(rate_msg)
        return

    wait_msg = await update.message.reply_text("داریم غذا رو بررسی می‌کنیم... 🔍")

    try:
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        photo_bytes = bytes(await photo_file.download_as_bytearray())

        vision_data, vision_provider = ai_provider.analyze_photo_with_positions(
            base64.b64encode(photo_bytes).decode("utf-8")
        )
        items = vision_data.get("items", [])
        confidence = vision_data.get("confidence", "high")

        if not items:
            await wait_msg.edit_text("نتونستم غذا رو تشخیص بدم. یه عکس واضح‌تر بفرست 📸")
            return

        totals = vision_data.get("totals", {})
        db.log_ingredients(user_id, items, provider=vision_provider)
        db.log_meal(
            user_id,
            totals.get("calories", 0),
            totals.get("protein_g", 0),
            totals.get("carbs_g", 0),
            totals.get("fat_g", 0),
            photo_type=vision_data.get("photo_type"),
            provider=vision_provider,
        )

        tagged_bytes, untagged = image_overlay.create_tagged_image(
            photo_bytes, items, totals=vision_data.get("totals")
        )
        caption = _format_wow_caption(vision_data, untagged)
        if context.user_data.pop("from_share_invite", False):
            caption += "\n\n📲 *این کارت رو می‌تونی توی استوری به اشتراک بذاری!*"

        context.user_data["last_items"] = items
        context.user_data["last_vision_data"] = vision_data

        new_usage_count = user["usage_count"] + 1
        new_streak = user["streak"] + 1
        db.update_user(user_id, usage_count=new_usage_count, streak=new_streak, has_wow=1)

        keyboard = [[InlineKeyboardButton("🍳 می‌خوای با همینا یه چیزی بپزی؟", callback_data="want_cook")]]
        if new_usage_count >= 3 and not user["is_subscribed"]:
            keyboard.append([InlineKeyboardButton("🌟 اشتراک ویژه", callback_data="subscribe")])

        await wait_msg.delete()
        await update.message.reply_photo(
            photo=tagged_bytes,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        user = db.get_user(user_id)
        if not db.is_profile_complete(user):
            await _send_profile_hook(update.message, user_id)

        if new_streak > 0 and new_streak % 8 == 0:
            await update.message.reply_text(
                "🎉 ترکوندی!\n\n"
                f"{new_streak} روز پشت سر هم استفاده کردی.\n\n"
                "فردا مرخصی داری — هر چی دوست داری بخور! 😄\n"
                "پس‌فردا دوباره برمی‌گردیم."
            )

        if confidence == "low":
            await update.message.reply_text("⚠️ مطمئن نبودم از بعضی موارد. اگه چیزی اشتباهه بگو.")

    except Exception as e:
        logger.error(f"Error processing photo for user {user_id}: {e}", exc_info=True)
        await wait_msg.edit_text("مشکلی پیش اومد. دوباره امتحان کن 🙏")


# ---------- دستور پخت، فیدبک، اشتراک ----------

async def handle_recipe_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "want_cook":
        user = db.get_user(user_id)
        items = context.user_data.get("last_items", [])
        if not items:
            await query.message.reply_text("اول یه عکس بفرست تا ببینم چی داری 📸")
            return
        await _run_recipe_flow(update, context, user, items)
        return

    if query.data.startswith("recipe_"):
        index = int(query.data.split("_")[1])
        recipes = context.user_data.get("last_recipes", [])

        if index < len(recipes):
            recipe = recipes[index]
            steps_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(recipe["steps"]))
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
            context.user_data["last_recipe_detail_text"] = message

            image_bytes = ai_provider.generate_recipe_image(recipe["name"])
            if image_bytes:
                await query.message.reply_photo(
                    photo=image_bytes,
                    caption=message,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            else:
                await _edit_message(query, message, reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "subscribe":
        await _edit_message(
            query,
            "🌟 *اشتراک ویژه فیت پز*\n\n"
            "با اشتراک ماهانه دریافت می‌کنی:\n\n"
            "✅ تحلیل کالری و ماکرو نامحدود\n"
            "✅ پیشنهاد غذای نامحدود\n"
            "✅ رژیم ماهانه شخصی‌سازی شده\n"
            "✅ برنامه ورزش ماهانه\n\n"
            "به زودی فعال می‌شه... 🚀",
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
            base_text = "ممنون! خوشحالم که پسندیدی 😊"
        else:
            base_text = "فهمیدم! دفعه بهتر می‌شه 💪"

        previous_text = context.user_data.get("last_recipe_detail_text", "")
        combined_text = f"{previous_text}\n\n———\n\n{base_text}"

        user = db.get_user(user_id)
        if fb_type == "good":
            variant = _get_or_assign_share_variant(user_id, user)
            if variant == "immediate":
                db.log_share_invite(user_id, "immediate")
                combined_text += (
                    "\n\n🎉 وقتی پختیش، عکس غذای واقعی‌ای که درست کردی رو برام بفرست!"
                )
            elif variant == "delayed" and context.job_queue is not None:
                context.job_queue.run_once(
                    send_delayed_share_prompt,
                    when=timedelta(hours=SHARE_DELAY_HOURS),
                    data={"user_id": user_id},
                    name=f"share_delay_{user_id}_{index}",
                )

        keyboard = [[
            InlineKeyboardButton("📸 عکس جدید", callback_data="take_photo"),
            InlineKeyboardButton("بیشتر بگم 🎯", callback_data="deepen_profile"),
        ]]
        await _edit_message(
            query,
            f"{combined_text}\n\n{PHOTO_PROMPT}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif query.data == "take_photo":
        await _edit_message(query, PHOTO_PROMPT)

    elif query.data == "deepen_profile":
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
            "یه سوال کوچیک — بیشتر دوست داری چه سبک غذایی پیشنهاد بدم؟",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif query.data == "start_profile":
        await handle_profile_start(update, context)

    elif query.data == "skip_profile":
        await handle_skip_profile(update, context)


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

    await _edit_message(query, f"ثبت شد! ✅\n\n{PHOTO_PROMPT}")


async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = db.get_weekly_stats(user_id)
    user = db.get_user(user_id)

    if stats["count"] == 0:
        await update.message.reply_text(f"هنوز داده‌ای برای این هفته ثبت نشده. {PHOTO_PROMPT}")
        return

    await update.message.reply_text(
        f"📊 *پیشرفت این هفته‌ات*\n\n"
        f"📸 {stats['count']} بار غذا ثبت کردی\n"
        f"🔥 میانگین کالری: {stats['avg_calories']}\n"
        f"🔥 استریک فعلی: {user['streak']} روز\n",
        parse_mode="Markdown",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    if user["step"] == "ask_why_goal":
        db.update_user(user_id, why_goal=update.message.text.strip()[:200], step="ask_anchor_permission")
        keyboard = [
            [
                InlineKeyboardButton("بله، یادم بده ✅", callback_data="anchor_yes"),
                InlineKeyboardButton("نه، فعلاً نه ❌", callback_data="anchor_no"),
            ]
        ]
        await update.message.reply_text(
            "ممنون که گفتی! 🙏\n\nمی‌خوای هر روز یه پیام کوتاه بفرستم؟",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if user["step"] in ("new", "awaiting_photo"):
        await start(update, context)
    else:
        await update.message.reply_text(PHOTO_PROMPT)


async def send_daily_anchor(context: ContextTypes.DEFAULT_TYPE):
    user_ids = db.get_all_active_users_for_reminder()
    for user_id in user_ids:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"سلام! 👋\n\nامروز چی خوردی یا چی داری؟ {PHOTO_PROMPT}",
            )
        except Exception as e:
            logger.warning(f"ارسال لنگر روزانه به {user_id} ناموفق بود: {e}")


async def send_delayed_share_prompt(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.data["user_id"]
    db.log_share_invite(user_id, "delayed")
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="چطور شد؟ 😋\n\nعکس غذای پخته‌شده رو نشونم بده!",
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
    app.add_handler(CallbackQueryHandler(handle_age, pattern="^age_"))
    app.add_handler(CallbackQueryHandler(handle_goal, pattern="^goal_"))
    app.add_handler(CallbackQueryHandler(handle_restriction, pattern="^rest_"))
    app.add_handler(CallbackQueryHandler(handle_why_goal, pattern="^why_"))
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
