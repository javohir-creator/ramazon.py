import logging
import requests
import json
import aiosqlite
import os
import io  #hgjg
from datetime import datetime
from typing import Optional, Tuple, Dict, List
from contextlib import asynccontextmanager
from PIL import Image, ImageDraw, ImageFont
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # Serverda ishlashi uchunn.
import numpy as np
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
import tempfile

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    CallbackContext,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

# --- Sozlamalar ---
BOT_TOKEN = "8157054894:AAFjY6XsRrkPcKLT9ksx-06W8zKY-2yw6ps"
API_BASE_URL = "https://ramazon-taqvimi-2026.onrender.com/api"
AREAS_URL = f"{API_BASE_URL}/areas"
PORT = int(os.environ.get('PORT', 8443))
# WEBHOOK_URL = "https://sizning-bot-url.uz/webhook"  # Uptime Robot uchun

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Conversation holatlari ---
REGION_SELECT, DISTRICT_SELECT = range(2)

# --- Ma'lumotlar bazasi ---
DB_PATH = "users.db"

@asynccontextmanager
async def get_db_connection():
    conn = await aiosqlite.connect(DB_PATH)
    try:
        yield conn
    finally:
        await conn.close()

async def init_db():
    async with get_db_connection() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                region_name TEXT,
                district_name TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.commit()

