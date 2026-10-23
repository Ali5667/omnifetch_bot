import os
import sys
import re
import io
import json
import asyncio
import logging
import sqlite3
import threading
import subprocess
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import date, datetime, timedelta
from typing import Optional, List, Tuple

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from PIL import Image
import httpx
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    User,
    InputMediaPhoto,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeChat,
)
from telegram.request import HTTPXRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)
import yt_dlp

# --- الإعدادات الأساسية (CONFIGURATION) ---
BOT_TOKEN = "8849751050:AAFQuDQL5E40L2L2H62kpvUijUbHwMl829s"
ADMIN_ID = 8472563112  # آيدي المدير الخاص بك
ADMIN_USERNAME = "@CYS_ALI3"
ADMIN_URL = "https://t.me/CYS_ALI3"

# التوكن من BotFather -> /mybots -> Payments
PAYMENT_PROVIDER_TOKEN = "YOUR_PAYMENT_PROVIDER_TOKEN_HERE" 

DAILY_FREE_LIMIT = 10
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.svg', '.ico')
COOKIES_FILE = "cookies.txt"  # ملف كوكيز يوتيوب لتجاوز القيود وتحميل فيديوهات الانتساب

# --- محرك الترجمة واستخراج النصوص للفيديوهات (14 لغة عالمية) ---
TRANSLATION_LANGUAGES = {
    "ar": {"name": "العربية (Arabic)", "flag": "🇸🇦", "code": "ar"},
    "en": {"name": "English (الإنجليزية)", "flag": "🇺🇸", "code": "en"},
    "ku": {"name": "کوردی (الكردية)", "flag": "☀️", "code": "ku"},
    "tr": {"name": "Türkçe (التركية)", "flag": "🇹🇷", "code": "tr"},
    "fa": {"name": "فارسی (الفارسية)", "flag": "🇮🇷", "code": "fa"},
    "fr": {"name": "Français (الفرنسية)", "flag": "🇫🇷", "code": "fr"},
    "es": {"name": "Español (الإسبانية)", "flag": "🇪🇸", "code": "es"},
    "de": {"name": "Deutsch (الألمانية)", "flag": "🇩🇪", "code": "de"},
    "ru": {"name": "Русский (الروسية)", "flag": "🇷🇺", "code": "ru"},
    "zh-CN": {"name": "中文 (الصينية)", "flag": "🇨🇳", "code": "zh-CN"},
    "ja": {"name": "日本語 (اليابانية)", "flag": "🇯🇵", "code": "ja"},
    "ko": {"name": "한국어 (الكورية)", "flag": "🇰🇷", "code": "ko"},
    "it": {"name": "Italiano (الإيطالية)", "flag": "🇮🇹", "code": "it"},
    "pt": {"name": "Português (البرتغالية)", "flag": "🇧🇷", "code": "pt"},
}

def get_translation_keyboard(include_back: bool = True) -> InlineKeyboardMarkup:
    """توليد لوحة أزرار تفاعلية تضم الـ 14 لغة ترجمة عالمية المتاحة"""
    keys = list(TRANSLATION_LANGUAGES.items())
    rows = []
    for i in range(0, len(keys), 2):
        row = []
        for code, info in keys[i:i+2]:
            row.append(InlineKeyboardButton(f"{info['flag']} {info['name']}", callback_data=f"tr_lang_{code}"))
        rows.append(row)
    if include_back:
        rows.append([InlineKeyboardButton("🔙 رجوع للقائمة السابقة", callback_data="choose_type_back")])
    return InlineKeyboardMarkup(rows)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- خادم ويب خفيف للفحص السحابي (CLOUD HEALTH CHECK FOR 24/7 HOSTING) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'{"status":"online","bot":"OmniFetch","version":"3.5"}')

    def log_message(self, format, *args):
        pass  # كتم سجلات الويب لتفادي إزعاج الكونسول

def run_health_server():
    """خادم ويب مدمج لتشغيل البوت 24/7 على الاستضافات السحابية المجانية مثل Render أو Railway"""
    try:
        port = int(os.environ.get("PORT", 8080))
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        logger.warning(f"Health server info/warning: {e}")

# --- إدارة قاعدة البيانات فائقة السرعة مع دعم التزامن (DATABASE SETUP) ---
DB_FILE = "users.db"

def get_db():
    """فتح اتصال آمن وسريع بقاعدة البيانات مع تفعيل نمط WAL لمنع أي قفل."""
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init_db():
    with get_db() as conn:
        # جدول المستخدمين
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                daily_count INTEGER DEFAULT 0,
                total_downloads INTEGER DEFAULT 0,
                registered_date TEXT,
                last_active_date TEXT,
                premium_until TEXT DEFAULT NULL,
                total_spent REAL DEFAULT 0.0,
                is_banned INTEGER DEFAULT 0
            )
        """)
        
        # جدول المدفوعات والعمليات المالية
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                currency TEXT,
                method TEXT,
                paid_at TEXT,
                payload TEXT
            )
        """)

        # جدول المدراء المساعدين المصرح لهم
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                added_by INTEGER,
                added_at TEXT
            )
        """)

        # ترقية الجداول القديمة تلقائياً دون فقدان بيانات
        columns_to_add = [
            ("username", "TEXT"),
            ("first_name", "TEXT"),
            ("total_downloads", "INTEGER DEFAULT 0"),
            ("registered_date", "TEXT"),
            ("premium_until", "TEXT DEFAULT NULL"),
            ("total_spent", "REAL DEFAULT 0.0"),
            ("is_banned", "INTEGER DEFAULT 0"),
        ]
        for col_name, col_type in columns_to_add:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass

        conn.commit()

init_db()

# --- دوال إدارة المدراء والمستخدمين ---
def get_all_admin_ids() -> List[int]:
    """استرجاع قائمة بجميع معرفات المدراء المصرح لهم بإدارة البوت."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM admins")
        rows = cur.fetchall()
    admins = [ADMIN_ID]
    for r in rows:
        if r[0] not in admins:
            admins.append(r[0])
    return admins

def is_admin(user_id: int) -> bool:
    """التحقق مما إذا كان المستخدم مديراً أو مالك البوت."""
    return user_id in get_all_admin_ids()

def is_owner(user_id: int) -> bool:
    """التحقق مما إذا كان المستخدم هو المالك الأساسي للبوت."""
    return user_id == ADMIN_ID

def add_admin_db(user_id: int, username: str, added_by: int) -> bool:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO admins (user_id, username, added_by, added_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, username, added_by, now_str))
        conn.commit()
    return True

def remove_admin_db(user_id: int) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        conn.commit()
    return True

def get_admins_list_info() -> List[Tuple]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id, username, added_by, added_at FROM admins ORDER BY added_at DESC")
        return cur.fetchall()

def get_user_by_query(query_str: str) -> Optional[dict]:
    """البحث عن مستخدم في قاعدة البيانات عبر الآيدي الرقمي أو معرف التيليجرام."""
    query_clean = query_str.strip()
    with get_db() as conn:
        cur = conn.cursor()
        if query_clean.isdigit():
            cur.execute("""
                SELECT user_id, username, first_name, daily_count, total_downloads,
                       registered_date, last_active_date, premium_until, total_spent, is_banned
                FROM users WHERE user_id = ?
            """, (int(query_clean),))
        else:
            uname = query_clean if query_clean.startswith("@") else f"@{query_clean}"
            cur.execute("""
                SELECT user_id, username, first_name, daily_count, total_downloads,
                       registered_date, last_active_date, premium_until, total_spent, is_banned
                FROM users WHERE LOWER(username) = LOWER(?)
            """, (uname,))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "user_id": row[0],
            "username": row[1] or "بدون معرف",
            "first_name": row[2] or "مستخدم",
            "daily_count": row[3] or 0,
            "total_downloads": row[4] or 0,
            "registered_date": row[5] or "غير محدد",
            "last_active_date": row[6] or "غير محدد",
            "premium_until": row[7],
            "total_spent": row[8] or 0.0,
            "is_banned": bool(row[9]),
        }

def get_or_update_user(user: User):
    """تسجيل المستخدم إذا كان جديداً، وتحديث نشاطه وحساب مدة اشتراكه ورصيده وحالة الحظر."""
    today = date.today()
    today_str = today.isoformat()
    user_id = user.id
    username = f"@{user.username}" if user.username else "بدون معرف"
    first_name = user.first_name or "مستخدم"

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT daily_count, last_active_date, premium_until, total_spent, total_downloads, is_banned FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = cur.fetchone()

        if not row:
            # تسجيل مستخدم جديد لأول مرة
            cur.execute("""
                INSERT INTO users (
                    user_id, username, first_name, daily_count, total_downloads,
                    registered_date, last_active_date, premium_until, total_spent, is_banned
                ) VALUES (?, ?, ?, 0, 0, ?, ?, NULL, 0.0, 0)
            """, (user_id, username, first_name, today_str, today_str))
            conn.commit()
            return {
                "count": 0,
                "is_premium": False,
                "days_left": 0,
                "total_spent": 0.0,
                "total_downloads": 0,
                "is_banned": False,
                "is_new": True,
            }

        daily_count, last_date, premium_until, total_spent, total_downloads, is_banned = row
        total_spent = total_spent or 0.0
        total_downloads = total_downloads or 0
        is_banned = bool(is_banned)

        # تصفير العداد اليومي عند حلول منتصف الليل
        if last_date != today_str:
            daily_count = 0

        # تحديث بيانات المستخدم وتاريخ آخر ظهور
        cur.execute("""
            UPDATE users SET 
                username = ?, 
                first_name = ?, 
                daily_count = ?, 
                last_active_date = ? 
            WHERE user_id = ?
        """, (username, first_name, daily_count, today_str, user_id))

        # فحص صلاحية اشتراك البريميوم
        is_premium = False
        days_left = 0
        if premium_until:
            try:
                expiry_date = datetime.strptime(premium_until, "%Y-%m-%d").date()
                if expiry_date >= today:
                    is_premium = True
                    days_left = (expiry_date - today).days
                else:
                    cur.execute("UPDATE users SET premium_until = NULL WHERE user_id = ?", (user_id,))
            except ValueError:
                pass

        conn.commit()
        return {
            "count": daily_count,
            "is_premium": is_premium,
            "days_left": days_left,
            "total_spent": total_spent,
            "total_downloads": total_downloads,
            "is_banned": is_banned,
            "is_new": False,
        }

def record_payment(user_id: int, amount: float, currency: str, method: str, payload: str, days: int = 30):
    """تسجيل الدفع في السجل المالي وتمديد اشتراك البريميوم للمستخدم."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = date.today()

    with get_db() as conn:
        cur = conn.cursor()
        
        # 1. إدراج الفاتورة في السجل
        cur.execute("""
            INSERT INTO payments (user_id, amount, currency, method, paid_at, payload)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, amount, currency, method, now_str, payload))

        # 2. تحديث إجمالي ما دفعه المستخدم
        cur.execute("UPDATE users SET total_spent = COALESCE(total_spent, 0.0) + ? WHERE user_id = ?", (amount, user_id))

        # 3. حساب تاريخ الانتهاء الجديد
        cur.execute("SELECT premium_until FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        start_date = today
        if row and row[0]:
            try:
                curr_expiry = datetime.strptime(row[0], "%Y-%m-%d").date()
                if curr_expiry > today:
                    start_date = curr_expiry
            except ValueError:
                pass

        new_expiry = (start_date + timedelta(days=days)).isoformat()
        cur.execute("UPDATE users SET premium_until = ? WHERE user_id = ?", (new_expiry, user_id))
        conn.commit()
        return new_expiry

def increment_confirmed_download(user_id: int, is_free_tier: bool):
    """يتم استدعاء هذا التابع فقط بعد نجاح إرسال الفيديو للمستخدم للتأكيد."""
    with get_db() as conn:
        if is_free_tier:
            conn.execute(
                "UPDATE users SET daily_count = daily_count + 1, total_downloads = total_downloads + 1 WHERE user_id = ?",
                (user_id,)
            )
        else:
            conn.execute(
                "UPDATE users SET total_downloads = total_downloads + 1 WHERE user_id = ?",
                (user_id,)
            )
        conn.commit()

# --- رسالة حظر الميزات المدفوعة (PREMIUM GATE) ---
PREMIUM_GATE_TEXT = (
    "🔒 **ميزة حصرية لمشتركي باقة البريميوم 💎**\n\n"
    "هذه الأداة المتقدمة متاحة فقط للمشتركين في الباقة المدفوعة.\n\n"
    "✨ **مميزات البريميوم الحصرية:**\n"
    "• 🌐 ترجمة الفيديوهات لـ 14 لغة عالمية (Hardsub + SRT)\n"
    "• ✂️ قص المقاطع السحابي (Trim) بأي مدة\n"
    "• 🎞️ صانع GIF المتحرك عالي الجودة\n"
    "• 📹 دقات العرض العالية (1080p / 2K / 4K)\n"
    "• ⚡ سرعة معالجة وتحويل فائقة\n"
    "• ♾️ تحميل غير محدود بدون حد يومي\n\n"
    "💰 **السعر:** 5.00$ فقط لمدة 30 يوم (أو 250 نجمة تيليجرام)\n\n"
    "🚀 اضغط الزر أدناه للترقية والاستمتاع بجميع المميزات فوراً!"
)

def get_premium_gate_keyboard():
    """إنشاء لوحة أزرار ترقية البريميوم عند محاولة استخدام ميزة مدفوعة."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 ترقية الحساب الآن (بريميوم)", callback_data="upgrade_menu")],
        [InlineKeyboardButton("💬 تواصل مع المدير للاشتراك", url=ADMIN_URL)],
    ])

# --- محرك الإحصائيات (ADMIN STATS ENGINE) ---
def get_database_stats():
    """حساب المقاييس الحية للوحة تحكم المدير."""
    today = date.today().isoformat()
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM users WHERE premium_until >= ?", (today,))
        active_paid = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM users WHERE total_spent > 0 AND (premium_until < ? OR premium_until IS NULL)", (today,))
        expired_paid = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM users WHERE total_spent = 0 OR total_spent IS NULL")
        pure_free = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        banned_users = cur.fetchone()[0]

        cur.execute("SELECT SUM(total_downloads) FROM users")
        total_downloads = cur.fetchone()[0] or 0

        cur.execute("SELECT SUM(amount) FROM payments WHERE currency = 'USD'")
        usd_revenue = cur.fetchone()[0] or 0.0

        cur.execute("SELECT SUM(amount) FROM payments WHERE currency = 'XTR'")
        stars_revenue = cur.fetchone()[0] or 0.0

        return {
            "total_users": total_users,
            "active_paid": active_paid,
            "expired_paid": expired_paid,
            "pure_free": pure_free,
            "banned_users": banned_users,
            "total_downloads": total_downloads,
            "usd_revenue": usd_revenue,
            "stars_revenue": int(stars_revenue),
        }

# --- أدوات تحويل الصور واستخراجها (IMAGE ENGINE) ---
def convert_image_format(input_path: str, target_format: str) -> str:
    """تحويل الصورة إلى أي صيغة مطلوبة (PNG, JPG, WEBP, PDF, ICO)."""
    target = target_format.upper().strip()
    base, _ = os.path.splitext(input_path)
    
    with Image.open(input_path) as img:
        if target == "PDF":
            out_path = f"{base}_conv.pdf"
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(out_path, "PDF", resolution=100.0)
            return out_path
        elif target in ("JPEG", "JPG"):
            out_path = f"{base}_conv.jpg"
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(out_path, "JPEG", quality=95)
            return out_path
        elif target == "PNG":
            out_path = f"{base}_conv.png"
            img.save(out_path, "PNG", optimize=True)
            return out_path
        elif target == "WEBP":
            out_path = f"{base}_conv.webp"
            img.save(out_path, "WEBP", quality=95)
            return out_path
        elif target == "ICO":
            out_path = f"{base}_conv.ico"
            # resize for icon if needed
            icon_img = img.copy()
            icon_img.thumbnail((256, 256))
            icon_img.save(out_path, "ICO")
            return out_path
        else:
            return input_path

async def extract_images_from_link(url: str, user_id: int) -> Tuple[List[str], str]:
    """استخراج الصور بأعلى دقة متوفرة من الروابط المباشرة أو منصات التواصل."""
    clean_url_base = url.split("?")[0].lower()
    
    # 1. فحص إذا كان رابط مباشر لملف صورة
    if any(clean_url_base.endswith(ext) for ext in IMAGE_EXTENSIONS):
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            if resp.status_code == 200:
                _, ext = os.path.splitext(clean_url_base)
                ext = ext or ".jpg"
                fp = os.path.join(DOWNLOAD_DIR, f"{user_id}_direct_img{ext}")
                with open(fp, "wb") as f:
                    f.write(resp.content)
                return [fp], "صورة مباشرة"

    # 2. استخراج الصور من إنستغرام، بينترست، تيك توك، تويتر، أو يوتيوب عبر yt-dlp
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "nocheckcertificate": True,
        "socket_timeout": 25,
        "geo_bypass": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android", "mweb"]
            }
        }
    }
    if os.path.exists(COOKIES_FILE):
        ydl_opts["cookiefile"] = COOKIES_FILE

    loop = asyncio.get_running_loop()
    def get_info():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)

    info = await loop.run_in_executor(None, get_info)
    title = info.get("title", "صورة")
    
    image_urls = []
    # فحص ألبومات الصور (Carousels/Entries) لاستخراج جميع صور المنشور
    if "entries" in info and info["entries"]:
        for entry in info["entries"]:
            if not entry:
                continue
            chosen_url = None
            if entry.get("thumbnails"):
                best_t = max(entry["thumbnails"], key=lambda t: t.get("preference", 0) or (t.get("width", 0) * t.get("height", 0)) or t.get("width", 0))
                chosen_url = best_t.get("url")
            if not chosen_url and entry.get("url"):
                chosen_url = entry["url"]
            if chosen_url and chosen_url not in image_urls:
                image_urls.append(chosen_url)
    
    # فحص الصور المفردة أو الأغلفة فائقة الدقة
    if not image_urls and info.get("thumbnails"):
        best_t = max(info["thumbnails"], key=lambda t: t.get("preference", 0) or (t.get("width", 0) * t.get("height", 0)))
        if best_t.get("url"):
            image_urls.append(best_t["url"])
            
    if not image_urls and info.get("url"):
        image_urls.append(info.get("url"))

    if not image_urls:
        raise ValueError("لم يتم العثور على صور قابلة للتنزيل في هذا الرابط.")

    # تحديد عدد الصور حسب المنشور بالضبط (مع مراعاة الحد الأقصى لألبوم تيليجرام: 10 صور)
    target_urls = image_urls[:10]

    downloaded = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for idx, i_url in enumerate(target_urls):
            if not i_url:
                continue
            resp = await client.get(i_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            if resp.status_code == 200:
                ext = ".jpg"
                if ".png" in i_url.lower():
                    ext = ".png"
                elif ".webp" in i_url.lower():
                    ext = ".webp"
                fp = os.path.join(DOWNLOAD_DIR, f"{user_id}_img_{idx}{ext}")
                with open(fp, "wb") as f:
                    f.write(resp.content)
                downloaded.append(fp)

    return downloaded, title

# --- أوامر المستخدمين العامة (USER COMMANDS) ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_status = get_or_update_user(update.effective_user)
    
    if user_status["is_banned"]:
        await update.message.reply_text("⛔ عذراً، لقد تم حظرك من استخدام هذا البوت من قبل الإدارة.")
        return

    if user_status["is_premium"]:
        sub_info = (
            f"💎 **نوع الحساب:** مشترك بريميوم ✨\n"
            f"⏳ **صلاحية الاشتراك:** متبقي {user_status['days_left']} يوم\n"
            f"⚡ **التحميل:** غير محدود وبأقصى سرعة (فيديوهات حتى 4K وصوت وصور)\n"
            f"📁 **إجمالي تحميلاتك:** {user_status['total_downloads']} ملف"
        )
    else:
        remaining_today = max(0, DAILY_FREE_LIMIT - user_status['count'])
        sub_info = (
            f"👤 **نوع الحساب:** الباقة المجانية\n"
            f"📊 **استهلاكك اليوم:** {user_status['count']} من أصل {DAILY_FREE_LIMIT} فيديوهات\n"
            f"🔄 **المتبقي لك اليوم:** {remaining_today} فيديوهات\n"
            f"🖼️ **الصور والصوتيات:** مجانية تماماً وغير محدودة لجميع الفئات!\n"
            f"📁 **إجمالي تحميلاتك:** {user_status['total_downloads']} ملف"
        )

    user = update.effective_user
    user_id_line = f"🆔 **الآيدي الخاص بك:** `{user.id}` _(اضغط عليه للنسخ)_\n\n"
    admin_hint = "\n\n👑 **أنت مدير في البوت:** يمكنك فتح لوحة التحكم عبر `/admin`" if is_admin(user.id) else ""

    keyboard = [
        [
            InlineKeyboardButton("💎 ترقية الحساب (بريميوم)", callback_data="upgrade_menu"),
            InlineKeyboardButton("ℹ️ دليل الاستخدام", callback_data="show_help"),
        ],
        [
            InlineKeyboardButton("💬 تواصل مع المدير 👨‍💼", url=ADMIN_URL),
        ]
    ]

    msg = (
        f"👋 أهلاً بك في **بوت OmniFetch الشامل واستوديو الوسائط الذكي**!\n\n"
        f"🎯 **الميزات الفريدة والحصرية في البوت:**\n"
        f"• 🌐 **ترجمة الفيديوهات (14 لغة عالمية):** استخراج الكلام ودمج الترجمة الملونة الاحترافية بالفيديو فورياً (عربي، إنجليزي، كردي، تركي، فارسي، فرنسي، إسباني، ألماني، روسي، صيني، ياباني، كوري، إيطالي، برتغالي).\n"
        f"• ✂️ **قص المقاطع السحابي (Trim):** تحديد الثواني المطلوبة وتنزيلها مقصوصة فوراً.\n"
        f"• 🎞️ **صانع الـ GIF:** تحويل أي لقطة فيديو إلى GIF متحرك عالي الجودة.\n"
        f"• 📹 **تحميل الفيديوهات:** يوتيوب، تيك توك، إنستغرام، فيسبوك، X (تويتر)، بينترست.\n"
        f"• 🎵 **صوتيات عالية النقاوة:** MP3 (320kbps / 192kbps / 128kbps).\n"
        f"• 🖼️ **الصور والألبومات:** تنزيل وتحويل فوري (PNG, JPG, WEBP, PDF).\n\n"
        f"{user_id_line}"
        f"{sub_info}\n\n"
        f"💬 **للتواصل مع المدير والدعم:** [{ADMIN_USERNAME}]({ADMIN_URL})"
        f"{admin_hint}"
    )
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دليل استخدام البوت والمنصات المدعومة."""
    msg_target = update.effective_message
    if not msg_target:
        return
    help_text = (
        "📖 **دليل استخدام واستوديو بوت OmniFetch:**\n\n"
        "1️⃣ **كيفية التحميل والترجمة:**\n"
        "أرسل أي رابط فيديو أو صورة. ستظهر لك قائمة فورية تتيح لك:\n"
        "• 📹 **تحميل الفيديو بدقات متعددة**\n"
        "• 🌐 **ترجمة الفيديو لـ 14 لغة عالمية** (دمج الترجمة بخط أنيق + الحصول على ملف SRT)\n"
        "• ✂️ **قص مقطع محدد** بإرسال وقت البداية والنهاية (مثلاً: `00:15 00:45`)\n"
        "• 🎞️ **تحويل الفيديو إلى GIF متحرك**\n"
        "• 🎵 **استخراج الصوت بصيغة MP3 عالية النقاوة**\n\n"
        "2️⃣ **دعم الصور (مجاني للجميع وبأي صيغة):**\n"
        "• إرسال أي رابط إنستغرام أو بينترست لتحميل الألبوم كاملاً بدقة أصلية.\n"
        "• إرسال أي صورة مباشرة للبوت لتحويلها إلى (PNG, JPG, WEBP, PDF) فوراً!\n\n"
        "3️⃣ **الجودات والدقات:**\n"
        "• **فيديو:** مجاني حتى 720p، ودقات (1080p, 2K, 4K) للبريميوم.\n"
        "• **الترجمة:** متاحة لـ 14 لغة عالمية بدقة 720p واضحة جداً.\n"
        "• **صوت وصور:** مجانية لجميع الفئات بأعلى دقة متوفرة.\n\n"
        "4️⃣ **الأوامر المتوفرة:**\n"
        "• `/start` — بدء البوت وتحديث بياناتك\n"
        "• `/status` أو `/me` — رصيدك اليومي وحالة حسابك\n"
        "• `/upgrade` — ترقية الحساب إلى بريميوم\n"
        "• `/ping` — فحص سرعة استجابة البوت\n"
        "• `/id` — معرفة رقم الآيدي الخاص بك للنسخ\n"
        "• `/contact` — التواصل مع المدير والدعم\n"
        "• `/help` — عرض دليل الاستخدام الكامل\n\n"
        f"📞 **للتواصل المباشر مع المدير:** `{ADMIN_USERNAME}`"
    )
    keyboard = [
        [InlineKeyboardButton("💬 مراسلة المدير @CYS_ALI3", url=ADMIN_URL)]
    ]
    await msg_target.reply_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر التواصل المباشر مع المدير."""
    text = (
        "👨‍💼 **التواصل مع إدارة بوت OmniFetch:**\n\n"
        f"• **معرف المدير المباشر:** [{ADMIN_USERNAME}]({ADMIN_URL})\n"
        "• للاستفسار عن الاشتراكات وتفعيل البريميوم يدوياً (زين كاش، آسيا حوالة، USDT، تحويل بنكي).\n"
        "• للإبلاغ عن أي رابط أو اقتراح ميزة جديدة.\n\n"
        "اضغط على الزر أدناه لبدء المحادثة فوراً:"
    )
    keyboard = [
        [InlineKeyboardButton("💬 فتح محادثة مع المدير @CYS_ALI3", url=ADMIN_URL)]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def my_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض الآيدي الخاص بالمستخدم لتسهيل الاشتراك اليدوي أو الدعم."""
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton("💬 إرسال الآيدي للمدير لتفعيل بريميوم", url=ADMIN_URL)]
    ]
    await update.message.reply_text(
        f"🆔 **معلومات حسابك:**\n\n"
        f"👤 الاسم: {user.first_name}\n"
        f"🏷 المعرف: @{user.username if user.username else 'بدون معرف'}\n"
        f"🔑 رقم الآيدي (ID): `{user.id}`",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def upgrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_target = update.effective_message
    user_status = get_or_update_user(update.effective_user)
    if user_status["is_banned"]:
        if msg_target:
            await msg_target.reply_text("⛔ عذراً، أنت محظور من استخدام البوت.")
        return

    keyboard = [
        [InlineKeyboardButton("💳 الدفع ببطاقة فيزا / ماستركارد ($5.00)", callback_data="pay_card_30")],
        [InlineKeyboardButton("⭐ الدفع بنجوم تيليجرام (250 نجمة)", callback_data="pay_stars_30")],
        [InlineKeyboardButton("⚡ الدفع بالعملات الرقمية / تحويل يدوي", callback_data="pay_crypto")],
        [InlineKeyboardButton("💬 تواصل مع المدير للاشتراك اليدوي", url=ADMIN_URL)],
    ]
    if msg_target:
        await msg_target.reply_text(
            "💎 **اشتراك OmniFetch بريميوم (لمدة 30 يوماً)**\n\n"
            "• **المدة:** 30 يوماً كاملة\n"
            "• **السعر:** 5.00$ فقط (أو 250 نجمة تيليجرام)\n\n"
            "🔓 **الميزات الحصرية التي ستُفتح لك:**\n"
            "• 🌐 ترجمة الفيديوهات لـ 14 لغة عالمية (Hardsub + SRT)\n"
            "• ✂️ قص المقاطع السحابي (Trim) بأي مدة وأعلى جودة\n"
            "• 🎞️ صانع GIF المتحرك عالي الجودة\n"
            "• 📹 دقات العرض الفائقة: **1080p و 2K و 4K**\n"
            "• ♾️ تحميل فيديو **غير محدود** بدون حد يومي\n"
            "• ⚡ أولوية قصوى وأعلى سرعة معالجة وتحويل\n"
            "• 🎵 صوت ومقاطع صوتية بأقصى جودة MP3\n\n"
            f"📞 للدفع بالتحويل المحلي أو العملات الرقمية تواصل مع المدير: `{ADMIN_USERNAME}`\n\n"
            "اختر وسيلة الدفع التي تناسبك:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

async def translate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر الترجمة المباشر عبر السلاش: /translate أو /tr"""
    user_status = get_or_update_user(update.effective_user)
    if user_status["is_banned"]:
        await update.message.reply_text("⛔ عذراً، أنت محظور من استخدام البوت.")
        return

    # --- بوابة البريميوم: الترجمة ميزة مدفوعة ---
    if not user_status["is_premium"]:
        await update.message.reply_text(
            PREMIUM_GATE_TEXT,
            reply_markup=get_premium_gate_keyboard(),
            parse_mode="Markdown"
        )
        return

    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)

    if len(parts) > 1 and ("http://" in parts[1] or "https://" in parts[1]):
        url_match = re.search(r"https?://[^\s]+", parts[1])
        if url_match:
            clean_url = clean_tracking_parameters(url_match.group(0).rstrip(").,;!?\"'"))
            context.user_data["pending_url"] = clean_url
            await update.message.reply_text(
                "🌐 **ميزة ترجمة الفيديوهات الاحترافية (AI Subtitles & Video Translation):**\n\n"
                "🎯 **اختر اللغة التي تريد ترجمة ودمج الكلام بها داخل الفيديو (14 لغة عالمية):**\n\n"
                "✨ سيتم دمج الترجمة على الفيديو بخط واضح وظل أنيق (Hardsub) مع إرسال ملف SRT المنفصل.",
                reply_markup=get_translation_keyboard(include_back=False),
                parse_mode="Markdown"
            )
            return

    context.user_data["awaiting_translate_url"] = True
    await update.message.reply_text(
        "🌐 **أداة ترجمة الفيديوهات لـ 14 لغة عالمية:**\n\n"
        "أرسل الآن رابط الفيديو المطلوب ترجمته (يوتيوب، بودكاست، محاضرات، تيك توك، إلخ).\n\n"
        "💡 يمكنك أيضاً كتابة الأمر متبوعاً بالرابط مباشرة، مثال:\n"
        "`/translate https://youtu.be/...`",
        parse_mode="Markdown"
    )

async def trim_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر قص الفيديو المباشر عبر السلاش: /trim"""
    user_status = get_or_update_user(update.effective_user)
    if user_status["is_banned"]:
        await update.message.reply_text("⛔ عذراً، أنت محظور من استخدام البوت.")
        return

    # --- بوابة البريميوم: القص ميزة مدفوعة ---
    if not user_status["is_premium"]:
        await update.message.reply_text(
            PREMIUM_GATE_TEXT,
            reply_markup=get_premium_gate_keyboard(),
            parse_mode="Markdown"
        )
        return

    text = (update.message.text or "").strip()
    parts = text.split()
    url_found = None
    for p in parts[1:]:
        if p.startswith("http://") or p.startswith("https://"):
            url_found = p
            break

    if url_found:
        clean_url = clean_tracking_parameters(url_found)
        context.user_data["pending_url"] = clean_url
        context.user_data["awaiting_trim"] = True
        await update.message.reply_text(
            "✂️ **أداة قص مقطع محدد من الفيديو (Trim Video):**\n\n"
            "أرسل الآن وقت البداية ووقت النهاية للمقطع المطلوب.\n"
            "📝 أمثلة:\n"
            "• `00:15 00:45`\n"
            "• `01:00 01:30`\n"
            "• `10 30`",
            parse_mode="Markdown"
        )
        return

    context.user_data["awaiting_trim_url"] = True
    await update.message.reply_text(
        "✂️ **أداة قص المقاطع السحابية:**\n\n"
        "أرسل الآن رابط الفيديو المطلوب قصه.\n\n"
        "💡 يمكنك أيضاً كتابة الأمر متبوعاً بالرابط مباشرة، مثال:\n"
        "`/trim https://youtu.be/...`",
        parse_mode="Markdown"
    )

async def gif_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر تحويل الفيديو إلى GIF عبر السلاش: /gif"""
    user_status = get_or_update_user(update.effective_user)
    if user_status["is_banned"]:
        await update.message.reply_text("⛔ عذراً، أنت محظور من استخدام البوت.")
        return

    # --- بوابة البريميوم: صانع GIF ميزة مدفوعة ---
    if not user_status["is_premium"]:
        await update.message.reply_text(
            PREMIUM_GATE_TEXT,
            reply_markup=get_premium_gate_keyboard(),
            parse_mode="Markdown"
        )
        return

    text = (update.message.text or "").strip()
    parts = text.split()
    url_found = None
    for p in parts[1:]:
        if p.startswith("http://") or p.startswith("https://"):
            url_found = p
            break

    if url_found:
        clean_url = clean_tracking_parameters(url_found)
        context.user_data["pending_url"] = clean_url
        status_msg = await update.message.reply_text("⏳ _جاري التحويل لـ GIF..._", parse_mode="Markdown")
        class CmdQuery:
            def __init__(self, msg): self._msg = msg
            async def answer(self): pass
            async def edit_message_text(self, t, parse_mode=None):
                return await self._msg.edit_text(t, parse_mode=parse_mode)
            @property
            def message(self): return self._msg
        dummy_up = Update(update.update_id, callback_query=CmdQuery(status_msg))
        await process_video_to_gif(dummy_up, context)
        return

    context.user_data["awaiting_gif_url"] = True
    await update.message.reply_text(
        "🎞️ **أداة تحويل الفيديو إلى GIF متحرك:**\n\n"
        "أرسل الآن رابط الفيديو الذي تريد تحويله إلى GIF متحرك عالي الجودة.\n\n"
        "💡 يمكنك أيضاً كتابة الأمر متبوعاً بالرابط مباشرة، مثال:\n"
        "`/gif https://youtu.be/...`",
        parse_mode="Markdown"
    )

# --- لوحة تحكم الإدارة الشاملة وبطاقة المستخدمين (ADMIN CONTROL PANEL & USER ENGINE) ---
def render_user_card(user_info: dict) -> Tuple[str, InlineKeyboardMarkup]:
    """توليد بطاقة المستخدم التفاعلية مع كافة بياناته وأزرار الإجراءات الفورية."""
    u_id = user_info["user_id"]
    today_str = date.today().isoformat()
    prem_until = user_info.get("premium_until")
    is_prem = bool(prem_until and prem_until >= today_str)
    
    prem_status = f"💎 بريميوم نشط (ينتهي: `{prem_until}`)" if is_prem else "👤 باقة مجانية"
    ban_status = "🔴 محظور ومطرود" if user_info["is_banned"] else "🟢 نشط (غير محظور)"

    card_text = (
        f"👤 **بطاقة إدارة المستخدم:**\n\n"
        f"• **الاسم:** {user_info['first_name']}\n"
        f"• **المعرف:** {user_info['username']}\n"
        f"• **الآيدي (ID):** `{u_id}`\n"
        f"• **الاشتراك:** {prem_status}\n"
        f"• **حالة الحساب:** {ban_status}\n"
        f"• **تاريخ التسجيل:** `{user_info['registered_date']}`\n"
        f"• **آخر نشاط:** `{user_info['last_active_date']}`\n"
        f"• **تحميلات اليوم:** `{user_info['daily_count']}` / {DAILY_FREE_LIMIT}\n"
        f"• **إجمالي التحميلات:** `{user_info['total_downloads']}` ملف\n"
        f"• **إجمالي المدفوعات:** `${user_info['total_spent']:.2f}`\n\n"
        f"⚡ **الإجراءات المتاحة لهذا المستخدم:**"
    )

    ban_btn_text = "🟢 فك الحظر عن الحساب" if user_info["is_banned"] else "🚫 حظر وطرد المستخدم"
    ban_cb = f"act_unban_{u_id}" if user_info["is_banned"] else f"act_ban_{u_id}"

    keyboard = [
        [
            InlineKeyboardButton("🎁 منح شهر مجاني (30 يوم)", callback_data=f"act_grant_30_{u_id}"),
            InlineKeyboardButton("💎 منح أسبوع (7 أيام)", callback_data=f"act_grant_7_{u_id}"),
        ],
        [
            InlineKeyboardButton(ban_btn_text, callback_data=ban_cb),
            InlineKeyboardButton("🔄 تصفير عداد اليوم", callback_data=f"act_reset_daily_{u_id}"),
        ],
        [
            InlineKeyboardButton("❌ إلغاء البريميوم", callback_data=f"act_revoke_prem_{u_id}"),
            InlineKeyboardButton("📩 إرسال رسالة خاصة", callback_data=f"act_prompt_msg_{u_id}"),
        ],
        [
            InlineKeyboardButton("🔍 بحث عن آيدي آخر", callback_data="search_user_prompt"),
            InlineKeyboardButton("🔙 رجوع للوحة التحكم", callback_data="admin_panel_home"),
        ]
    ]

    return card_text, InlineKeyboardMarkup(keyboard)

async def admin_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فتح لوحة تحكم الإدارة الكاملة بأزرار تفاعلية."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ عذراً، هذا الأمر مخصص لمدراء البوت فقط.")
        return

    owner_badge = "👑 **(مالك البوت)**" if is_owner(user_id) else "🛡️ **(مدير مصرح)**"
    keyboard = [
        [
            InlineKeyboardButton("🔍 البحث عن مستخدم بالآيدي", callback_data="search_user_prompt"),
            InlineKeyboardButton("📊 عرض الإحصائيات", callback_data="admin_stats"),
        ],
        [
            InlineKeyboardButton("👑 قائمة المشتركين", callback_data="admin_users"),
            InlineKeyboardButton("🚫 قائمة المحظورين", callback_data="admin_banned"),
        ],
        [
            InlineKeyboardButton("👥 إدارة المدراء (Admins)", callback_data="manage_admins"),
            InlineKeyboardButton("💾 نسخة احتياطية للقاعدة", callback_data="admin_backup_db"),
        ]
    ]

    panel_text = (
        f"👑 **لوحة تحكم إدارة بوت OmniFetch** {owner_badge}\n\n"
        "يمكنك إدارة البوت والمستخدمين عبر الأزرار التفاعلية أو الأوامر المباشرة:\n\n"
        "• `/user <ID>` أو `/find <ID>` — البحث الفوري عن مستخدم والتحكم بحسابه\n"
        "• `/stats` — الإحصائيات والأرباح بالتفصيل\n"
        "• `/ban <user_id>` — لحظر أي مستخدم فوراً\n"
        "• `/unban <user_id>` — لفك الحظر عن مستخدم\n"
        "• `/addpremium <user_id> <days>` — تفعيل أو تمديد بريميوم لمستخدم\n"
        "• `/broadcast <رسالتك>` — إذاعة رسالة لجميع المستخدمين\n"
        "• `/addadmin <user_id>` — تعيين مدير جديد (للمالك فقط)\n"
        "• `/deladmin <user_id>` — إزالة مدير (للمالك فقط)\n"
        "• `/admins` — عرض قائمة المدراء المشرفين\n"
        "• `/rempremium <user_id>` — إلغاء اشتراك البريميوم لمستخدم\n"
        "• `/resetdaily <user_id>` — تصفير عداد التحميل اليومي لمستخدم\n"
        "• `/clean` — تنظيف وحذف الملفات المؤقتة في السيرفر\n"
        "• `/backup` — سحب نسخة احتياطية فورية من قاعدة البيانات وإرسالها لك"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(panel_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(panel_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def user_search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر البحث المباشر عن مستخدم: /user <id_or_username>"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ غير مصرح لك.")
        return

    if not context.args:
        context.user_data["awaiting_user_search"] = True
        keyboard = [[InlineKeyboardButton("🔙 إلغاء والرجوع", callback_data="admin_panel_home")]]
        await update.message.reply_text(
            "🔍 **أرسل الآن رقم الآيدي (ID) أو المعرف (@username) للمستخدم الذي تريد البحث عنه:**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    query_str = context.args[0].strip()
    user_info = get_user_by_query(query_str)
    if not user_info:
        await update.message.reply_text(f"❌ لم يتم العثور على أي مستخدم بالمدخل: `{query_str}`", parse_mode="Markdown")
        return

    card_text, card_markup = render_user_card(user_info)
    await update.message.reply_text(card_text, reply_markup=card_markup, parse_mode="Markdown")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر إحصائيات المدير السريع /stats"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ غير مصرح لك.")
        return

    stats = get_database_stats()
    report = (
        "📊 **إحصائيات البوت اللحظية:**\n\n"
        f"👥 **إجمالي المستخدمين:** {stats['total_users']}\n"
        f"💎 **المشتركون النشطون:** {stats['active_paid']}\n"
        f"🔄 **مشتركون سابقون:** {stats['expired_paid']}\n"
        f"👤 **المستخدمون المجانيون:** {stats['pure_free']}\n"
        f"🚫 **المستخدمون المحظورون:** {stats['banned_users']}\n\n"
        f"🚀 **إجمالي التحميلات الناجحة:** {stats['total_downloads']}\n"
        f"💰 **الأرباح بالدولار:** ${stats['usd_revenue']:.2f}\n"
        f"⭐ **أرباح نجوم تيليجرام:** {stats['stars_revenue']} نجمة"
    )
    await update.message.reply_text(report, parse_mode="Markdown")

async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر إضافة مدير جديد (مخصص للمالك فقط): /addadmin <user_id>"""
    sender_id = update.effective_user.id
    if not is_owner(sender_id):
        await update.message.reply_text("⛔ عذراً، هذا الأمر مخصص لمالك البوت فقط.")
        return

    try:
        new_admin_id = int(context.args[0])
        u_info = get_user_by_query(str(new_admin_id))
        uname = u_info.get("username", "بدون معرف") if u_info else "مدير"
        add_admin_db(new_admin_id, uname, added_by=sender_id)
        await update.message.reply_text(f"👑 **تم بنجاح تعيين المستخدم `{new_admin_id}` مديراً في البوت.**", parse_mode="Markdown")
        try:
            await context.bot.send_message(
                chat_id=new_admin_id,
                text=(
                    "👑 **تهانينا!**\n\n"
                    "قام مالك البوت بتعيينك **مديراً (Admin)** في بوت OmniFetch!\n"
                    "يمكنك الآن استخدام الأمر `/admin` للوصول إلى لوحة التحكم وإدارة المستخدمين."
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass
    except (IndexError, ValueError):
        await update.message.reply_text("طريقة الاستخدام: `/addadmin <user_id>`\nمثال: `/addadmin 123456789`", parse_mode="Markdown")

async def del_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر إزالة مدير (مخصص للمالك فقط): /deladmin <user_id>"""
    sender_id = update.effective_user.id
    if not is_owner(sender_id):
        await update.message.reply_text("⛔ عذراً، هذا الأمر مخصص لمالك البوت فقط.")
        return

    try:
        target_id = int(context.args[0])
        if target_id == ADMIN_ID:
            await update.message.reply_text("⚠️ لا يمكنك إزالة مالك البوت الأساسي!")
            return
        remove_admin_db(target_id)
        await update.message.reply_text(f"✅ تم بنجاح إزالة صلاحيات الإدارة عن المستخدم `{target_id}`.", parse_mode="Markdown")
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="ℹ️ تم سحب صلاحيات الإدارة عن حسابك في البوت من قبل المالك."
            )
        except Exception:
            pass
    except (IndexError, ValueError):
        await update.message.reply_text("طريقة الاستخدام: `/deladmin <user_id>`\nمثال: `/deladmin 123456789`", parse_mode="Markdown")

async def list_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المدراء المشرفين: /admins"""
    sender_id = update.effective_user.id
    if not is_admin(sender_id):
        await update.message.reply_text("⛔ غير مصرح لك.")
        return

    admin_records = get_admins_list_info()
    lines = [
        "👥 **قائمة مدراء بوت OmniFetch:**\n",
        f"👑 **المالك الأساسي:** `{ADMIN_ID}` (`{ADMIN_USERNAME}`)"
    ]
    if admin_records:
        lines.append("\n🛡️ **المدراء المساعدون:**")
        for u_id, uname, added_by, added_at in admin_records:
            lines.append(f"• `{u_id}` | {uname} (تاريخ الإضافة: {added_at[:10]})")
    else:
        lines.append("\n_لا يوجد مدراء مساعدون مضافون حالياً._")

    if is_owner(sender_id):
        lines.append("\n💡 لإضافة مدير جديد: `/addadmin <user_id>`\n💡 لحذف مدير: `/deladmin <user_id>`")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

def create_sqlite_backup_file() -> Tuple[str, int]:
    """
    إنشاء نسخة احتياطية آمنة ومتوافقة 100% من قاعدة البيانات
    باستخدام آلية SQLite Online Backup API لمنع أي تضارب أو تلف أثناء الكتابة.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_filename = f"users_backup_{timestamp}.db"
    backup_path = os.path.join(DOWNLOAD_DIR, backup_filename)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    
    src_conn = sqlite3.connect(DB_FILE)
    dest_conn = sqlite3.connect(backup_path)
    try:
        src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()
        
    size_bytes = os.path.getsize(backup_path) if os.path.exists(backup_path) else 0
    return backup_path, size_bytes


async def send_database_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إرسال نسخة احتياطية من قاعدة البيانات للمدير."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        if update.callback_query:
            await update.callback_query.answer("⛔ غير مصرح لك.", show_alert=True)
        else:
            await update.message.reply_text("⛔ غير مصرح لك.")
        return

    msg_target = update.effective_message
    if update.callback_query:
        await update.callback_query.answer("⏳ جاري إنشاء النسخة الاحتياطية...")
    status_msg = await msg_target.reply_text("⏳ **جاري إنشاء وحزم نسخة احتياطية آمنة لقاعدة البيانات...**", parse_mode="Markdown")

    backup_path = None
    try:
        backup_path, size_bytes = await asyncio.to_thread(create_sqlite_backup_file)
        stats = get_database_stats()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        size_kb = size_bytes / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.2f} MB"
        
        caption = (
            "📦 **نسخة احتياطية لقاعدة البيانات (Database Backup)**\n\n"
            f"📅 **تاريخ الإنشاء:** `{now_str}`\n"
            f"👥 **إجمالي المستخدمين:** `{stats['total_users']}`\n"
            f"💎 **المشتركون النشطون:** `{stats['active_paid']}`\n"
            f"🚀 **إجمالي التحميلات:** `{stats['total_downloads']}`\n"
            f"💾 **حجم الملف:** `{size_str}`\n\n"
            "🔒 _احتفظ بهذا الملف في مكان آمن أو في رسائلك المحفوظة (Saved Messages). "
            "في حال حدوث أي طارئ يمكنك استعادة بيانات المستخدمين فوراً._"
        )
        
        with open(backup_path, "rb") as f:
            await update.effective_chat.send_document(
                document=f,
                filename=os.path.basename(backup_path),
                caption=caption,
                parse_mode="Markdown"
            )
            
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Failed to generate db backup: {e}", exc_info=True)
        if status_msg:
            await status_msg.edit_text(f"❌ **حدث خطأ أثناء أخذ النسخة الاحتياطية:** `{e}`")
        else:
            await msg_target.reply_text(f"❌ **حدث خطأ أثناء أخذ النسخة الاحتياطية:** `{e}`")
    finally:
        if backup_path and os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except Exception:
                pass


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر أخذ نسخة احتياطية فورية: /backup"""
    await send_database_backup(update, context)

# --- أوامر التوسعة الجديدة (EXPANDED COMMAND SUITE) ---

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تفاصيل الحساب والرصيد المتبقي للمستخدم: /status أو /me"""
    user = update.effective_user
    st = get_or_update_user(user)
    today = date.today().isoformat()
    is_prem = bool(st["premium_until"] and st["premium_until"] >= today)
    tier_badge = "💎 **عضو بريميوم (VIP)**" if is_prem else "👤 **عضو مجاني**"

    if is_prem:
        limit_text = "♾️ **غير محدود** (تحميل سريع وبدون قيود)"
        exp_text = f"📅 **تاريخ الانتهاء:** `{st['premium_until']}`"
    else:
        used = st["daily_downloads"]
        remaining = max(0, DAILY_FREE_LIMIT - used)
        limit_text = f"`{remaining}` من أصل `{DAILY_FREE_LIMIT}` فيديوهات متبقية اليوم"
        exp_text = "🔄 **تجديد الرصيد:** يومياً الساعة 12:00 منتصف الليل"

    status_card = (
        "📊 **بطاقة حالة الحساب الشخصي:**\n\n"
        f"👤 **الاسم:** {user.first_name}\n"
        f"🏷 **المعرف:** @{user.username if user.username else 'بدون معرف'}\n"
        f"🆔 **الآيدي:** `{user.id}`\n"
        f"👑 **نوع الحساب:** {tier_badge}\n"
        f"📥 **الرصيد المتاح:** {limit_text}\n"
        f"🚀 **إجمالي التحميلات السابقة:** `{st['total_downloads']}` عملية\n"
        f"{exp_text}\n"
    )

    keyboard = []
    if not is_prem:
        keyboard.append([InlineKeyboardButton("💎 ترقية الحساب إلى بريميوم", callback_data="upgrade_menu")])
    keyboard.append([InlineKeyboardButton("💬 مساعدة وتواصل", callback_data="show_help")])

    msg_target = update.effective_message
    if msg_target:
        await msg_target.reply_text(status_card, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فحص سرعة استجابة البوت وحالة السيرفر: /ping"""
    start_time = datetime.now()
    msg_target = update.effective_message
    sent_msg = await msg_target.reply_text("🏓 **Pong! جارٍ فحص استجابة السيرفر...**", parse_mode="Markdown")
    end_time = datetime.now()
    latency_ms = int((end_time - start_time).total_seconds() * 1000)

    if latency_ms < 250:
        speed_badge = "🚀 فائقة السرعة"
    elif latency_ms < 600:
        speed_badge = "⚡ جيدة وطبيعية"
    else:
        speed_badge = "⏳ متوسطة"

    await sent_msg.edit_text(
        f"🏓 **معلومات الاتصال وحالة السيرفر:**\n\n"
        f"⚡ **سرعة الاستجابة (Latency):** `{latency_ms} ms` ({speed_badge})\n"
        f"🟢 **حالة النظام:** جميع محركات التحميل والمعالجة تعمل بكفاءة 100%\n"
        f"⏱️ **توقيت السيرفر:** `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        parse_mode="Markdown"
    )


async def rem_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء اشتراك البريميوم لمستخدم: /rempremium <user_id>"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ غير مصرح لك.")
        return
    try:
        target_id = int(context.args[0])
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE users SET premium_until = NULL WHERE user_id = ?", (target_id,))
            conn.commit()

        await update.message.reply_text(f"✅ تم إلغاء اشتراك البريميوم للمستخدم `{target_id}` بنجاح.", parse_mode="Markdown")
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="ℹ️ **تم إنهاء اشتراك البريميوم لحسابك من قبل الإدارة.**\nيمكنك الاستمرار بالاستخدام المجاني أو التجديد عبر أمر /upgrade.",
                parse_mode="Markdown"
            )
        except Exception:
            pass
    except (IndexError, ValueError):
        await update.message.reply_text("طريقة الاستخدام: `/rempremium <user_id>`\nمثال: `/rempremium 123456789`", parse_mode="Markdown")


async def reset_daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تصفير العداد اليومي لمستخدم: /resetdaily <user_id>"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ غير مصرح لك.")
        return
    try:
        target_id = int(context.args[0])
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE users SET daily_downloads = 0 WHERE user_id = ?", (target_id,))
            conn.commit()

        await update.message.reply_text(f"✅ تم تصفير عداد التحميل اليومي للمستخدم `{target_id}` بنجاح.", parse_mode="Markdown")
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="🎁 **تمت إعادة ضبط رصيدك اليومي للتحميل! يمكنك الآن التحميل مجدداً.**",
                parse_mode="Markdown"
            )
        except Exception:
            pass
    except (IndexError, ValueError):
        await update.message.reply_text("طريقة الاستخدام: `/resetdaily <user_id>`\nمثال: `/resetdaily 123456789`", parse_mode="Markdown")