async def save_user_location(user_id: int, region: str, district: str):
    async with get_db_connection() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, region_name, district_name, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                region_name = excluded.region_name,
                district_name = excluded.district_name,
                updated_at = CURRENT_TIMESTAMP
        """, (user_id, region, district))
        await conn.commit()

async def get_user_location(user_id: int) -> Optional[Tuple[str, str]]:
    async with get_db_connection() as conn:
        cursor = await conn.execute(
            "SELECT region_name, district_name FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = await cursor.fetchone()
        return row if row else None

# --- API dan hududlar olish ---
def fetch_areas() -> Optional[Dict[str, List[str]]]:
    try:
        response = requests.get(AREAS_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        regions_dict = {}
        for region in data.get("regions", []):
            name = region.get("name")
            districts = region.get("districts", [])
            if name and districts:
                regions_dict[name] = districts
        return regions_dict
    except Exception as e:
        logger.error(f"API xatosi: {e}")
        return None

def fetch_ramazon_calendar(region: str, district: str) -> Optional[List[Dict]]:
    try:
        url = f"{API_BASE_URL}/ramazon-2026"
        params = {"region": region, "district": district}
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        days = data.get("days", [])
        result = []
        for d in days:
            day_num = d.get("day")
            saharlik = d.get("saharlik")
            iftorlik = d.get("iftorlik")
            if day_num is not None and saharlik and iftorlik:
                result.append({"kun": day_num, "saharlik": saharlik, "iftorlik": iftorlik, "date": d.get("date")})
        return result if result else None
    except Exception as e:
        logger.error(f"Ramazon taqvimi API xatosi: {e}")
        return None

# --- NAMUNAVIY TAQVIM MA'LUMOTLARI (HAQIQIY API BILAN ALMASHTIRILADI) ---
def get_sample_calendar_data(region: str, district: str) -> List[Dict]:
    """Namuna ma'lumot - haqiqiy API dan olinadigan ma'lumotlar bilan almashtiring"""
    data = []
    for day in range(1, 31):
        data.append({
            "kun": day,
            "saharlik": f"{4 + day % 3}:{30 + day % 20}",
            "iftorlik": f"{18 + day % 2}:{45 - day % 15}"
        })
    return data

# --- PDF YARATISH ---
async def create_pdf_calendar(region: str, district: str, calendar_data: List[Dict]) -> bytes:
    """Ramazon taqvimini PDF formatida yaratish"""
    buffer = io.BytesIO()
    
    # A4 o'lcham (210mm x 297mm)
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Sarlavha
    c.setFont("Helvetica-Bold", 24)
    c.drawString(50, height - 50, f"🌙 RAMAZON 2026")
    
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 80, f"{region} - {district}")
    
    c.setFont("Helvetica", 12)
    c.drawString(50, height - 100, f"Yuklab olingan sana: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    
    # Jadval sarlavhalari
    y_start = height - 140
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y_start, "Kun")
    c.drawString(150, y_start, "Saharlik")
    c.drawString(250, y_start, "Iftorlik")
    c.line(40, y_start - 5, 550, y_start - 5)
    
    # Jadval ma'lumotlari
    y = y_start - 25
    c.setFont("Helvetica", 10)
    
    for i, day_data in enumerate(calendar_data):
        if y < 50:  # Yangi sahifa
            c.showPage()
            y = height - 50
            c.setFont("Helvetica", 10)
        
        c.drawString(50, y, str(day_data["kun"]))
        c.drawString(150, y, day_data["saharlik"])
        c.drawString(250, y, day_data["iftorlik"])
        
        # Har 2 kunda chiziq
        if i % 2 == 0:
            c.line(40, y - 3, 550, y - 3)
        
        y -= 20
    
    # Pastki qism
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(50, 30, "© Ramazon Taqvimi 2026 - Barcha huquqlar himoyalangan")
    
    c.save()
    buffer.seek(0)
    return buffer.getvalue()

# --- JPG YARATISH ---
async def create_jpg_calendar(region: str, district: str, calendar_data: List[Dict]) -> bytes:
    """Ramazon taqvimini JPG formatida yaratish (chiroyli islomiy dizayn)"""
    
    # O'lchamlar
    img_width = 1200
    img_height = 1800
    background_color = (10, 40, 20)  # To'q yashil islomiy rang
    text_color = (255, 215, 0)  # Oltin rang
    table_color = (255, 255, 200)  # Och sariq
    
    # Rasm yaratish
    img = Image.new('RGB', (img_width, img_height), color=background_color)
    draw = ImageDraw.Draw(img)
    
    # Fon naqsh (yarim oy va yulduzlar)
    try:
        # Fontlarni yuklash (agar mavjud bo'lmasa, default ishlatiladi)
        title_font = ImageFont.truetype("arial.ttf", 60)
        subtitle_font = ImageFont.truetype("arial.ttf", 40)
        regular_font = ImageFont.truetype("arial.ttf", 25)
    except:
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()
        regular_font = ImageFont.load_default()
    
    # Sarlavha
    draw.text((img_width//2, 80), "🌙 RAMAZON 2026", 
              fill=text_color, font=title_font, anchor="mt")
    draw.text((img_width//2, 150), f"{region} - {district}", 
              fill=(255, 255, 255), font=subtitle_font, anchor="mt")
    
    # Jadval chizish
    start_x, start_y = 100, 250
    col_width = 300
    row_height = 50
    
    # Jadval sarlavhalari
    draw.rectangle([start_x-5, start_y-5, start_x+col_width*3+5, start_y+row_height], 
                   fill=(0, 80, 0))
    draw.text((start_x + 50, start_y + 10), "Kun", fill=text_color, font=regular_font)
    draw.text((start_x + col_width + 50, start_y + 10), "Saharlik", fill=text_color, font=regular_font)
    draw.text((start_x + col_width*2 + 50, start_y + 10), "Iftorlik", fill=text_color, font=regular_font)
    
    # Ma'lumotlar
    for i, day_data in enumerate(calendar_data):
        y = start_y + row_height + (i * row_height)
        
        # Rangli fon (juft va toq kunlar uchun)
        if i % 2 == 0:
            draw.rectangle([start_x-5, y-5, start_x+col_width*3+5, y+row_height-5], 
                          fill=(0, 60, 0))
        
        draw.text((start_x + 50, y), str(day_data["kun"]), fill=table_color, font=regular_font)
        draw.text((start_x + col_width + 50, y), day_data["saharlik"], fill=table_color, font=regular_font)
        draw.text((start_x + col_width*2 + 50, y), day_data["iftorlik"], fill=table_color, font=regular_font)
    
    # Chegara chiziqlari
    for i in range(4):
        x = start_x + (i * col_width) - 5
        draw.line([(x, start_y-5), (x, y+row_height)], fill=text_color, width=2)
    
    # Pastki matn
    draw.text((img_width//2, img_height-80), 
              "Ramazon oyi muborak bo'lsin! 🤲", 
              fill=text_color, font=regular_font, anchor="mt")
    
    # Rasmni byte larga o'tkazish
    img_buffer = io.BytesIO()
    img.save(img_buffer, format='JPEG', quality=95)
    img_buffer.seek(0)
    return img_buffer.getvalue()

# --- INLINE KLAVIATURALAR ---
def build_regions_keyboard(regions_dict: Dict[str, List[str]]) -> InlineKeyboardMarkup:
    keyboard = []
    sorted_regions = sorted(regions_dict.keys())
    row = []
    for i, region in enumerate(sorted_regions):
        row.append(InlineKeyboardButton(region, callback_data=f"region_{region}"))
        if len(row) == 2 or i == len(sorted_regions) - 1:
            keyboard.append(row)
            row = []
    return InlineKeyboardMarkup(keyboard)

def build_districts_keyboard(districts: List[str]) -> InlineKeyboardMarkup:
    keyboard = []
    row = []
    for i, district in enumerate(sorted(districts)):
        row.append(InlineKeyboardButton(district, callback_data=f"district_{district}"))
        if len(row) == 2 or i == len(districts) - 1:
            keyboard.append(row)
            row = []
    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back_to_regions")])
    return InlineKeyboardMarkup(keyboard)

def build_format_keyboard() -> InlineKeyboardMarkup:
    """PDF yoki JPG tanlash uchun klaviatura"""
    keyboard = [
        [
            InlineKeyboardButton("📄 PDF yuklab olish", callback_data="format_pdf"),
            InlineKeyboardButton("🖼️ JPG yuklab olish", callback_data="format_jpg")
        ],
        [InlineKeyboardButton("🔙 Bekor qilish", callback_data="cancel_format")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_main_reply_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
         [KeyboardButton("📍 Hudud")],
         [KeyboardButton("🗓️ Taqvim")],
         [KeyboardButton("🌅 Bugun")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
# --- HANDLERLAR ---
async def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    welcome_text = (
        f"🌙 *Assalomu alaykum, {user.first_name}!*\n\n"
        "🤲 Ramazon oyi muborak bo'lsin!\n\n"
        "Men sizga 2026 yilgi Ramazon taqvimini chiroyli formatlarda yuklab beraman.\n\n"
        "📌 *Buyruqlar:*\n"
        "▪️ /hudud – Viloyat va tumaningizni tanlash\n"
        "▪️ /taqvim – Oylik taqvimni yuklab olish (PDF yoki JPG)\n"
        "▪️ /bugun – Bugungi vaqtlarni ko'rish\n\n"
        "Avval /hudud buyrug'i orqali joylashuvingizni tanlang."
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN, reply_markup=build_main_reply_keyboard())

async def hudud(update: Update, context: CallbackContext) -> int:
    regions_dict = fetch_areas()
    if not regions_dict:
        await update.message.reply_text(
            "❌ Hududlar ro'yxatini yuklab bo'lmadi.\n"
            "Iltimos, birozdan so'ng qayta urinib ko'ring."
        )
        return ConversationHandler.END

    context.user_data["regions_dict"] = regions_dict
    reply_markup = build_regions_keyboard(regions_dict)
    await update.message.reply_text(
        "🌍 *Viloyatingizni tanlang:*",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    return REGION_SELECT

async def region_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    region_name = query.data.replace("region_", "")
    regions_dict = context.user_data.get("regions_dict")

    if not regions_dict or region_name not in regions_dict:
        await query.edit_message_text("❌ Xatolik. Iltimos, /hudud buyrug'ini qaytadan yuboring.")
        return ConversationHandler.END

    districts = regions_dict[region_name]
    context.user_data["selected_region"] = region_name

    reply_markup = build_districts_keyboard(districts)
    await query.edit_message_text(
        f"📍 *{region_name}* viloyati.\n\nTumanni tanlang:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    return DISTRICT_SELECT

async def district_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    district_name = query.data.replace("district_", "")
    region_name = context.user_data.get("selected_region")

    if not region_name:
        await query.edit_message_text("❌ Xatolik. /hudud buyrug'ini qaytadan yuboring.")
        return ConversationHandler.END

    user_id = update.effective_user.id
    await save_user_location(user_id, region_name, district_name)

    await query.edit_message_text(
        f"✅ *Hududingiz saqlandi!*\n\n"
        f"Viloyat: {region_name}\n"
        f"Tuman: {district_name}\n\n"
        f"Endi /taqvim buyrug'i orqali taqvimni yuklab olishingiz mumkin.",
        parse_mode=ParseMode.MARKDOWN
    )

    context.user_data.clear()
    return ConversationHandler.END

async def back_to_regions(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    regions_dict = context.user_data.get("regions_dict")
    if not regions_dict:
        await query.edit_message_text("❌ Xatolik. Iltimos, /hudud buyrug'ini qaytadan yuboring.")
        return ConversationHandler.END

    reply_markup = build_regions_keyboard(regions_dict)
    await query.edit_message_text(
        "🌍 *Viloyatingizni tanlang:*",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    return REGION_SELECT

async def cancel(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("🛑 Hudud tanlash bekor qilindi.")
    context.user_data.clear()
    return ConversationHandler.END

async def taqvim(update: Update, context: CallbackContext) -> None:
    """Oylik taqvimni yuklab olish"""
    user_id = update.effective_user.id
    location = await get_user_location(user_id)

    if not location:
        await update.message.reply_text(
            "❗ Avval /hudud buyrug'i orqali joylashuvingizni tanlang."
        )
        return

    region, district = location
    context.user_data["taqvim_region"] = region
    context.user_data["taqvim_district"] = district

    # Format tanlash uchun klaviatura
    await update.message.reply_text(
        f"📊 *{region}, {district}* uchun taqvim formatini tanlang:",
        reply_markup=build_format_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )

async def format_callback(update: Update, context: CallbackContext) -> None:
    """Tanlangan formatda taqvim yaratish va yuborish"""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_format":
        await query.edit_message_text("✅ Bekor qilindi.")
        return

    region = context.user_data.get("taqvim_region")
    district = context.user_data.get("taqvim_district")

    if not region or not district:
        await query.edit_message_text("❌ Xatolik. Iltimos, qaytadan /taqvim buyrug'ini yuboring.")
        return

    # Yuklanayotgan xabar
    await query.edit_message_text(
        f"⏳ *{region}, {district}* uchun taqvim tayyorlanmoqda...\n"
        f"Iltimos, biroz kuting.",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        calendar_data = fetch_ramazon_calendar(region, district)
        if not calendar_data:
            calendar_data = get_sample_calendar_data(region, district)

        if query.data == "format_pdf":
            # PDF yaratish
            pdf_bytes = await create_pdf_calendar(region, district, calendar_data)
            
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=io.BytesIO(pdf_bytes),
                filename=f"ramazon_2026_{district}.pdf",
                caption=f"📄 *{region}, {district}* uchun Ramazon 2026 taqvimi\n\n🤲 Ro'zangiz qabul bo'lsin!",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            # JPG yaratish
            jpg_bytes = await create_jpg_calendar(region, district, calendar_data)
            
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=io.BytesIO(jpg_bytes),
                caption=f"🖼️ *{region}, {district}* uchun Ramazon 2026 taqvimi\n\n🤲 Ro'zangiz qabul bo'lsin!",
                parse_mode=ParseMode.MARKDOWN,
                filename=f"ramazon_2026_{district}.jpg"
            )

        # Format tanlash xabarini o'chirish
        await query.delete_message()

    except Exception as e:
        logger.error(f"Taqvim yaratishda xatolik: {e}")
        await query.edit_message_text(
            "❌ Taqvim yaratishda xatolik yuz berdi.\n"
            "Iltimos, qaytadan urinib ko'ring."
        )

async def bugun(update: Update, context: CallbackContext) -> None:
    """Bugungi vaqtlarni ko'rsatish"""
    user_id = update.effective_user.id
    location = await get_user_location(user_id)

    if not location:
        await update.message.reply_text(
            "❗ Avval /hudud buyrug'i orqali joylashuvingizni tanlang."
        )
        return

    region, district = location
    today_dt = datetime.now()
    today_str = today_dt.strftime("%Y-%m-%d")
    calendar_data = fetch_ramazon_calendar(region, district) or []
    today_item = None
    for d in calendar_data:
        if d.get("date") == today_str:
            today_item = d
            break
    if not today_item and calendar_data:
        today_item = calendar_data[0]
    saharlik = today_item["saharlik"] if today_item else "—"
    iftorlik = today_item["iftorlik"] if today_item else "—"
    today = today_dt.strftime("%d %B %Y")
    
    # Bugungi ma'lumot (namuna)
    bugun = f"""📅 *{today}*
📍 *{region}, {district}*

🌙 *Saharlik:* {saharlik}
⭐ *Iftorlik:* {iftorlik}

⏳ *Ro'za muddati:* —

🤲 Duo: Saharlik
Navaytu an asuma savma shahri ramazona 
minal fajri ilal mag'ribi xolisan lillahi taala
Ma'nosi: Ramazon oyining ro'zasini subhdan to kechgacha 
tutmoqni niyat qildim. Xolis Alloh taolo uchun.

🤲 Duo: Iftorlik
Allohumma laka sumtu va bika amantu 
va alayaka tavakkaltu va ala rizqika aftartu
Ma'nosi: Ey Alloh, Sen uchun ro'za tutdim, 
Senga iymon keltirdim, Senga tavakkal qildim 
va bergan rizqing bilan og'iz ochdim.

🤲 *Alloh ro'zangizni qabul qilsin!*"""
    await update.message.reply_text(bugun, parse_mode=ParseMode.MARKDOWN)