async def clean_temp_files_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنظيف الملفات المؤقتة في مجلد التحميلات: /clean"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ غير مصرح لك.")
        return

    status_msg = await update.message.reply_text("🧹 **جارٍ فحص وتنظيف الملفات المؤقتة...**", parse_mode="Markdown")
    deleted_count = 0
    freed_bytes = 0
    try:
        for fname in os.listdir(DOWNLOAD_DIR):
            fpath = os.path.join(DOWNLOAD_DIR, fname)
            if os.path.isfile(fpath):
                try:
                    sz = os.path.getsize(fpath)
                    os.remove(fpath)
                    deleted_count += 1
                    freed_bytes += sz
                except Exception:
                    pass

        freed_mb = freed_bytes / (1024 * 1024)
        await status_msg.edit_text(
            f"✅ **تم تنظيف المجلد المؤقت بنجاح!**\n\n"
            f"🗑️ **عدد الملفات المحذوفة:** `{deleted_count}` ملف\n"
            f"💾 **المساحة المحررة:** `{freed_mb:.2f} MB`",
            parse_mode="Markdown"
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ خطأ أثناء التنظيف: `{e}`")


async def post_init_commands(app):
    """تسجيل قوائم الأوامر الرسمية في واجهة تيليجرام تلقائياً (زر القائمة Menu)."""
    user_commands = [
        BotCommand("start", "🚀 بدء استخدام البوت وتحديث بياناتك"),
        BotCommand("translate", "🌐 ترجمة أي فيديو إلى 14 لغة عالمية"),
        BotCommand("trim", "✂️ قص جزء محدد من الفيديو"),
        BotCommand("gif", "🎞️ تحويل مقطع فيديو إلى GIF متحرك"),
        BotCommand("status", "📊 رصيدك اليومي وحالة حسابك الشخصي"),
        BotCommand("upgrade", "💎 ترقية الحساب إلى بريميوم والأسعار"),
        BotCommand("help", "📖 دليل الاستخدام والمنصات المدعومة"),
        BotCommand("contact", "💬 التواصل المباشر مع المدير والدعم"),
        BotCommand("id", "🆔 معرف الآيدي الخاص بك للنسخ"),
        BotCommand("ping", "⚡ فحص سرعة استجابة البوت والسيرفر"),
    ]
    try:
        await app.bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    except Exception as e:
        logger.error(f"Failed to set user commands menu: {e}")

    admin_commands = user_commands + [
        BotCommand("admin", "👑 لوحة التحكم والإدارة التفاعلية"),
        BotCommand("user", "🔍 البحث عن مستخدم والتحكم بحسابه"),
        BotCommand("stats", "📈 إحصائيات البوت اللحظية والأرباح"),
        BotCommand("backup", "💾 سحب نسخة احتياطية من قاعدة البيانات"),
        BotCommand("broadcast", "📢 إذاعة رسالة لجميع المستخدمين"),
        BotCommand("clean", "🧹 تنظيف الملفات المؤقتة من السيرفر"),
        BotCommand("admins", "👥 عرض وإدارة طاقم المشرفين"),
    ]
    admin_ids = get_all_admin_ids()
    for a_id in admin_ids:
        try:
            await app.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=a_id))
        except Exception:
            pass


async def admin_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة جميع أزرار لوحة تحكم المدير وبطاقات المستخدمين."""
    query = update.callback_query
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await query.answer("⛔ غير مصرح لك.", show_alert=True)
        return

    data = query.data

    if data == "admin_backup_db":
        await send_database_backup(update, context)
        return

    elif data == "admin_panel_home":
        await admin_panel_command(update, context)
        await query.answer()

    elif data == "search_user_prompt":
        context.user_data["awaiting_user_search"] = True
        keyboard = [[InlineKeyboardButton("🔙 رجوع للوحة التحكم", callback_data="admin_panel_home")]]
        await query.edit_message_text(
            "🔍 **أرسل الآن رقم الآيدي (ID) أو المعرف الخاص بالمستخدم:**\n\n"
            "_مثال: `123456789` أو `@username`_\n"
            "💡 يمكنك أيضاً استخدام الأمر المباشر في أي وقت: `/user <ID>`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        await query.answer()

    elif data == "manage_admins":
        admin_records = get_admins_list_info()
        lines = [
            "👥 **إدارة طاقم المدراء:**\n",
            f"👑 **المالك الأساسي:** `{ADMIN_ID}` (`{ADMIN_USERNAME}`)"
        ]
        if admin_records:
            lines.append("\n🛡️ **المدراء المساعدون:**")
            for u_id, uname, added_by, added_at in admin_records:
                lines.append(f"• `{u_id}` | {uname} (تاريخ الإضافة: {added_at[:10]})")
        else:
            lines.append("\n_لا يوجد مدراء مساعدون حالياً._")

        keyboard = []
        if is_owner(user_id):
            keyboard.append([InlineKeyboardButton("➕ إضافة مدير جديد", callback_data="prompt_add_admin")])
        keyboard.append([InlineKeyboardButton("🔙 رجوع للوحة التحكم", callback_data="admin_panel_home")])

        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        await query.answer()

    elif data == "prompt_add_admin":
        if not is_owner(user_id):
            await query.answer("⛔ مخصص لمالك البوت فقط.", show_alert=True)
            return
        context.user_data["awaiting_add_admin"] = True
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="manage_admins")]]
        await query.edit_message_text(
            "👑 **إضافة مدير جديد:**\n\nأرسل الآن رقم الآيدي (ID) الخاص بالمستخدم لتعيينه كمدير:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        await query.answer()

    elif data == "admin_stats":
        stats = get_database_stats()
        report = (
            "📊 **إحصائيات البوت اللحظية:**\n\n"
            f"👥 **إجمالي المستخدمين:** {stats['total_users']}\n"
            f"💎 **المشتركون النشطون:** {stats['active_paid']}\n"
            f"🔄 **مشتركون سابقون:** {stats['expired_paid']}\n"
            f"👤 **المستخدمون المجانيون:** {stats['pure_free']}\n"
            f"🚫 **المستخدمون المحظورون:** {stats['banned_users']}\n\n"
            f"🚀 **إجمالي التحميلات الناجحة:** {stats['total_downloads']}\n"
            f"💰 **الأرباح بالدولار:** ${stats['usd_revenue']:.2f}\n"
            f"⭐ **أرباح نجوم تيليجرام:** {stats['stars_revenue']} نجمة"
        )
        keyboard = [[InlineKeyboardButton("🔙 رجوع للوحة التحكم", callback_data="admin_panel_home")]]
        await query.edit_message_text(report, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        await query.answer()

    elif data == "admin_users":
        today = date.today().isoformat()
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT user_id, username, first_name, premium_until, total_spent, total_downloads 
                FROM users 
                WHERE total_spent > 0 
                ORDER BY total_spent DESC 
                LIMIT 15
            """)
            rows = cur.fetchall()

        if not rows:
            await query.answer("لا يوجد مشتركون دافعون حتى الآن.", show_alert=True)
            return

        lines = ["👑 **أبرز المشتركين في البوت:**\n"]
        for uid, uname, fname, exp, spent, dls in rows:
            status_icon = "🟢" if exp and exp >= today else "🔴"
            lines.append(f"{status_icon} `{uid}` | {uname} ({fname}) — المدفوع: ${spent:.2f} | الانتهاء: {exp or 'منتهي'} | التحميلات: {dls}")

        keyboard = [
            [InlineKeyboardButton("🔍 إدارة أي مستخدم بالآيدي", callback_data="search_user_prompt")],
            [InlineKeyboardButton("🔙 رجوع للوحة التحكم", callback_data="admin_panel_home")]
        ]
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        await query.answer()

    elif data == "admin_banned":
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id, username, first_name FROM users WHERE is_banned = 1")
            banned = cur.fetchall()

        if not banned:
            await query.answer("لا يوجد أي مستخدم محظور حالياً.", show_alert=True)
            return

        lines = ["🚫 **قائمة المستخدمين المحظورين:**\n"]
        for uid, uname, fname in banned:
            lines.append(f"• `{uid}` | {uname} ({fname})")

        keyboard = [
            [InlineKeyboardButton("🔍 فك حظر بالآيدي", callback_data="search_user_prompt")],
            [InlineKeyboardButton("🔙 رجوع للوحة التحكم", callback_data="admin_panel_home")]
        ]
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        await query.answer()

    # --- إجراءات بطاقة المستخدم الفورية (USER ACTIONS) ---
    elif data.startswith("act_grant_30_"):
        target_uid = int(data.replace("act_grant_30_", ""))
        new_expiry = record_payment(
            user_id=target_uid,
            amount=0.0,
            currency="COMP",
            method="منحة شهر مجاني من الإدارة",
            payload="gift_30",
            days=30
        )
        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=(
                    "🎁 **تهانينا! هدية خاصة من إدارة البوت** 🎉\n\n"
                    "تم منحك اشتراك **بريميوم مجاني لمدة 30 يوماً**!\n"
                    f"📅 ينتهي اشتراكك في: `{new_expiry}`\n\n"
                    "🚀 استمتع الآن بالتحميلات غير المحدودة وبأعلى جودة وسرعة!"
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass

        u_info = get_user_by_query(str(target_uid))
        if u_info:
            card_text, card_markup = render_user_card(u_info)
            await query.edit_message_text(f"✅ **تم منح شهر مجاني بنجاح وإشعار المستخدم!**\n\n{card_text}", reply_markup=card_markup, parse_mode="Markdown")
        await query.answer("تم تفعيل 30 يوم وإرسال الإشعار!", show_alert=True)

    elif data.startswith("act_grant_7_"):
        target_uid = int(data.replace("act_grant_7_", ""))
        new_expiry = record_payment(
            user_id=target_uid,
            amount=0.0,
            currency="COMP",
            method="منحة أسبوع مجاني من الإدارة",
            payload="gift_7",
            days=7
        )
        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=(
                    "💎 **هدية خاصة من إدارة البوت** 🎉\n\n"
                    "تم منحك اشتراك **بريميوم مجاني لمدة 7 أيام**!\n"
                    f"📅 ينتهي اشتراكك في: `{new_expiry}`\n\n"
                    "🚀 استمتع بالتحميلات غير المحدودة وبأعلى جودة!"
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass

        u_info = get_user_by_query(str(target_uid))
        if u_info:
            card_text, card_markup = render_user_card(u_info)
            await query.edit_message_text(f"✅ **تم منح أسبوع مجاني بنجاح وإشعار المستخدم!**\n\n{card_text}", reply_markup=card_markup, parse_mode="Markdown")
        await query.answer("تم تفعيل 7 أيام وإرسال الإشعار!", show_alert=True)

    elif data.startswith("act_ban_"):
        target_uid = int(data.replace("act_ban_", ""))
        if target_uid == ADMIN_ID:
            await query.answer("⚠️ لا يمكن حظر مالك البوت!", show_alert=True)
            return

        with get_db() as conn:
            conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (target_uid,))
            conn.commit()

        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text="⛔ **تنبيه من إدارة البوت:**\n\nلقد تم حظر حسابك وطرده من استخدام هذا البوت.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        u_info = get_user_by_query(str(target_uid))
        if u_info:
            card_text, card_markup = render_user_card(u_info)
            await query.edit_message_text(f"🚫 **تم حظر المستخدم بنجاح وإشعاره.**\n\n{card_text}", reply_markup=card_markup, parse_mode="Markdown")
        await query.answer("تم الحظر وإشعار المستخدم بنجاح!", show_alert=True)

    elif data.startswith("act_unban_"):
        target_uid = int(data.replace("act_unban_", ""))
        with get_db() as conn:
            conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (target_uid,))
            conn.commit()

        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text="🟢 **تنبيه من إدارة البوت:**\n\nتم فك الحظر عن حسابك بنجاح! يمكنك الآن استئناف استخدام البوت بشكل طبيعي.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        u_info = get_user_by_query(str(target_uid))
        if u_info:
            card_text, card_markup = render_user_card(u_info)
            await query.edit_message_text(f"🟢 **تم فك الحظر بنجاح وإشعار المستخدم.**\n\n{card_text}", reply_markup=card_markup, parse_mode="Markdown")
        await query.answer("تم فك الحظر وإشعار المستخدم!", show_alert=True)

    elif data.startswith("act_reset_daily_"):
        target_uid = int(data.replace("act_reset_daily_", ""))
        with get_db() as conn:
            conn.execute("UPDATE users SET daily_count = 0 WHERE user_id = ?", (target_uid,))
            conn.commit()

        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text="🔄 **تنبيه من إدارة البوت:**\n\nتمت إعادة تعيين رصيدك اليومي من الفيديوهات مجاناً! يمكنك متابعة التحميل الآن.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        u_info = get_user_by_query(str(target_uid))
        if u_info:
            card_text, card_markup = render_user_card(u_info)
            await query.edit_message_text(f"🔄 **تم تصفير العداد اليومي وإشعار المستخدم.**\n\n{card_text}", reply_markup=card_markup, parse_mode="Markdown")
        await query.answer("تم تصفير العداد اليومي!", show_alert=True)

    elif data.startswith("act_revoke_prem_"):
        target_uid = int(data.replace("act_revoke_prem_", ""))
        with get_db() as conn:
            conn.execute("UPDATE users SET premium_until = NULL WHERE user_id = ?", (target_uid,))
            conn.commit()

        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text="ℹ️ **تنبيه من إدارة البوت:**\n\nتم إنهاء اشتراك البريميوم لحسابك والعودة للباقة المجانية.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        u_info = get_user_by_query(str(target_uid))
        if u_info:
            card_text, card_markup = render_user_card(u_info)
            await query.edit_message_text(f"❌ **تم إلغاء البريميوم وإشعار المستخدم.**\n\n{card_text}", reply_markup=card_markup, parse_mode="Markdown")
        await query.answer("تم إلغاء البريميوم بنجاح!", show_alert=True)

    elif data.startswith("act_prompt_msg_"):
        target_uid = int(data.replace("act_prompt_msg_", ""))
        context.user_data["awaiting_user_msg_target"] = target_uid
        keyboard = [[InlineKeyboardButton("🔙 إلغاء", callback_data="search_user_prompt")]]
        await query.edit_message_text(
            f"✍️ **أرسل الآن نص الرسالة التي تريد توجيهها للمستخدم `{target_uid}`:**\n\n"
            "_ستصل الرسالة مباشرة للمستخدم عبر البوت باسم إدارة البوت._",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        await query.answer()

    # --- نظام الموافقة والرفض على طلبات الاشتراك اليدوية ---
    elif data.startswith("approve_sub_"):
        target_id = int(data.replace("approve_sub_", ""))
        new_expiry = record_payment(
            user_id=target_id,
            amount=5.0,
            currency="USD",
            method="موافقة يدوية من المدير",
            payload=f"admin_approved_{target_id}",
            days=30,
        )

        await query.edit_message_text(
            f"✅ **تمت الموافقة بنجاح!**\n"
            f"تم تفعيل اشتراك البريميوم لمدة 30 يوماً للمستخدم: `{target_id}`\n"
            f"📅 ينتهي الاشتراك في: `{new_expiry}`",
            parse_mode="Markdown"
        )

        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=(
                    f"🎉 **تهانينا! تمت موافقة المدير على طلب اشتراكك.**\n\n"
                    f"تم تفعيل باقة البريميوم لمدة **30 يوماً** بنجاح!\n"
                    f"📅 ينتهي اشتراكك في: `{new_expiry}`\n\n"
                    f"🚀 استمتع الآن بالتحميلات غير المحدودة وبأعلى جودة وسرعة! أرسل أي رابط للبدء."
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify approved user: {e}")

    elif data.startswith("reject_sub_"):
        target_id = int(data.replace("reject_sub_", ""))
        await query.edit_message_text(
            f"❌ **تم رفض طلب الاشتراك للمستخدم:** `{target_id}`",
            parse_mode="Markdown"
        )
        try:
            keyboard = [[InlineKeyboardButton("💬 مراسلة المدير مباشرة", url=ADMIN_URL)]]
            await context.bot.send_message(
                chat_id=target_id,
                text=(
                    "❌ **عذراً، تعذرت الموافقة على طلب اشتراكك من قِبل المدير.**\n\n"
                    f"إذا قمت بالتحويل أو واجهت مشكلة، يرجى مراسلة المدير مباشرة: `{ADMIN_USERNAME}`"
                ),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify rejected user: {e}")

# --- أوامر الحظر وفك الحظر والإذاعة للمدير ---
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر حظر مستخدم: /ban <user_id>"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ غير مصرح لك.")
        return

    try:
        target_user = int(context.args[0])
        if target_user == ADMIN_ID:
            await update.message.reply_text("⚠️ عذراً، لا يمكنك حظر مالك البوت!")
            return

        with get_db() as conn:
            conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (target_user,))
            conn.commit()

        await update.message.reply_text(f"🚫 تم بنجاح حظر المستخدم `{target_user}` ومنعه من استخدام البوت نهائياً.", parse_mode="Markdown")
        try:
            await context.bot.send_message(chat_id=target_user, text="⛔ لقد تم حظر حسابك من استخدام هذا البوت من قبل الإدارة.")
        except Exception:
            pass
    except (IndexError, ValueError):
        await update.message.reply_text("طريقة الاستخدام: `/ban <user_id>`\nمثال: `/ban 123456789`", parse_mode="Markdown")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر فك الحظر: /unban <user_id>"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ غير مصرح لك.")
        return

    try:
        target_user = int(context.args[0])
        with get_db() as conn:
            conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (target_user,))
            conn.commit()

        await update.message.reply_text(f"🟢 تم بنجاح فك الحظر عن المستخدم `{target_user}`.", parse_mode="Markdown")
        try:
            await context.bot.send_message(chat_id=target_user, text="🟢 تم فك الحظر عن حسابك، يمكنك الآن استخدام البوت بشكل طبيعي!")
        except Exception:
            pass
    except (IndexError, ValueError):
        await update.message.reply_text("طريقة الاستخدام: `/unban <user_id>`\nمثال: `/unban 123456789`", parse_mode="Markdown")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر إذاعة رسالة لجميع المستخدمين: /broadcast <الرسالة>"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ غير مصرح لك.")
        return

    msg_text = " ".join(context.args).strip()
    if not msg_text:
        await update.message.reply_text("يرجى كتابة نص الرسالة بعد الأمر.\nمثال: `/broadcast أهلاً بكم، تم تحديث البوت!`", parse_mode="Markdown")
        return

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE is_banned = 0")
        all_users = cur.fetchall()

    sent_count = 0
    await update.message.reply_text(f"📢 جاري إرسال الإذاعة إلى {len(all_users)} مستخدم...")
    for (uid,) in all_users:
        try:
            try:
                await context.bot.send_message(chat_id=uid, text=f"📢 **تنبيه من إدارة البوت:**\n\n{msg_text}", parse_mode="Markdown")
            except Exception:
                await context.bot.send_message(chat_id=uid, text=f"📢 تنبيه من إدارة البوت:\n\n{msg_text}")
            sent_count += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass

    await update.message.reply_text(f"✅ اكتملت الإذاعة بنجاح! تم التوصيل إلى {sent_count} مستخدم.")

async def add_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خاص بالمدراء: تفعيل أو تمديد اشتراك بريميوم لمستخدم يدوياً عبر: /addpremium <user_id> <days>"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ عذراً، هذا الأمر مخصص لمدراء البوت فقط.")
        return

    try:
        target_user = int(context.args[0])
        duration_days = int(context.args[1])
        new_expiry = record_payment(
            user_id=target_user,
            amount=0.0,
            currency="COMP",
            method="تفعيل يدوي من الإدارة",
            payload="manual_grant",
            days=duration_days,
        )
        await update.message.reply_text(
            f"✅ تم بنجاح تفعيل {duration_days} يوماً للمستخدم `{target_user}`.\n📅 ينتهي الاشتراك في: `{new_expiry}`",
            parse_mode="Markdown"
        )
        try:
            await context.bot.send_message(
                chat_id=target_user,
                text=f"🎁 **مبروك!** قامت الإدارة بمنحك اشتراك بريميوم لمدة {duration_days} يوماً!\n📅 ينتهي الاشتراك في: `{new_expiry}`"
            )
        except Exception:
            pass
    except (IndexError, ValueError):
        await update.message.reply_text("طريقة الاستخدام: `/addpremium <user_id> <days>`\nمثال: `/addpremium 123456789 30`", parse_mode="Markdown")