async def text_buttons_handler(update: Update, context: CallbackContext):
    text = (update.message.text or "").strip()
    if text in ("📍 Hudud", "Hudud"):
        return await hudud(update, context)
    if text in ("🗓️ Taqvim", "Taqvim"):
        return await taqvim(update, context)
    if text in ("🌅 Bugun", "Bugun"):
        return await bugun(update, context)

async def post_init(application: Application) -> None:
    """Bot ishga tushganda ma'lumotlar bazasini yaratish"""
    await init_db()
    logger.info("Ma'lumotlar bazasi tayyor")

# --- ASOSIY FUNKSIYA (Webhook bilan) ---
def main() -> None:
    """Botni ishga tushirish (Webhook)"""
    
    # Application yaratish
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Conversation handler
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("hudud", hudud),
            MessageHandler(filters.Regex(r"^(📍 Hudud|Hudud)$"), hudud),
        ],
        states={
            REGION_SELECT: [CallbackQueryHandler(region_callback, pattern="^region_")],
            DISTRICT_SELECT: [
                CallbackQueryHandler(district_callback, pattern="^district_"),
                CallbackQueryHandler(back_to_regions, pattern="^back_to_regions$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Handlerlarni qo'shish
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("taqvim", taqvim))
    application.add_handler(CommandHandler("bugun", bugun))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_buttons_handler))
    application.add_handler(CallbackQueryHandler(format_callback, pattern="^(format_pdf|format_jpg|cancel_format)$"))

    # Webhook yoki polling
    WEBHOOK_URL = os.environ.get('WEBHOOK_URL')
    
    if WEBHOOK_URL:
        # Webhook rejimi (Uptime Robot uchun)
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        )
        logger.info(f"Bot webhook bilan ishga tushdi: {WEBHOOK_URL}")
    else:
        # Polling rejimi (localhostda test qilish uchun)
        logger.info("Bot polling bilan ishga tushdi")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