# --- معالجة فواتير الدفع وطلبات الاشتراك اليدوية ---
async def send_payment_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    user = update.effective_user
    user_id = user.id

    if query.data == "upgrade_menu":
        await upgrade_command(update, context)
        return

    elif query.data == "show_help":
        await help_command(update, context)
        return

    elif query.data == "pay_card_30":
        if PAYMENT_PROVIDER_TOKEN == "YOUR_PAYMENT_PROVIDER_TOKEN_HERE" or not PAYMENT_PROVIDER_TOKEN:
            keyboard = [
                [InlineKeyboardButton("⭐ الدفع بنجوم تيليجرام (تقبل البطاقات)", callback_data="pay_stars_30")],
                [InlineKeyboardButton("💬 تواصل مع المدير @CYS_ALI3", url=ADMIN_URL)]
            ]
            await query.message.reply_text(
                "⚙️ **ملاحظة بوابة البطاقات:**\n"
                "يرجى ربط توكن بوابة الدفع في ملف البوت من @BotFather (/mybots > Payments).\n\n"
                "💡 يمكنك حالياً الدفع وتجربة الاشتراك عبر **نجوم تيليجرام** فوراً أو التحويل المباشر مع المدير!",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            return

        prices = [LabeledPrice("اشتراك بريميوم (30 يوم)", 500)]
        await context.bot.send_invoice(
            chat_id=chat_id,
            title="اشتراك OmniFetch بريميوم (30 يوم)",
            description="تحميلات فائقة السرعة وغير محدودة لجميع الفيديوهات والمقاطع الصوتية لمدة 30 يوماً.",
            payload=f"sub_card_30_{user_id}",
            provider_token=PAYMENT_PROVIDER_TOKEN,
            currency="USD",
            prices=prices,
        )

    elif query.data == "pay_stars_30":
        prices = [LabeledPrice("اشتراك بريميوم (30 يوم)", 250)]
        await context.bot.send_invoice(
            chat_id=chat_id,
            title="اشتراك OmniFetch بريميوم (30 يوم)",
            description="تحميلات فائقة السرعة وغير محدودة لجميع الفيديوهات والمقاطع الصوتية لمدة 30 يوماً.",
            payload=f"sub_stars_30_{user_id}",
            currency="XTR",
            prices=prices,
        )

    elif query.data == "pay_crypto":
        keyboard = [
            [InlineKeyboardButton("📨 إرسال طلب اشتراك للمدير للموافقة", callback_data="request_sub_manual")],
            [InlineKeyboardButton("💬 مراسلة المدير مباشرة @CYS_ALI3", url=ADMIN_URL)]
        ]
        await query.edit_message_text(
            "⚡ **الدفع بالعملات الرقمية أو التحويل اليدوي:**\n\n"
            "1. قم بالتحويل (5 USDT أو ما يعادلها عبر المحافظ المحلية زين كاش/آسيا حوالة أو كاش).\n"
            "2. اضغط على الزر أدناه لإرسال طلب اشتراك للمدير، أو راسله مباشرة لتأكيد التحويل.\n\n"
            f"👤 **معرف المدير المباشر:** [{ADMIN_USERNAME}]({ADMIN_URL})",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif query.data == "request_sub_manual":
        uname = f"@{user.username}" if user.username else "بدون معرف"
        admin_keyboard = [
            [
                InlineKeyboardButton("✅ موافقة وتفعيل 30 يوم", callback_data=f"approve_sub_{user_id}"),
                InlineKeyboardButton("❌ رفض الطلب", callback_data=f"reject_sub_{user_id}"),
            ]
        ]
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🔔 **وصلك طلب اشتراك يدوي جديد!**\n\n"
                f"👤 المستخدم: {user.first_name} ({uname})\n"
                f"🆔 الآيدي: `{user_id}`\n"
                f"📅 الباقة: بريميوم 30 يوماً ($5.00)\n\n"
                f"هل توافق على تفعيل الاشتراك له؟"
            ),
            reply_markup=InlineKeyboardMarkup(admin_keyboard),
            parse_mode="Markdown"
        )

        keyboard = [[InlineKeyboardButton("💬 مراسلة المدير @CYS_ALI3", url=ADMIN_URL)]]
        await query.edit_message_text(
            "✅ **تم إرسال طلب اشتراكك إلى المدير بنجاح!**\n"
            f"سيصلك إشعار فوري هنا حال مراجعة الطلب والموافقة عليه.\n\n"
            f"يمكنك أيضاً إرسال إشعار التحويل مباشرة للمدير: `{ADMIN_USERNAME}`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    if query.invoice_payload.startswith(("sub_card_30_", "sub_stars_30_")):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="انتهت جلسة الدفع، يرجى إعادة المحاولة.")

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    payment = update.message.successful_payment

    if payment.currency == "USD":
        amount = payment.total_amount / 100.0
        method = "بطاقة فيزا / ماستركارد"
    elif payment.currency == "XTR":
        amount = float(payment.total_amount)
        method = "نجوم تيليجرام"
    else:
        amount = float(payment.total_amount)
        method = payment.currency

    new_expiry = record_payment(
        user_id=user_id,
        amount=amount,
        currency=payment.currency,
        method=method,
        payload=payment.invoice_payload,
        days=30
    )

    await update.message.reply_text(
        f"🎉 **تم تأكيد الدفع بنجاح!**\n\n"
        f"المبلغ المدفوع: **{amount} {payment.currency}** عبر {method}\n"
        f"تم تفعيل اشتراك البريميوم الخاص بك لمدة **30 يوماً**!\n"
        f"📅 صالح حتى تاريخ: `{new_expiry}`\n\n"
        f"🚀 يمكنك الآن الاستمتاع بتحميلات غير محدودة وبأعلى جودة وسرعة. أرسل أي رابط للبدء!",
        parse_mode="Markdown"
    )

# --- محرك الترجمة واستخراج النصوص للفيديوهات (VIDEO TRANSLATION & SUBTITLE ENGINE) ---

def clean_tracking_parameters(url: str) -> str:
    """إزالة وسوم التتبع التجسسية ومعاملات الإحالة من الروابط لحماية الخصوصية ومنع الأخطاء"""
    try:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
        tracking_keys = {
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "igsh", "si", "feature", "fbclid", "gclid", "_hsenc", "_hsmi", "ref"
        }
        filtered = {k: v for k, v in params.items() if k.lower() not in tracking_keys}
        new_query = urllib.parse.urlencode(filtered, doseq=True)
        return urllib.parse.urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        ))
    except Exception:
        return url

def detect_platform_badge(url_str: str) -> str:
    """التعرف الذكي على المنصة وعرض الشعار المنسق الخاص بها"""
    u = url_str.lower()
    if "youtu" in u:
        return "🔴 يوتيوب (YouTube)"
    elif "tiktok.com" in u:
        return "🎵 تيك توك (TikTok)"
    elif "instagram.com" in u:
        return "📸 إنستغرام (Instagram)"
    elif "facebook.com" in u or "fb.watch" in u:
        return "📘 فيسبوك (Facebook)"
    elif "twitter.com" in u or "x.com" in u:
        return "🐦 إكس / تويتر (X)"
    elif "pinterest." in u or "pin.it" in u:
        return "📌 بينترست (Pinterest)"
    elif "reddit.com" in u:
        return "🤖 ريديت (Reddit)"
    elif "snapchat.com" in u:
        return "👻 سناب شات (Snapchat)"
    elif "threads.net" in u:
        return "🧵 ثريدز (Threads)"
    elif "soundcloud.com" in u:
        return "☁️ ساوند كلاود (SoundCloud)"
    elif "vimeo.com" in u:
        return "🎬 فيميو (Vimeo)"
    elif "dailymotion.com" in u:
        return "📺 ديلي موشن (Dailymotion)"
    return "🌐 منصة وسائط عامة"

def ms_to_timestamp(ms: int) -> str:
    """تحويل أجزاء الألف من الثانية إلى صيغة التوقيت القياسية 00:00:00,000"""
    secs, m = divmod(ms, 1000)
    mins, s = divmod(secs, 60)
    h, m_val = divmod(mins, 60)
    return f"{h:02d}:{m_val:02d}:{s:02d},{m:03d}"

def normalize_time_str(t_str: str) -> str:
    """توحيد صيغ التوقيت المختلفة إلى HH:MM:SS,mmm"""
    t_str = t_str.replace('.', ',').strip()
    parts = t_str.split(':')
    if len(parts) == 2:
        return f"00:{parts[0].zfill(2)}:{parts[1]}"
    elif len(parts) == 3:
        return f"{parts[0].zfill(2)}:{parts[1]}:{parts[2]}"
    return t_str

def parse_vtt_or_srt_text(content: str) -> List[dict]:
    """استخراج المقاطع الزمنية والنصوص من ملفات VTT أو SRT"""
    lines = content.splitlines()
    cues = []
    i = 0
    time_pat = re.compile(r'(\d{1,2}:\d{2}[\.,]\d{3}|\d{1,2}:\d{2}:\d{2}[\.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}[\.,]\d{3}|\d{1,2}:\d{2}:\d{2}[\.,]\d{3})')
    while i < len(lines):
        line = lines[i].strip()
        m = time_pat.search(line)
        if m:
            start_t = normalize_time_str(m.group(1))
            end_t = normalize_time_str(m.group(2))
            i += 1
            text_lines = []
            while i < len(lines) and lines[i].strip():
                clean_t = re.sub(r'<[^>]+>', '', lines[i].strip())
                clean_t = re.sub(r'\{[^\}]+\}', '', clean_t).strip()
                if clean_t and not clean_t.startswith("NOTE") and not clean_t.startswith("STYLE"):
                    text_lines.append(clean_t)
                i += 1
            if text_lines:
                cues.append({
                    "start": start_t,
                    "end": end_t,
                    "text": " ".join(text_lines)
                })
        else:
            i += 1
    return cues

def parse_json3_subtitles(content: str) -> List[dict]:
    """استخراج النصوص والمقاطع الزمنية من صيغة JSON3 الخاصة بيوتيوب"""
    try:
        data = json.loads(content)
        cues = []
        for ev in data.get("events", []):
            if "segs" not in ev or "tStartMs" not in ev:
                continue
            s_ms = ev["tStartMs"]
            d_ms = ev.get("dDurationMs", 2000)
            txt = "".join(s.get("utf8", "") for s in ev["segs"]).replace("\n", " ").strip()
            if txt:
                cues.append({
                    "start": ms_to_timestamp(s_ms),
                    "end": ms_to_timestamp(s_ms + d_ms),
                    "text": txt
                })
        return cues
    except Exception as e:
        logger.error(f"Error parsing json3: {e}")
        return []

def translate_cues_network(cues: List[dict], target_lang: str, src_lang: str = "") -> List[dict]:
    """ترجمة مقاطع الترجمة في دفعات ذكية وسريعة باستخدام محرك الترجمة العصبي"""
    if not cues:
        return []
    # إذا كانت لغة المصدر هي نفسها لغة الهدف المختارة، نستخدم المقاطع الأصلية مباشرة
    if src_lang and (src_lang == target_lang or src_lang.startswith(f"{target_lang}-") or (target_lang == "zh-CN" and "zh" in src_lang)):
        return cues

    chunk_size = 20
    translated = []
    for i in range(0, len(cues), chunk_size):
        chunk = cues[i:i+chunk_size]
        texts = [c["text"].replace("\n", " ").strip() for c in chunk]
        delim = " ___CUE___ "
        combined = delim.join(texts)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={target_lang}&dt=t&q=" + urllib.parse.quote(combined)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                full_trans = "".join([part[0] for part in data[0] if part[0]])
            parts = full_trans.split("___CUE___")
            clean_parts = [p.strip() for p in parts]
            while len(clean_parts) < len(texts):
                clean_parts.append(texts[len(clean_parts)])
            for j, c in enumerate(chunk):
                translated.append({
                    "start": c["start"],
                    "end": c["end"],
                    "text": clean_parts[j]
                })
        except Exception as e:
            logger.warning(f"Translation batch failed ({e}), using original text...")
            for c in chunk:
                translated.append(c)
    return translated

def cues_to_srt_str(cues: List[dict]) -> str:
    """تحويل المقاطع إلى نص SRT قياسي"""
    lines = []
    for idx, c in enumerate(cues, start=1):
        lines.append(str(idx))
        lines.append(f"{c['start']} --> {c['end']}")
        lines.append(c['text'])
        lines.append("")
    return "\n".join(lines)

def cues_to_ass_str(cues: List[dict]) -> str:
    """تحويل المقاطع إلى صيغة ASS الاحترافية مع تخصيص المظهر والخلفية المظللة والخطوط المتناسقة"""
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1280\n"
        "PlayResY: 720\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,26,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,2,0,2,20,20,35,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for c in cues:
        s_parts = c['start'].replace(',', '.').split(':')
        e_parts = c['end'].replace(',', '.').split(':')
        s_h = int(s_parts[0])
        e_h = int(e_parts[0])
        s_time = f"{s_h}:{s_parts[1]}:{float(s_parts[2]):05.2f}"
        e_time = f"{e_h}:{e_parts[1]}:{float(e_parts[2]):05.2f}"
        clean_txt = c['text'].replace('\n', '\\N').replace('{', '').replace('}', '')
        events.append(f"Dialogue: 0,{s_time},{e_time},Default,,0,0,0,,{clean_txt}")
    return header + "\n".join(events) + "\n"

def extract_best_subtitles(info_dict: dict, target_lang: str = "ar") -> Tuple[Optional[str], str, str]:
    """
    استخراج أفضل مسار ترجمة وتفريغ صوتي متوفر (VTT, JSON3, SRT)
    يبحث أولاً عن لغة الهدف المطلوبة إذا كانت متوفرة مسبقاً، أو يستخرج الإنجليزية/المتوفر لترجمتها
    يرجع: (محتوى_الترجمة_كنص, الامتداد, لغة_المصدر)
    """
    subs = info_dict.get("subtitles") or {}
    auto_subs = info_dict.get("automatic_captions") or {}

    # 1. فحص إذا كانت الترجمة المطلوبة متوفرة مباشرة في ترجمات الفيديو الرسمية
    target_pool = None
    chosen_lang = None
    for pool in [subs, auto_subs]:
        for lk in pool.keys():
            if lk == target_lang or lk.startswith(f"{target_lang}-") or (target_lang == "zh-CN" and "zh" in lk):
                target_pool = pool
                chosen_lang = lk
                break
        if target_pool:
            break

    # 2. إذا لم تكن لغة الهدف متوفرة كمسار جاهز، نختار الإنجليزية أو أول تفريغ صوتي متاح للترجمة
    if not target_pool:
        target_pool = subs if subs else auto_subs
        if not target_pool:
            return None, "", ""
        for pref in ["en", "en-US", "en-GB"]:
            if pref in target_pool:
                chosen_lang = pref
                break
        if not chosen_lang:
            chosen_lang = list(target_pool.keys())[0]

    formats = target_pool[chosen_lang]
    chosen_format = None
    for pref_ext in ["json3", "vtt", "srt", "srv1"]:
        match = [f for f in formats if f.get("ext") == pref_ext]
        if match:
            chosen_format = match[0]
            break
    if not chosen_format and formats:
        chosen_format = formats[0]

    if not chosen_format or not chosen_format.get("url"):
        return None, "", ""

    sub_url = chosen_format["url"]
    req = urllib.request.Request(sub_url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            return content, chosen_format.get("ext", "vtt"), chosen_lang
    except Exception as e:
        logger.error(f"Failed to fetch subtitle content: {e}")
        return None, "", ""


async def process_video_translation(update: Update, context: ContextTypes.DEFAULT_TYPE, target_lang_code: str):
    """المحرك الكامل لترجمة الفيديو وتوليد ودمج الترجمة الملونة الاحترافية وإرسالها للمستخدم"""
    query = update.callback_query
    await query.answer()
    
    url = context.user_data.get("pending_url")
    if not url:
        await query.edit_message_text("❌ انتهت صلاحية الجلسة، يرجى إعادة إرسال الرابط من جديد.")
        return

    user_id = update.effective_user.id
    status = get_or_update_user(update.effective_user)
    if status["is_banned"]:
        await query.edit_message_text("⛔ أنت محظور من استخدام البوت.")
        return

    # --- بوابة البريميوم الحصرية: ميزة الترجمة مدفوعة فقط ---
    if not status["is_premium"]:
        await query.edit_message_text(
            PREMIUM_GATE_TEXT,
            reply_markup=get_premium_gate_keyboard(),
            parse_mode="Markdown"
        )
        return


    lang_info = TRANSLATION_LANGUAGES.get(target_lang_code, {"name": target_lang_code, "flag": "🌐", "code": target_lang_code})
    lang_name = lang_info["name"]
    lang_flag = lang_info["flag"]

    status_msg = await query.edit_message_text(
        f"🔍 **[1/4] جاري فحص الرابط والبحث عن الترجمة والتفريغ الصوتي...**\n"
        f"⏳ _يرجى الانتظار لحظات..._",
        parse_mode="Markdown"
    )

    loop = asyncio.get_running_loop()

    def extract_info():
        ydl_opts_info = {
            "quiet": True,
            "noplaylist": True,
            "nocheckcertificate": True,
            "socket_timeout": 25,
            "geo_bypass": True,
            "no_color": True,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
            },
            "extractor_args": {
                "youtube": {
                    "player_client": ["ios", "android", "mweb"]
                }
            }
        }
        if os.path.exists(COOKIES_FILE):
            ydl_opts_info["cookiefile"] = COOKIES_FILE
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info_dict = await loop.run_in_executor(None, extract_info)
        if not info_dict:
            raise ValueError("تعذر استخراج بيانات الفيديو.")

        media_title = (info_dict.get("title") or "فيديو")[:80]

        content, sub_ext, src_lang = await loop.run_in_executor(None, extract_best_subtitles, info_dict, target_lang_code)
        if not content:
            back_kb = [
                [InlineKeyboardButton("📹 تحميل كفيديو عادي", callback_data="choose_type_video")],
                [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="choose_type_back")]
            ]
            await status_msg.edit_text(
                "⚠️ **عذراً، لم يتم العثور على ترجمة أو تفريغ صوتي متاح لهذا المقطع.**\n\n"
                "💡 ميزة ترجمة الفيديوهات تعتمد على وجود تفريغ صوتي أو ترجمة مصاحبة للفيديو (مثل مقاطع يوتيوب، تيد، والمحاضرات والبودكاست).\n\n"
                "يمكنك تحميل الفيديو بجودته الأصلية مباشرة:",
                reply_markup=InlineKeyboardMarkup(back_kb),
                parse_mode="Markdown"
            )
            return

        if sub_ext == "json3":
            cues = parse_json3_subtitles(content)
        else:
            cues = parse_vtt_or_srt_text(content)

        if not cues:
            raise ValueError("لم يتم العثور على أسطر صالحة للترجمة في ملف التفريغ الصوتي.")

        await status_msg.edit_text(
            f"🌐 **[2/4] جاري تجهيز وترجمة {len(cues)} مقطع صوتي إلى {lang_flag} {lang_name}...**\n"
            f"⚡ _جاري المعالجة الفورية بالذكاء الاصطناعي..._",
            parse_mode="Markdown"
        )

        translated_cues = await loop.run_in_executor(None, translate_cues_network, cues, target_lang_code, src_lang)

        srt_content = cues_to_srt_str(translated_cues)
        ass_content = cues_to_ass_str(translated_cues)

        srt_filename = f"{user_id}_sub_{target_lang_code}.srt"
        ass_filename = f"{user_id}_sub_{target_lang_code}.ass"
        srt_path = os.path.join(DOWNLOAD_DIR, srt_filename)
        ass_path = os.path.join(DOWNLOAD_DIR, ass_filename)

        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_content)
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_content)

        await status_msg.edit_text(
            f"⬇️ **[3/4] جاري تنزيل الفيديو ودمج الترجمة الاحترافية عليه (Hardsub)...**\n"
            f"🎬 _جاري المعالجة الاحترافية عبر FFmpeg..._",
            parse_mode="Markdown"
        )

        raw_vid_tmpl = os.path.join(DOWNLOAD_DIR, f"{user_id}_raw_%(id)s.%(ext)s")
        dl_opts = {
            "outtmpl": raw_vid_tmpl,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "nocheckcertificate": True,
            "socket_timeout": 35,
            "geo_bypass": True,
            "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            "merge_output_format": "mp4",
        }
        if os.path.exists(COOKIES_FILE):
            dl_opts["cookiefile"] = COOKIES_FILE

        def run_dl_video():
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                inf = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(inf)

        raw_video_path = await loop.run_in_executor(None, run_dl_video)
        
        if not os.path.exists(raw_video_path):
            base_p, _ = os.path.splitext(raw_video_path)
            for ex in [".mp4", ".mkv", ".webm"]:
                if os.path.exists(base_p + ex):
                    raw_video_path = base_p + ex
                    break

        if not os.path.exists(raw_video_path):
            raise FileNotFoundError("تعذر العثور على الفيديو بعد تنزيله.")

        output_translated_mp4 = os.path.join(DOWNLOAD_DIR, f"{user_id}_translated_{target_lang_code}.mp4")
        rel_in = os.path.relpath(raw_video_path).replace("\\", "/")
        rel_sub = os.path.relpath(ass_path).replace("\\", "/")
        rel_out = os.path.relpath(output_translated_mp4).replace("\\", "/")

        def run_ffmpeg_burn():
            cmd = [
                "ffmpeg", "-y",
                "-i", rel_in,
                "-vf", f"subtitles={rel_sub}",
                "-c:a", "copy",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                rel_out
            ]
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode != 0:
                logger.warning(f"FFmpeg burn failed: {res.stderr.decode('utf-8', errors='ignore')}")
                rel_srt = os.path.relpath(srt_path).replace("\\", "/")
                cmd_fallback = [
                    "ffmpeg", "-y",
                    "-i", rel_in,
                    "-vf", f"subtitles={rel_srt}",
                    "-c:a", "copy",
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "23",
                    rel_out
                ]
                subprocess.run(cmd_fallback, check=True)

        await loop.run_in_executor(None, run_ffmpeg_burn)

        if not os.path.exists(output_translated_mp4):
            output_translated_mp4 = raw_video_path

        await status_msg.edit_text(
            f"📤 **[4/4] جاري رفع الفيديو المترجم إلى تيليجرام...**\n"
            f"🚀 _شارفنا على الانتهاء!_",
            parse_mode="Markdown"
        )

        file_size = os.path.getsize(output_translated_mp4)
        MAX_TELEGRAM_LIMIT = 50 * 1024 * 1024
        if file_size > MAX_TELEGRAM_LIMIT:
            with open(srt_path, "rb") as f_sub:
                await query.message.reply_document(
                    document=f_sub,
                    caption=(
                        f"🎬 **{media_title}**\n\n"
                        f"🌐 **ملف الترجمة ({lang_flag} {lang_name})**\n\n"
                        f"⚠️ حجم الفيديو بعد الدمج تجاوز 50MB (حد تيليجرام للبوتات).\n"
                        f"تم إرسال ملف الترجمة SRT لتقوم بتشغيله مع الفيديو في هاتفك أو عبر تطبيق VLC."
                    ),
                    filename=f"{media_title}_{target_lang_code}.srt"
                )
            return

        caption_video = (
            f"🎬 **{media_title}**\n\n"
            f"🌐 **مترجم إلى:** {lang_flag} {lang_name}\n"
            f"🤖 بواسطة: @OmniFetch45_Bot"
        )

        with open(output_translated_mp4, "rb") as f_vid:
            try:
                await query.message.reply_video(
                    video=f_vid,
                    caption=caption_video,
                    supports_streaming=True,
                    parse_mode="Markdown",
                    read_timeout=300,
                    write_timeout=300,
                    connect_timeout=60,
                )
            except Exception:
                f_vid.seek(0)
                await query.message.reply_document(
                    document=f_vid,
                    caption=caption_video,
                    filename=f"{media_title}_translated_{target_lang_code}.mp4",
                    read_timeout=300,
                    write_timeout=300,
                )

        with open(srt_path, "rb") as f_srt:
            try:
                await query.message.reply_document(
                    document=f_srt,
                    caption=f"📄 **ملف الترجمة المنفصل ({lang_flag} {lang_name})**\n💡 يمكنك استخدامه مع مشغل VLC أو أي برنامج آخر.",
                    filename=f"Subtitle_{target_lang_code}.srt"
                )
            except Exception:
                pass

        increment_confirmed_download(user_id, is_free_tier=not status["is_premium"])
        refreshed_status = get_or_update_user(update.effective_user)

        if not status["is_premium"]:
            remaining_today = max(0, DAILY_FREE_LIMIT - refreshed_status['count'])
            await status_msg.edit_text(
                f"✅ **تمت ترجمة الفيديو وإرساله بنجاح!**\n"
                f"🌐 اللغة: {lang_flag} {lang_name}\n"
                f"📊 المتبقي من رصيدك اليومي: **{remaining_today}**\n"
                f"💎 للترجمة والتحميل غير المحدود اشترك عبر: `/upgrade`",
                parse_mode="Markdown"
            )
        else:
            await status_msg.edit_text(
                f"✅ **تمت ترجمة الفيديو وإرساله بنجاح!**\n"
                f"🌐 اللغة: {lang_flag} {lang_name}\n"
                f"💎 باقة بريميوم نشطة (متبقي {status['days_left']} يوم)",
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"Translation failed: {e}")
        clean_err = str(e).replace("`", "").replace("*", "").replace("_", "")[:180]
        back_kb = [
            [InlineKeyboardButton("📹 تحميل كفيديو عادي", callback_data="choose_type_video")],
            [InlineKeyboardButton("💬 تواصل مع المدير @CYS_ALI3", url=ADMIN_URL)]
        ]
        await status_msg.edit_text(
            f"❌ **عذراً، فشلت ترجمة هذا الفيديو:**\n{clean_err}\n\nيمكنك تحميله كفيديو عادي أو التواصل مع الإدارة:",
            reply_markup=InlineKeyboardMarkup(back_kb),
            parse_mode="Markdown"
        )
    finally:
        try:
            prefix = f"{user_id}_"
            for fname in os.listdir(DOWNLOAD_DIR):
                if fname.startswith(prefix) and not fname.endswith(IMAGE_EXTENSIONS):
                    f_path = os.path.join(DOWNLOAD_DIR, fname)
                    try:
                        if os.path.isfile(f_path):
                            os.remove(f_path)
                    except Exception:
                        pass
        except Exception:
            pass

async def handle_trim_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قص مقطع محدد من الفيديو بناءً على توقيت البداية والنهاية المرسل من المستخدم"""
    context.user_data["awaiting_trim"] = False
    text = (update.message.text or "").strip()
    url = context.user_data.get("pending_url")
    if not url:
        await update.message.reply_text("❌ انتهت صلاحية الجلسة، يرجى إعادة إرسال الرابط من جديد.")
        return

    times = text.split()
    if len(times) != 2:
        await update.message.reply_text(
            "⚠️ يرجى إرسال وقت البداية والنهاية مفصولين بمسافة واحدة.\nمثال: `00:15 00:45` أو `10 40`",
            parse_mode="Markdown"
        )
        return

    start_str, end_str = times[0], times[1]
    user_id = update.effective_user.id
    status = get_or_update_user(update.effective_user)
    if status["is_banned"]:
        await update.message.reply_text("⛔ عذراً، أنت محظور من استخدام البوت.")
        return

    # --- بوابة البريميوم الحصرية: ميزة قص المقاطع مدفوعة فقط ---
    if not status["is_premium"]:
        await update.message.reply_text(
            PREMIUM_GATE_TEXT,
            reply_markup=get_premium_gate_keyboard(),
            parse_mode="Markdown"
        )
        return

    status_msg = await update.message.reply_text(
        f"✂️ **جاري تنزيل وقص المقطع من (`{start_str}`) إلى (`{end_str}`)...**\n⏳ _لحظات من فضلك..._",
        parse_mode="Markdown"
    )

    loop = asyncio.get_running_loop()
    raw_vid_tmpl = os.path.join(DOWNLOAD_DIR, f"{user_id}_trim_raw_%(id)s.%(ext)s")
    dl_opts = {
        "outtmpl": raw_vid_tmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "socket_timeout": 35,
        "geo_bypass": True,
        "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "merge_output_format": "mp4",
    }
    if os.path.exists(COOKIES_FILE):
        dl_opts["cookiefile"] = COOKIES_FILE

    try:
        def run_dl():
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                inf = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(inf), (inf.get("title") or "مقطع مقصوص")

        raw_path, vid_title = await loop.run_in_executor(None, run_dl)
        if not os.path.exists(raw_path):
            base_p, _ = os.path.splitext(raw_path)
            for ex in [".mp4", ".mkv", ".webm"]:
                if os.path.exists(base_p + ex):
                    raw_path = base_p + ex
                    break

        trimmed_out = os.path.join(DOWNLOAD_DIR, f"{user_id}_trimmed.mp4")
        rel_in = os.path.relpath(raw_path).replace("\\", "/")
        rel_out = os.path.relpath(trimmed_out).replace("\\", "/")

        def parse_seconds(t: str) -> float:
            parts = t.strip().split(":")
            if len(parts) == 1:
                return float(parts[0])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            return 0.0

        try:
            s_sec = parse_seconds(start_str)
            e_sec = parse_seconds(end_str)
            dur_sec = max(1.0, e_sec - s_sec)
        except Exception:
            s_sec = 0.0
            dur_sec = 10.0

        def run_cut():
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(s_sec),
                "-i", rel_in,
                "-t", str(dur_sec),
                "-c", "copy",
                rel_out
            ]
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode != 0:
                cmd_fallback = [
                    "ffmpeg", "-y",
                    "-ss", str(s_sec),
                    "-i", rel_in,
                    "-t", str(dur_sec),
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    rel_out
                ]
                subprocess.run(cmd_fallback, check=True, capture_output=True)

        await loop.run_in_executor(None, run_cut)

        with open(trimmed_out, "rb") as f_trim:
            await update.message.reply_video(
                video=f_trim,
                caption=f"✂️ **{vid_title}**\n⏱ **المقطع:** من `{start_str}` إلى `{end_str}`\n🤖 بواسطة: @OmniFetch45_Bot",
                parse_mode="Markdown"
            )

        increment_confirmed_download(user_id, is_free_tier=not status["is_premium"])
        await status_msg.delete()

    except Exception as e:
        logger.error(f"Trim failed: {e}")
        await status_msg.edit_text(f"❌ فشل قص المقطع: {e}\nتأكد من صيغة التوقيت بشكل صحيح (مثال: `00:15 00:45`).")
    finally:
        prefix = f"{user_id}_trim_"
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(prefix) or f.startswith(f"{user_id}_trimmed"):
                try:
                    os.remove(os.path.join(DOWNLOAD_DIR, f))
                except Exception:
                    pass

async def process_video_to_gif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحويل مقطع فيديو قصير إلى رسوم متحركة GIF عالية النقاوة وسريعة التحميل"""
    query = update.callback_query
    await query.answer()
    url = context.user_data.get("pending_url")
    if not url:
        await query.edit_message_text("❌ انتهت صلاحية الجلسة، يرجى إعادة إرسال الرابط من جديد.")
        return

    user_id = update.effective_user.id
    status = get_or_update_user(update.effective_user)
    if status["is_banned"]:
        await query.edit_message_text("⛔ أنت محظور من استخدام البوت.")
        return

    # --- بوابة البريميوم الحصرية: ميزة تحويل GIF مدفوعة فقط ---
    if not status["is_premium"]:
        await query.edit_message_text(
            PREMIUM_GATE_TEXT,
            reply_markup=get_premium_gate_keyboard(),
            parse_mode="Markdown"
        )
        return

    status_msg = await query.edit_message_text(
        "🎞️ **جاري تحويل المقطع إلى GIF متحرك عالي الجودة...**\n⏳ _لحظات من فضلك..._",
        parse_mode="Markdown"
    )

    loop = asyncio.get_running_loop()
    raw_vid_tmpl = os.path.join(DOWNLOAD_DIR, f"{user_id}_gif_raw_%(id)s.%(ext)s")
    dl_opts = {
        "outtmpl": raw_vid_tmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "socket_timeout": 35,
        "geo_bypass": True,
        "format": "bestvideo[height<=480]/best[height<=480]/best",
        "merge_output_format": "mp4",
    }
    if os.path.exists(COOKIES_FILE):
        dl_opts["cookiefile"] = COOKIES_FILE

    try:
        def run_dl():
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                inf = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(inf), (inf.get("title") or "GIF")

        raw_path, vid_title = await loop.run_in_executor(None, run_dl)
        if not os.path.exists(raw_path):
            base_p, _ = os.path.splitext(raw_path)
            for ex in [".mp4", ".mkv", ".webm"]:
                if os.path.exists(base_p + ex):
                    raw_path = base_p + ex
                    break

        gif_out = os.path.join(DOWNLOAD_DIR, f"{user_id}_anim.gif")
        rel_in = os.path.relpath(raw_path).replace("\\", "/")
        rel_out = os.path.relpath(gif_out).replace("\\", "/")

        def run_gif_conv():
            cmd = [
                "ffmpeg", "-y",
                "-i", rel_in,
                "-t", "8",
                "-vf", "fps=10,scale=360:-1:flags=lanczos",
                rel_out
            ]
            subprocess.run(cmd, check=True, capture_output=True)

        await loop.run_in_executor(None, run_gif_conv)

        with open(gif_out, "rb") as f_gif:
            await query.message.reply_animation(
                animation=f_gif,
                caption=f"🎞️ **{vid_title}**\n🤖 بواسطة: @OmniFetch45_Bot",
                parse_mode="Markdown"
            )
        await status_msg.delete()

    except Exception as e:
        logger.error(f"GIF generation failed: {e}")
        await status_msg.edit_text(f"❌ فشل تحويل المقطع إلى GIF: {e}")
    finally:
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(f"{user_id}_gif_") or f.startswith(f"{user_id}_anim"):
                try:
                    os.remove(os.path.join(DOWNLOAD_DIR, f))
                except Exception:
                    pass

# --- استقبال الروابط (MEDIA HANDLER) ---
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_status = get_or_update_user(update.effective_user)
    user_id = update.effective_user.id

    if user_status["is_banned"]:
        await update.message.reply_text("⛔ عذراً، لقد تم حظرك من استخدام هذا البوت من قبل الإدارة.")
        return

    text = (update.message.text or "").strip()

    # 0. فحص إذا كان المستخدم في وضع قص مقطع محدد (Trim Mode)
    if context.user_data.get("awaiting_trim"):
        await handle_trim_input(update, context)
        return

    # 1. فحص إذا كان المدير في وضع البحث عن مستخدم بالآيدي أو المعرف
    if is_admin(user_id) and context.user_data.get("awaiting_user_search"):
        context.user_data["awaiting_user_search"] = False
        target_info = get_user_by_query(text)
        if not target_info:
            keyboard = [
                [InlineKeyboardButton("🔍 محاولة بحث أخرى", callback_data="search_user_prompt")],
                [InlineKeyboardButton("🔙 رجوع للوحة التحكم", callback_data="admin_panel_home")]
            ]
            await update.message.reply_text(
                f"❌ لم يتم العثور على أي مستخدم بالمدخل: `{text}`\n\nتأكد من أن المستخدم قد دخل للبوت من قبل أو أن الآيدي صحيح.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            return
        card_text, card_markup = render_user_card(target_info)
        await update.message.reply_text(card_text, reply_markup=card_markup, parse_mode="Markdown")
        return

    # 2. فحص إذا كان المدير في وضع إرسال رسالة مباشرة لمستخدم
    if is_admin(user_id) and context.user_data.get("awaiting_user_msg_target"):
        target_uid = context.user_data.pop("awaiting_user_msg_target")
        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=f"📩 **رسالة خاصة من إدارة بوت OmniFetch:**\n\n{text}",
                parse_mode="Markdown"
            )
            await update.message.reply_text(f"✅ **تم إرسال الرسالة بنجاح للمستخدم:** `{target_uid}`", parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ تعذر إرسال الرسالة للمستخدم ({e}). قد يكون المستخدم قام بحظر البوت.")
        return

    # 3. فحص إذا كان المالك يضيف مديراً جديداً عبر إرسال الآيدي
    if is_owner(user_id) and context.user_data.get("awaiting_add_admin"):
        context.user_data["awaiting_add_admin"] = False
        if not text.isdigit():
            await update.message.reply_text("❌ يرجى إرسال رقم آيدي صحيح (أرقام فقط).")
            return
        new_admin_id = int(text)
        user_db_info = get_user_by_query(text)
        new_admin_uname = user_db_info.get("username", "بدون معرف") if user_db_info else "مدير جديد"
        add_admin_db(new_admin_id, new_admin_uname, added_by=user_id)
        await update.message.reply_text(f"👑 **تم بنجاح تعيين المستخدم `{new_admin_id}` مديراً في البوت!**", parse_mode="Markdown")
        try:
            await context.bot.send_message(
                chat_id=new_admin_id,
                text=(
                    "👑 **تهانينا!**\n\n"
                    "قام مالك البوت بتعيينك **مديراً (Admin)** في بوت OmniFetch!\n"
                    "يمكنك الآن استخدام الأمر `/admin` للوصول إلى لوحة التحكم وإدارة المستخدمين."
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass
        return

    # استخراج الرابط بذكاء حتى لو أرسل المستخدم نصاً مصاحباً للرابط
    url_match = re.search(r"https?://[^\s]+", text)
    if not url_match:
        keyboard = [[InlineKeyboardButton("💬 تواصل مع المدير @CYS_ALI3", url=ADMIN_URL)]]
        await update.message.reply_text(
            "⚠️ **يرجى إرسال رابط صحيح.**\n\n"
            "يدعم البوت روابط: يوتيوب، تيك توك، إنستغرام، فيسبوك، X (تويتر)، بينترست، وجميع روابط الصور والفيديوهات.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    raw_url = url_match.group(0).rstrip(").,;!?\"'")
    clean_url = clean_tracking_parameters(raw_url)
    context.user_data["pending_url"] = clean_url

    # إذا كان المستخدم في وضع طلب ترجمة بعد كتابة /translate
    if context.user_data.pop("awaiting_translate_url", False):
        if not user_status["is_premium"]:
            await update.message.reply_text(
                PREMIUM_GATE_TEXT,
                reply_markup=get_premium_gate_keyboard(),
                parse_mode="Markdown"
            )
            return
        await update.message.reply_text(
            "🌐 **ميزة ترجمة الفيديوهات الاحترافية (AI Subtitles & Video Translation):**\n\n"
            "🎯 **اختر اللغة التي تريد ترجمة ودمج الكلام بها داخل الفيديو (14 لغة عالمية):**\n\n"
            "✨ سيقوم البوت باستخراج الكلام وترجمته ودمجه بالفيديو فورياً (Hardsub) مع إرسال ملف SRT الأصلي.",
            reply_markup=get_translation_keyboard(include_back=True),
            parse_mode="Markdown"
        )
        return

    # إذا كان المستخدم في وضع طلب قص بعد كتابة /trim
    if context.user_data.pop("awaiting_trim_url", False):
        if not user_status["is_premium"]:
            await update.message.reply_text(
                PREMIUM_GATE_TEXT,
                reply_markup=get_premium_gate_keyboard(),
                parse_mode="Markdown"
            )
            return
        context.user_data["awaiting_trim"] = True
        await update.message.reply_text(
            "✂️ **أداة قص مقطع محدد من الفيديو (Trim Video):**\n\n"
            "أرسل الآن وقت البداية ووقت النهاية للمقطع المطلوب.\n"
            "📝 أمثلة:\n"
            "• `00:15 00:45`\n"
            "• `01:00 01:30`\n"
            "• `10 30`",
            parse_mode="Markdown"
        )
        return

    # إذا كان المستخدم في وضع طلب GIF بعد كتابة /gif
    if context.user_data.pop("awaiting_gif_url", False):
        if not user_status["is_premium"]:
            await update.message.reply_text(
                PREMIUM_GATE_TEXT,
                reply_markup=get_premium_gate_keyboard(),
                parse_mode="Markdown"
            )
            return
        status_msg = await update.message.reply_text("⏳ _جاري التحويل لـ GIF..._", parse_mode="Markdown")
        class CmdQuery:
            def __init__(self, msg): self._msg = msg
            async def answer(self): pass
            async def edit_message_text(self, t, parse_mode=None):
                return await self._msg.edit_text(t, parse_mode=parse_mode)
            @property
            def message(self): return self._msg
        dummy_up = Update(update.update_id, callback_query=CmdQuery(status_msg))
        await process_video_to_gif(dummy_up, context)
        return

    # عرض علامة القفل 🔒 على الميزات المدفوعة للمستخدمين المجانيين
    is_prem = user_status["is_premium"]
    tr_label = "🌐 ترجمة الفيديو (14 لغة) 🔤" if is_prem else "🔒 ترجمة الفيديو [بريميوم 💎]"
    trim_label = "✂️ قص مقطع محدد (Trim)" if is_prem else "🔒 قص مقطع [بريميوم 💎]"
    gif_label = "🎞️ تحويل لـ GIF متحرك" if is_prem else "🔒 GIF متحرك [بريميوم 💎]"
    keyboard = [
        [
            InlineKeyboardButton("📹 تحميل كفيديو (Video)", callback_data="choose_type_video"),
            InlineKeyboardButton("🎵 تحميل كصوت (Audio)", callback_data="choose_type_audio"),
        ],
        [
            InlineKeyboardButton(tr_label, callback_data="choose_type_translate"),
            InlineKeyboardButton("🖼️ استخراج الصور / الألبوم", callback_data="choose_type_image"),
        ],
        [
            InlineKeyboardButton(trim_label, callback_data="choose_type_trim"),
            InlineKeyboardButton(gif_label, callback_data="choose_type_gif"),
        ],
        [
            InlineKeyboardButton("💬 تواصل مع المدير 👨‍💼", url=ADMIN_URL),
        ]
    ]
    badge = detect_platform_badge(clean_url)
    tier_badge = "💎 **عضوية بريميوم (غير محدودة)**" if is_prem else f"👤 **حساب مجاني** (المتبقي: `{max(0, DAILY_FREE_LIMIT - user_status['count'])}/{DAILY_FREE_LIMIT}` اليوم)"
    await update.message.reply_text(
        f"🎯 **معالجة الرابط:**\n"
        f"• **المنصة:** {badge}\n"
        f"• **حسابك:** {tier_badge}\n\n"
        f"اختر نوع التحميل أو المعالجة المطلوبة لهذا الرابط:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# --- قائمة اختيار الدقات والجودات والترجمة التفاعلية ---
async def media_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    url = context.user_data.get("pending_url")

    if not url:
        await query.answer()
        await query.edit_message_text("❌ انتهت صلاحية الجلسة، يرجى إعادة إرسال الرابط من جديد.")
        return

    user_status = get_or_update_user(update.effective_user)
    if user_status["is_banned"]:
        await query.answer("⛔ أنت محظور من استخدام البوت.", show_alert=True)
        return

    # قفل الدقات العالية (1080p فما فوق) للمجانيين
    if data.startswith("locked_prem_"):
        await query.answer(
            "🔒 عذراً، دقة 1080p وما فوق مخصصة لمشتركي باقة البريميوم 💎!\n\n"
            "الحساب المجاني متاح له حتى دقة 720p مجاناً.\n"
            "يمكنك الترقية للاشتراك غير المحدود عبر /upgrade",
            show_alert=True
        )
        return

    elif data == "choose_type_back":
        await query.answer()
        context.user_data["awaiting_trim"] = False
        # عرض علامة القفل 🔒 على الميزات المدفوعة للمستخدمين المجانيين
        is_prem = user_status["is_premium"]
        tr_label = "🌐 ترجمة الفيديو (14 لغة) 🔤" if is_prem else "🔒 ترجمة الفيديو [بريميوم 💎]"
        trim_label = "✂️ قص مقطع محدد (Trim)" if is_prem else "🔒 قص مقطع [بريميوم 💎]"
        gif_label = "🎞️ تحويل لـ GIF متحرك" if is_prem else "🔒 GIF متحرك [بريميوم 💎]"
        keyboard = [
            [
                InlineKeyboardButton("📹 تحميل كفيديو (Video)", callback_data="choose_type_video"),
                InlineKeyboardButton("🎵 تحميل كصوت (Audio)", callback_data="choose_type_audio"),
            ],
            [
                InlineKeyboardButton(tr_label, callback_data="choose_type_translate"),
                InlineKeyboardButton("🖼️ استخراج الصور / الألبوم", callback_data="choose_type_image"),
            ],
            [
                InlineKeyboardButton(trim_label, callback_data="choose_type_trim"),
                InlineKeyboardButton(gif_label, callback_data="choose_type_gif"),
            ],
            [
                InlineKeyboardButton("💬 تواصل مع المدير 👨‍💼", url=ADMIN_URL),
            ]
        ]
        badge = detect_platform_badge(url)
        tier_badge = "💎 **عضوية بريميوم (غير محدودة)**" if is_prem else f"👤 **حساب مجاني** (المتبقي: `{max(0, DAILY_FREE_LIMIT - user_status['count'])}/{DAILY_FREE_LIMIT}` اليوم)"
        await query.edit_message_text(
            f"🎯 **معالجة الرابط:**\n"
            f"• **المنصة:** {badge}\n"
            f"• **حسابك:** {tier_badge}\n\n"
            f"اختر نوع التحميل أو المعالجة المطلوبة:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    elif data == "choose_type_translate":
        await query.answer()
        # --- بوابة البريميوم: الترجمة ميزة مدفوعة ---
        if not user_status["is_premium"]:
            await query.edit_message_text(
                PREMIUM_GATE_TEXT,
                reply_markup=get_premium_gate_keyboard(),
                parse_mode="Markdown"
            )
            return
        await query.edit_message_text(
            "🌐 **ميزة ترجمة الفيديوهات الاحترافية (AI Subtitles & Video Translation):**\n\n"
            "🎯 **اختر اللغة التي تريد ترجمة ودمج الكلام بها داخل الفيديو (14 لغة عالمية):**\n\n"
            "✨ سيقوم البوت باستخراج الكلام وترجمته ودمجه بالفيديو بخط واضح وخلفية أنيقة (Hardsub)، كما ستحصل على ملف الترجمة الأصلي (SRT) منفصلاً.",
            reply_markup=get_translation_keyboard(include_back=True),
            parse_mode="Markdown"
        )
        return

    elif data.startswith("tr_lang_"):
        # --- بوابة البريميوم: تأكيد مزدوج عند اختيار اللغة ---
        if not user_status["is_premium"]:
            await query.answer(
                "🔒 عذراً، ترجمة الفيديوهات متاحة فقط لمشتركي البريميوم 💎\n"
                "يمكنك الترقية عبر /upgrade",
                show_alert=True
            )
            return
        target_lang = data.replace("tr_lang_", "")
        await process_video_translation(update, context, target_lang)
        return

    elif data == "choose_type_trim":
        await query.answer()
        # --- بوابة البريميوم: القص ميزة مدفوعة ---
        if not user_status["is_premium"]:
            await query.edit_message_text(
                PREMIUM_GATE_TEXT,
                reply_markup=get_premium_gate_keyboard(),
                parse_mode="Markdown"
            )
            return
        context.user_data["awaiting_trim"] = True
        cancel_kb = [[InlineKeyboardButton("🔙 إلغاء والرجوع للقائمة", callback_data="choose_type_back")]]
        await query.edit_message_text(
            "✂️ **أداة قص مقطع محدد من الفيديو (Trim Video):**\n\n"
            "أرسل الآن رسالة تحتوي على **وقت البداية** و **وقت النهاية** للمقطع المطلوب.\n\n"
            "📝 **أمثلة مقبولة:**\n"
            "• `00:15 00:45` (من الثانية 15 إلى الثانية 45)\n"
            "• `01:30 02:15` (من الدقيقة 1:30 إلى الدقيقة 2:15)\n"
            "• `10 50` (من 10 ثواني إلى 50 ثانية)\n\n"
            "⚡ سيتم تنزيل هذا الجزء وقصه فقط بجودة عالية وبدون استهلاك بياناتك!",
            reply_markup=InlineKeyboardMarkup(cancel_kb),
            parse_mode="Markdown"
        )
        return

    elif data == "choose_type_gif":
        await query.answer()
        # --- بوابة البريميوم: GIF ميزة مدفوعة ---
        if not user_status["is_premium"]:
            await query.edit_message_text(
                PREMIUM_GATE_TEXT,
                reply_markup=get_premium_gate_keyboard(),
                parse_mode="Markdown"
            )
            return
        await process_video_to_gif(update, context)
        return

    elif data == "choose_type_image":
        # تنزيل واستخراج الصور لكل الفئات مجاناً وبأعلى دقة
        await query.answer()
        status_msg = await query.edit_message_text(
            "🖼️ **جاري استخراج وتحميل الصور بأعلى جودة متوفرة...**\n⏳ _لحظات من فضلك..._",
            parse_mode="Markdown"
        )
        try:
            image_paths, title = await extract_images_from_link(url, update.effective_user.id)
            if not image_paths:
                raise ValueError("لم يتم العثور على أي صورة صالحة.")

            count = len(image_paths)
            await status_msg.edit_text(f"📤 **جاري إرسال {count} صورة حسب المنشور بدقة كاملة...**")
            
            # إذا كان المنشور يحتوي على أكثر من صورة (من 2 إلى 10)، نرسلها كألبوم مجمّع (Media Group / Carousel)
            if count > 1:
                media_group = []
                opened_files = []
                for idx, img_path in enumerate(image_paths):
                    f_img = open(img_path, "rb")
                    opened_files.append(f_img)
                    caption = f"🖼️ **{title}**\n📊 تم استخراج {count} صور من المنشور\n🤖 بواسطة: @OmniFetch45_Bot" if idx == 0 else ""
                    media_group.append(InputMediaPhoto(media=f_img, caption=caption, parse_mode="Markdown" if caption else None))
                
                try:
                    await query.message.reply_media_group(media=media_group)
                except Exception as media_err:
                    logger.warning(f"reply_media_group failed ({media_err}), sending photos individually...")
                    for idx, img_path in enumerate(image_paths):
                        try:
                            with open(img_path, "rb") as f_single:
                                await query.message.reply_photo(
                                    photo=f_single,
                                    caption=f"🖼️ **{title}** ({idx+1}/{count})\n🤖 بواسطة: @OmniFetch45_Bot",
                                    parse_mode="Markdown"
                                )
                        except Exception:
                            pass
                finally:
                    for f in opened_files:
                        try:
                            f.close()
                        except Exception:
                            pass
            else:
                # إذا كانت صورة واحدة فقط
                with open(image_paths[0], "rb") as f_img:
                    await query.message.reply_photo(
                        photo=f_img,
                        caption=f"🖼️ **{title}**\n🤖 بواسطة: @OmniFetch45_Bot",
                        parse_mode="Markdown"
                    )

            # إرسال الملفات الأصلية بدون ضغط مع أزرار التحويل
            for img_path in image_paths:
                filename_clean = os.path.basename(img_path)
                with open(img_path, "rb") as f_doc:
                    conv_keyboard = [
                        [
                            InlineKeyboardButton("🔄 تحويل لـ PNG", callback_data=f"conv_img_png_{filename_clean}"),
                            InlineKeyboardButton("🔄 تحويل لـ JPG", callback_data=f"conv_img_jpg_{filename_clean}"),
                        ],
                        [
                            InlineKeyboardButton("🔄 تحويل لـ WEBP", callback_data=f"conv_img_webp_{filename_clean}"),
                            InlineKeyboardButton("📄 تحويل لـ PDF", callback_data=f"conv_img_pdf_{filename_clean}"),
                        ]
                    ]
                    try:
                        await query.message.reply_document(
                            document=f_doc,
                            filename=filename_clean,
                            caption=f"📁 **الملف الأصلي بدون ضغط ({filename_clean})**",
                            reply_markup=InlineKeyboardMarkup(conv_keyboard),
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass
                await asyncio.sleep(0.35)

            await status_msg.edit_text(
                f"✅ **تم تنزيل {count} صورة بنجاح حسب المنشور وبدقة كاملة!**\n"
                "💡 يمكنك أيضاً الضغط على أزرار التحويل المرفقة أسفل كل ملف لتغيير صيغته فوراً.",
                parse_mode="Markdown"
            )

        except Exception as e:
            logger.error(f"Image extraction error: {e}")
            clean_err = str(e).replace("`", "").replace("*", "").replace("_", "")[:180]
            keyboard = [
                [InlineKeyboardButton("📹 تجربة كفيديو", callback_data="choose_type_video")],
                [InlineKeyboardButton("💬 تواصل مع المدير", url=ADMIN_URL)]
            ]
            await status_msg.edit_text(
                f"⚠️ **تعذر استخراج صور من هذا الرابط:**\n{clean_err}\n\nقد يكون هذا الرابط مخصصاً للفيديو فقط أو يتطلب صلاحيات.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        return

    elif data == "choose_type_video":
        # فحص الحد اليومي للفيديوهات للحسابات المجانية
        if not user_status["is_premium"] and user_status["count"] >= DAILY_FREE_LIMIT:
            keyboard = [
                [InlineKeyboardButton("💎 الترقية للتحميل غير المحدود", callback_data="upgrade_menu")],
                [InlineKeyboardButton("💬 تواصل مع المدير @CYS_ALI3", url=ADMIN_URL)]
            ]
            await query.edit_message_text(
                f"⚠️ **عذراً، لقد استهلكت رصيدك اليومي المجاني من الفيديوهات ({DAILY_FREE_LIMIT}/{DAILY_FREE_LIMIT})!**\n\n"
                f"يتجدد رصيدك تلقائياً عند حلول منتصف الليل.\n"
                f"💎 يمكنك الترقية إلى باقة البريميوم لتحميل غير محدود وبأعلى جودة، أو مراسلة المدير مباشرة.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            return

        await query.answer()
        await query.edit_message_text(
            "🔍 **جاري فحص دقات الفيديو المتاحة لهذا الرابط...**\n⏳ _لحظات من فضلك..._",
            parse_mode="Markdown"
        )

        loop = asyncio.get_running_loop()
        def extract_video_resolutions():
            ydl_opts_info = {
                "quiet": True,
                "noplaylist": True,
                "nocheckcertificate": True,
                "socket_timeout": 25,
                "geo_bypass": True,
                "no_color": True,
                "http_headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
                },
                "extractor_args": {
                    "youtube": {
                        "player_client": ["ios", "android", "mweb"]
                    }
                }
            }
            if os.path.exists(COOKIES_FILE):
                ydl_opts_info["cookiefile"] = COOKIES_FILE

            with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            info = await loop.run_in_executor(None, extract_video_resolutions)
            if not info:
                raise ValueError("لم يتم استخراج بيانات الفيديو.")

            # فحص البث المباشر
            if info.get('is_live') or info.get('live_status') in ['is_live', 'is_upcoming']:
                await query.edit_message_text("⚠️ **هذا الرابط عبارة عن بث مباشر!**\nالبوت يدعم فقط المقاطع والفيديوهات المسجلة.")
                return

            formats = info.get("formats", [])
            raw_heights = {f['height'] for f in formats if f.get('height') and f['height'] >= 144}
            
            # تقريب الدقات للقيم القياسية الشائعة وتنسيقها
            standard_buckets = [2160, 1440, 1080, 720, 480, 360, 240, 144]
            detected_heights = set()
            for h in raw_heights:
                closest = min(standard_buckets, key=lambda x: abs(x - h))
                if abs(closest - h) <= 80:
                    detected_heights.add(closest)
                else:
                    detected_heights.add(h)

            heights = sorted(list(detected_heights), reverse=True)
            if not heights:
                heights = [1080, 720, 480, 360]

            duration_sec = info.get("duration", 0)
            if duration_sec:
                mins, secs = divmod(duration_sec, 60)
                duration_str = f"{mins:02d}:{secs:02d}"
            else:
                duration_str = "غير محدد"

            context.user_data["media_meta"] = {
                "title": info.get("title", "فيديو"),
                "duration_str": duration_str,
                "duration": duration_sec,
                "uploader": info.get("uploader", "OmniFetch"),
            }

        except Exception as e:
            logger.error(f"Format extraction error: {e}")
            heights = [1080, 720, 480, 360]
            context.user_data["media_meta"] = {
                "title": "فيديو",
                "duration_str": "غير محدد",
                "duration": 0,
                "uploader": "OmniFetch",
            }

        buttons = []
        row = []
        is_prem = user_status["is_premium"]

        for h in heights:
            if h > 720 and not is_prem:
                btn = InlineKeyboardButton(f"🔒 {h}p (بريميوم)", callback_data=f"locked_prem_{h}")
            else:
                btn = InlineKeyboardButton(f"📹 {h}p MP4", callback_data=f"dl_vid_{h}")
            
            row.append(btn)
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        buttons.append([InlineKeyboardButton("🔙 رجوع للقائمة السابقة", callback_data="choose_type_back")])

        meta = context.user_data.get("media_meta", {})
        title_display = meta.get("title", "فيديو")[:60]
        dur_display = meta.get("duration_str", "")

        dur_line = f"⏱ **المدة:** `{dur_display}`\n" if dur_display and dur_display != "غير محدد" else ""

        await query.edit_message_text(
            f"🎬 **اختر دقة الفيديو المطلوبة:**\n\n"
            f"📌 **المقطع:** {title_display}\n"
            f"{dur_line}\n"
            f"💡 **تنويه:** التحميل مجاني حتى **720p**، ودقات (1080p, 2K, 4K) مخصصة لمشتركي البريميوم 💎.",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
        return

    elif data == "choose_type_audio":
        await query.answer()
        audio_buttons = [
            [
                InlineKeyboardButton("🎵 MP3 - 320 kbps (أعلى نقاوة HQ)", callback_data="dl_aud_320"),
            ],
            [
                InlineKeyboardButton("🎵 MP3 - 192 kbps (جودة ممتازة متوازنة)", callback_data="dl_aud_192"),
            ],
            [
                InlineKeyboardButton("🎵 MP3 - 128 kbps (حجم خفيف وسريع)", callback_data="dl_aud_128"),
            ],
            [
                InlineKeyboardButton("🔙 رجوع للقائمة السابقة", callback_data="choose_type_back")
            ]
        ]
        await query.edit_message_text(
            "🎵 **اختر نقاوة وجودة ملف الصوت (MP3):**\n\n"
            "متاح للجميع مجاناً بجميع معدلات البت العالية وبدون أي قيود.",
            reply_markup=InlineKeyboardMarkup(audio_buttons),
            parse_mode="Markdown"
        )
        return

# --- معالجة وتنزيل الوسائط (DOWNLOAD ENGINE) ---
async def download_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    url = context.user_data.get("pending_url")
    if not url:
        await query.edit_message_text("❌ انتهت صلاحية الجلسة، يرجى إعادة إرسال الرابط من جديد.")
        return

    user_id = update.effective_user.id
    status = get_or_update_user(update.effective_user)
    if status["is_banned"]:
        await query.edit_message_text("⛔ أنت محظور من استخدام البوت.")
        return

    # رسالة الانتظار الأولى
    status_msg = await query.edit_message_text(
        "🔍 **[1/3] جاري فحص الرابط واستخراج البيانات...**\n"
        "⏳ _يرجى الانتظار لحظات..._",
        parse_mode="Markdown"
    )

    out_template = os.path.join(DOWNLOAD_DIR, f"{user_id}_%(id)s.%(ext)s")
    is_audio = query.data.startswith("dl_aud_")

    ydl_opts = {
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "ignoreerrors": False,
        "socket_timeout": 35,
        "geo_bypass": True,
        "no_color": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android", "mweb"]
            }
        }
    }
    if os.path.exists(COOKIES_FILE):
        ydl_opts["cookiefile"] = COOKIES_FILE

    if is_audio:
        bitrate = query.data.replace("dl_aud_", "")
        ydl_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(bitrate),
            }],
        })
    else:
        target_height = int(query.data.replace("dl_vid_", ""))
        ydl_opts.update({
            "format": f"bestvideo[height<={target_height}]+bestaudio/best[height<={target_height}]/best",
            "merge_output_format": "mp4",
        })

    downloaded_file: Optional[str] = None
    try:
        # رسالة الانتظار الثانية
        await status_msg.edit_text(
            "⬇️ **[2/3] جاري التنزيل والمعالجة من السيرفر...**\n"
            "⚡ _جاري تجهيز الملف بأقصى سرعة..._",
            parse_mode="Markdown"
        )

        loop = asyncio.get_running_loop()
        def run_dl():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info), info

        raw_filename, info_dict = await loop.run_in_executor(None, run_dl)

        # البحث الذكي عن الملف النهائي على القرص
        def find_final_file(expected_path: str):
            if os.path.exists(expected_path) and not expected_path.endswith((".part", ".ytdl")):
                return expected_path
            base, _ = os.path.splitext(expected_path)
            for ext in [".mp4", ".mp3", ".mkv", ".webm", ".m4a"]:
                if os.path.exists(base + ext):
                    return base + ext
            prefix = f"{user_id}_"
            candidates = []
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(prefix) and not f.endswith((".part", ".ytdl")):
                    fp = os.path.join(DOWNLOAD_DIR, f)
                    if os.path.isfile(fp):
                        candidates.append((os.path.getmtime(fp), fp))
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                return candidates[0][1]
            return None

        downloaded_file = find_final_file(raw_filename)

        if not downloaded_file or not os.path.exists(downloaded_file):
            raise FileNotFoundError("لم يتم العثور على الملف بعد التنزيل.")

        # فحص حجم الملف وفقاً لحد تيليجرام للبوتات (50 ميغابايت)
        file_size = os.path.getsize(downloaded_file)
        MAX_TELEGRAM_LIMIT = 50 * 1024 * 1024  # 50MB
        if file_size > MAX_TELEGRAM_LIMIT:
            size_mb = file_size / (1024 * 1024)
            retry_keyboard = [
                [InlineKeyboardButton("🎵 تحميل كصوت MP3 بدلاً من ذلك", callback_data="dl_aud_192")],
                [InlineKeyboardButton("📹 تجربة دقة أقل (480p)", callback_data="dl_vid_480")],
                [InlineKeyboardButton("📹 تجربة دقة (360p)", callback_data="dl_vid_360")],
                [InlineKeyboardButton("💬 تواصل مع المدير @CYS_ALI3", url=ADMIN_URL)],
            ]
            await status_msg.edit_text(
                f"⚠️ **حجم المقطع كبير ({size_mb:.1f} ميجابايت)!**\n\n"
                f"تطبيق تيليجرام يفرض حداً أقصى **50MB** لرفع الملفات عبر البوتات.\n\n"
                f"💡 **حل فوري:** يمكنك الضغط على أحد الخيارات أدناه لتحميله بحجم أصغر فوراً:",
                reply_markup=InlineKeyboardMarkup(retry_keyboard),
                parse_mode="Markdown"
            )
            return

        # رسالة الانتظار الثالثة
        await status_msg.edit_text(
            "📤 **[3/3] جاري رفع الملف إلى تيليجرام...**\n"
            "🚀 _شارفنا على الانتهاء!_",
            parse_mode="Markdown"
        )

        filename_clean = os.path.basename(downloaded_file)
        media_title = (info_dict.get("title") or "OmniFetch Media")[:100]
        uploader_name = info_dict.get("uploader") or "OmniFetch"
        duration_int = int(info_dict.get("duration") or 0)

        caption_text = f"🎬 **{media_title}**\n🤖 بواسطة: @OmniFetch45_Bot"

        with open(downloaded_file, "rb") as f:
            if is_audio:
                try:
                    await query.message.reply_audio(
                        audio=f,
                        title=media_title,
                        performer=uploader_name,
                        duration=duration_int,
                        filename=filename_clean,
                        read_timeout=300,
                        write_timeout=300,
                        connect_timeout=60,
                    )
                except Exception as aud_err:
                    logger.warning(f"reply_audio failed ({aud_err}), falling back to reply_document...")
                    f.seek(0)
                    await query.message.reply_document(
                        document=f,
                        caption=f"🎵 {media_title}\n🤖 بواسطة: @OmniFetch45_Bot",
                        filename=filename_clean,
                        read_timeout=300,
                        write_timeout=300,
                        connect_timeout=60,
                    )
            else:
                try:
                    await query.message.reply_video(
                        video=f,
                        caption=caption_text,
                        supports_streaming=True,
                        duration=duration_int,
                        filename=filename_clean,
                        parse_mode="Markdown",
                        read_timeout=300,
                        write_timeout=300,
                        connect_timeout=60,
                    )
                except Exception as vid_err:
                    logger.warning(f"reply_video failed ({vid_err}), falling back to reply_document...")
                    f.seek(0)
                    await query.message.reply_document(
                        document=f,
                        caption=caption_text,
                        filename=filename_clean,
                        read_timeout=300,
                        write_timeout=300,
                        connect_timeout=60,
                    )

        # احتساب التحميل فقط بعد تأكيد الإرسال بنجاح
        if not is_audio:
            increment_confirmed_download(user_id, is_free_tier=not status["is_premium"])
        refreshed_status = get_or_update_user(update.effective_user)

        if not status["is_premium"]:
            remaining_today = max(0, DAILY_FREE_LIMIT - refreshed_status['count'])
            await status_msg.edit_text(
                f"✅ **تم التحميل والإرسال بنجاح!**\n"
                f"📊 التحميلات المجانية المتبقية لك اليوم: **{remaining_today}**\n"
                f"📁 إجمالي تحميلاتك: **{refreshed_status['total_downloads']}**\n"
                f"💎 للتحميل غير المحدود اشترك عبر: `/upgrade`",
                parse_mode="Markdown"
            )
        else:
            await status_msg.edit_text(
                f"✅ **تم التحميل والإرسال بنجاح!**\n"
                f"💎 باقة بريميوم نشطة (متبقي {status['days_left']} يوم)\n"
                f"📁 إجمالي تحميلاتك: **{refreshed_status['total_downloads']}**",
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"Download failed: {e}")
        clean_err = str(e).replace("`", "").replace("*", "").replace("_", "")[:180]
        keyboard = [[InlineKeyboardButton("💬 تواصل مع المدير @CYS_ALI3", url=ADMIN_URL)]]
        await status_msg.edit_text(
            f"❌ **عذراً، فشلت معالجة هذا الرابط:**\n{clean_err}\n\nإذا استمرت المشكلة، يمكنك التواصل مع الإدارة:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    finally:
        # مسح جميع الملفات المؤقتة الخاصة بهذا المستخدم
        try:
            prefix = f"{user_id}_"
            for fname in os.listdir(DOWNLOAD_DIR):
                if fname.startswith(prefix) and not fname.endswith(IMAGE_EXTENSIONS):
                    f_path = os.path.join(DOWNLOAD_DIR, fname)
                    try:
                        if os.path.isfile(f_path):
                            os.remove(f_path)
                    except Exception:
                        await asyncio.sleep(0.5)
                        try:
                            if os.path.isfile(f_path):
                                os.remove(f_path)
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

# --- معالجة الصور المرفوعة مباشرة من المستخدم (DIRECT PHOTO CONVERTER) ---
async def handle_direct_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استقبال الصور المرسلة مباشرة إلى البوت وإتاحة تحويلها إلى أي صيغة لكل الفئات مجاناً."""
    user = update.effective_user
    user_status = get_or_update_user(user)
    if user_status["is_banned"]:
        await update.message.reply_text("⛔ أنت محظور من استخدام البوت.")
        return

    # استخراج معرف الملف
    file_id = None
    original_ext = ".jpg"
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        original_ext = ".jpg"
    elif update.message.document:
        doc = update.message.document
        file_id = doc.file_id
        _, ext = os.path.splitext(doc.file_name or "")
        original_ext = ext if ext else ".jpg"

    if not file_id:
        return

    dest_file = os.path.join(DOWNLOAD_DIR, f"{user.id}_upload{original_ext}")
    tg_file = await context.bot.get_file(file_id)
    await tg_file.download_to_drive(custom_path=dest_file)

    try:
        with Image.open(dest_file) as img:
            w, h = img.size
            format_name = img.format or original_ext.replace(".", "").upper()
            dim_str = f"{w}x{h}"
    except Exception:
        dim_str = "غير محدد"
        format_name = original_ext.replace(".", "").upper()

    clean_name = os.path.basename(dest_file)
    keyboard = [
        [
            InlineKeyboardButton("🔄 تحويل إلى PNG (عالي الجودة)", callback_data=f"conv_img_png_{clean_name}"),
            InlineKeyboardButton("🔄 تحويل إلى JPG (قياسي)", callback_data=f"conv_img_jpg_{clean_name}"),
        ],
        [
            InlineKeyboardButton("🔄 تحويل إلى WEBP (خفيف وسريع)", callback_data=f"conv_img_webp_{clean_name}"),
            InlineKeyboardButton("📄 تحويل إلى PDF (مستند جاهز للطباعة)", callback_data=f"conv_img_pdf_{clean_name}"),
        ],
        [
            InlineKeyboardButton("📥 إرسال كمستند أصلي بدون ضغط", callback_data=f"send_raw_doc_{clean_name}"),
        ],
        [
            InlineKeyboardButton("💬 تواصل مع المدير 👨‍💼", url=ADMIN_URL),
        ]
    ]

    await update.message.reply_text(
        f"🖼️ **تم استلام الصورة بنجاح!**\n\n"
        f"📐 **الأبعاد:** `{dim_str}`\n"
        f"🏷 **الصيغة الحالية:** `{format_name}`\n\n"
        f"اختر الصيغة التي ترغب في تحويل الصورة إليها فوراً وبدقة كاملة (مجاناً لجميع الفئات):",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def image_conversion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنفيذ تحويل الصور وإرسالها للمستخدم فوراً."""
    query = update.callback_query
    await query.answer()
    data = query.data

    user_id = update.effective_user.id
    target_format = None
    filename = None

    if data.startswith("conv_img_"):
        parts = data.split("_", 3)
        target_format = parts[2]
        filename = parts[3]
    elif data.startswith("send_raw_doc_"):
        filename = data.replace("send_raw_doc_", "")

    if not filename:
        await query.edit_message_text("❌ لم يتم العثور على الملف المطلوب.")
        return

    source_path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(source_path):
        await query.edit_message_text("❌ انتهت صلاحية الصورة على الخادم، يرجى إعادة إرسالها مجدداً.")
        return

    if target_format:
        await query.edit_message_text(f"⏳ **جاري تحويل الصورة إلى صيغة {target_format.upper()} بأقصى جودة...**")
        try:
            converted_path = convert_image_format(source_path, target_format)
            out_name = os.path.basename(converted_path)
            with open(converted_path, "rb") as f_out:
                if target_format.upper() in ["JPG", "JPEG", "PNG", "WEBP"]:
                    # إرسال الصورة كمعاينة ومستند
                    try:
                        f_out.seek(0)
                        await query.message.reply_photo(
                            photo=f_out,
                            caption=f"✅ **تم التحويل بنجاح إلى صيغة {target_format.upper()}!**\n🤖 بواسطة: @OmniFetch45_Bot",
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass
                
                f_out.seek(0)
                await query.message.reply_document(
                    document=f_out,
                    filename=out_name,
                    caption=f"📁 **الملف المحول بجودة أصلية:** `{out_name}`",
                    parse_mode="Markdown"
                )

            await query.edit_message_text(f"✅ **اكتمل التحويل بنجاح إلى {target_format.upper()}!**")
        except Exception as e:
            logger.error(f"Image conversion error: {e}")
            await query.edit_message_text(f"❌ حدث خطأ أثناء التحويل: {e}")

    else:
        # إرسال الملف كمستند أصلي بدون ضغط
        await query.edit_message_text("📤 **جاري رفع المستند الأصلي بدون أي ضغط...**")
        try:
            with open(source_path, "rb") as f_raw:
                await query.message.reply_document(
                    document=f_raw,
                    filename=filename,
                    caption=f"📁 **الصورة الأصلية بدقة 100%:** `{filename}`",
                    parse_mode="Markdown"
                )
            await query.edit_message_text("✅ **تم إرسال الملف الأصلي بنجاح!**")
        except Exception as e:
            await query.edit_message_text(f"❌ حدث خطأ أثناء الإرسال: {e}")

# --- تشغيل البوت (MAIN RUNNER) ---
def main():
    # تشغيل خادم الويب الخفيف في الخلفية لضمان عمل البوت 24/7 على الاستضافات السحابية
    threading.Thread(target=run_health_server, daemon=True).start()

    # تكوين اتصال متين بشبكة تيليجرام مع زيادة مهلة الاتصال ورفع الملفات
    request = HTTPXRequest(connect_timeout=60.0, read_timeout=300.0, write_timeout=300.0)
    app = ApplicationBuilder().token(BOT_TOKEN).request(request).post_init(post_init_commands).build()

    # أوامر المستخدمين العامة (موسعة)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler(["translate", "tr"], translate_command))
    app.add_handler(CommandHandler("trim", trim_command))
    app.add_handler(CommandHandler("gif", gif_command))
    app.add_handler(CommandHandler(["status", "me"], status_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("contact", contact_command))
    app.add_handler(CommandHandler("admin_contact", contact_command))
    app.add_handler(CommandHandler("id", my_id_command))
    app.add_handler(CommandHandler("myid", my_id_command))
    app.add_handler(CommandHandler(["upgrade", "plans", "prices"], upgrade_command))
    app.add_handler(CommandHandler("ping", ping_command))

    # لوحة تحكم المدير وأوامر الإدارة (موسعة)
    app.add_handler(CommandHandler("admin", admin_panel_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("addpremium", add_premium_command))
    app.add_handler(CommandHandler("rempremium", rem_premium_command))
    app.add_handler(CommandHandler("resetdaily", reset_daily_command))
    app.add_handler(CommandHandler("clean", clean_temp_files_command))
    app.add_handler(CommandHandler("user", user_search_command))
    app.add_handler(CommandHandler("find", user_search_command))
    app.add_handler(CommandHandler("addadmin", add_admin_command))
    app.add_handler(CommandHandler("deladmin", del_admin_command))
    app.add_handler(CommandHandler("admins", list_admins_command))
    app.add_handler(CommandHandler("backup", backup_command))

    # أزرار لوحة الإدارة ونظام الموافقة والرفض وإجراءات المستخدمين
    app.add_handler(CallbackQueryHandler(admin_button_callback, pattern="^(admin_|approve_sub_|reject_sub_|act_|search_user_prompt|manage_admins|prompt_add_admin)"))

    # معالجة الدفع (فيزا / ماستركارد ونجوم تيليجرام وطلبات الاشتراك)
    app.add_handler(CallbackQueryHandler(send_payment_invoice, pattern="^(upgrade_menu|show_help|pay_card_30|pay_stars_30|pay_crypto|request_sub_manual)$"))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))

    # تحويل الصور وإرسال المستندات
    app.add_handler(CallbackQueryHandler(image_conversion_callback, pattern="^(conv_img_|send_raw_doc_)"))

    # قائمة اختيار نوع الوسائط والدقات والترجمة
    app.add_handler(CallbackQueryHandler(media_selection_callback, pattern="^(choose_type_|locked_prem_|tr_lang_)"))

    # أزرار تنزيل الوسائط بالدقة المطلوبة
    app.add_handler(CallbackQueryHandler(download_button_callback, pattern="^(dl_vid_|dl_aud_)"))

    # استقبال الصور والمستندات المرسلة مباشرة
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_direct_photo))

    # استقبال الروابط النصية ورسائل البحث
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

    async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Exception in telegram handler:", exc_info=context.error)

    app.add_error_handler(global_error_handler)

    print("OmniFetch Bot is online and listening... (البوت يعمل الآن بنجاح مع دعم الصور والتواصل)")
    app.run_polling(bootstrap_retries=10)

if __name__ == "__main__":
    main()
