import os
import sys
import re
import io
import asyncio
import logging
import sqlite3
import threading
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
        f"👋 أهلاً بك في **بوت OmniFetch الشامل**!\n\n"
        f"🎯 **ماذا يمكن للبوت تحميله ومعالجته؟**\n"
        f"• **فيديوهات:** يوتيوب، تيك توك، إنستغرام، فيسبوك، X (تويتر)، ساوند كلاود.\n"
        f"• **صوتيات:** استخراج MP3 بنقاوة عالية (320kbps / 192kbps / 128kbps).\n"
        f"• **صور وألبومات:** تحميل الصور بأي صيغة وتحويلها فوراً (PNG, JPG, WEBP, PDF).\n\n"
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
        "📖 **دليل استخدام بوت OmniFetch:**\n\n"
        "1️⃣ **كيفية التحميل:**\n"
        "فقط أرسل أي رابط فيديو أو صورة أو منشور (تيك توك، إنستغرام، يوتيوب، بينترست، X، فيسبوك).\n\n"
        "2️⃣ **دعم الصور (لكل الفئات وبأي صيغة):**\n"
        "• يمكنك إرسال روابط صور أو منشورات إنستغرام/بينترست لتحميلها بأعلى نقاوة.\n"
        "• يمكنك إرسال أي صورة مباشرة للبوت لتحويلها إلى (PNG, JPG, WEBP, PDF) فوراً!\n\n"
        "3️⃣ **الجودات والدقات:**\n"
        "• **فيديو:** مجاني حتى 720p، ودقات (1080p, 2K, 4K) للبريميوم.\n"
        "• **صوت:** MP3 مجاني للجميع بجميع الجودات حتى 320kbps.\n"
        "• **صور:** مجانية لجميع الفئات بأعلى دقة متوفرة كصورة وكمستند أصلي.\n\n"
        "4️⃣ **الأوامر المتوفرة:**\n"
        "• `/start` — بدء البوت وعرض الرصيد\n"
        "• `/upgrade` — ترقية الحساب إلى بريميوم\n"
        "• `/contact` — التواصل مع المدير مباشرة\n"
        "• `/id` — معرفة رقم الآيدي الخاص بك\n"
        "• `/help` — عرض هذه التعليمات\n\n"
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
            "• **السعر:** 5.00$ فقط (أو 250 نجمة تيليجرام)\n"
            "• تحميل فيديو **غير محدود** بدون حد يومي\n"
            "• فتح دقات العرض الفائقة: **1080p و 2K و 4K**\n"
            "• أولوية قصوى وأعلى سرعة معالجة وتحويل على السيرفر\n"
            "• دعم جميع المنصات ومقاطع الصوت والصور بأقصى جودة\n\n"
            f"📞 للدفع بالتحويل المحلي أو العملات الرقمية تواصل مع المدير: `{ADMIN_USERNAME}`\n\n"
            "اختر وسيلة الدفع التي تناسبك:",
            reply_markup=InlineKeyboardMarkup(keyboard),
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
        "• `/admins` — عرض قائمة المدراء المشرفين"
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

async def admin_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة جميع أزرار لوحة تحكم المدير وبطاقات المستخدمين."""
    query = update.callback_query
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await query.answer("⛔ غير مصرح لك.", show_alert=True)
        return

    data = query.data

    if data == "admin_panel_home":
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

# --- استقبال الروابط (MEDIA HANDLER) ---
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_status = get_or_update_user(update.effective_user)
    user_id = update.effective_user.id

    if user_status["is_banned"]:
        await update.message.reply_text("⛔ عذراً، لقد تم حظرك من استخدام هذا البوت من قبل الإدارة.")
        return

    text = (update.message.text or "").strip()

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

    clean_url = url_match.group(0).rstrip(").,;!?\"'")
    context.user_data["pending_url"] = clean_url

    keyboard = [
        [
            InlineKeyboardButton("📹 تحميل كفيديو (Video)", callback_data="choose_type_video"),
            InlineKeyboardButton("🎵 تحميل كصوت (Audio)", callback_data="choose_type_audio"),
        ],
        [
            InlineKeyboardButton("🖼️ استخراج الصور / الألبوم (Image)", callback_data="choose_type_image"),
        ],
        [
            InlineKeyboardButton("💬 تواصل مع المدير 👨‍💼", url=ADMIN_URL),
        ]
    ]
    await update.message.reply_text(
        "🎯 **اختر نوع التحميل المطلوب لهذا الرابط:**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# --- قائمة اختيار الدقات والجودات التفاعلية ---
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
        keyboard = [
            [
                InlineKeyboardButton("📹 تحميل كفيديو (Video)", callback_data="choose_type_video"),
                InlineKeyboardButton("🎵 تحميل كصوت (Audio)", callback_data="choose_type_audio"),
            ],
            [
                InlineKeyboardButton("🖼️ استخراج الصور / الألبوم (Image)", callback_data="choose_type_image"),
            ],
            [
                InlineKeyboardButton("💬 تواصل مع المدير 👨‍💼", url=ADMIN_URL),
            ]
        ]
        await query.edit_message_text(
            "🎯 **اختر نوع التحميل المطلوب:**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
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
    app = ApplicationBuilder().token(BOT_TOKEN).request(request).build()

    # أوامر المستخدمين العامة
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("contact", contact_command))
    app.add_handler(CommandHandler("admin_contact", contact_command))
    app.add_handler(CommandHandler("id", my_id_command))
    app.add_handler(CommandHandler("myid", my_id_command))
    app.add_handler(CommandHandler("upgrade", upgrade_command))

    # لوحة تحكم المدير وأوامر الإدارة
    app.add_handler(CommandHandler("admin", admin_panel_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("addpremium", add_premium_command))
    app.add_handler(CommandHandler("user", user_search_command))
    app.add_handler(CommandHandler("find", user_search_command))
    app.add_handler(CommandHandler("addadmin", add_admin_command))
    app.add_handler(CommandHandler("deladmin", del_admin_command))
    app.add_handler(CommandHandler("admins", list_admins_command))

    # أزرار لوحة الإدارة ونظام الموافقة والرفض وإجراءات المستخدمين
    app.add_handler(CallbackQueryHandler(admin_button_callback, pattern="^(admin_|approve_sub_|reject_sub_|act_|search_user_prompt|manage_admins|prompt_add_admin)"))

    # معالجة الدفع (فيزا / ماستركارد ونجوم تيليجرام وطلبات الاشتراك)
    app.add_handler(CallbackQueryHandler(send_payment_invoice, pattern="^(upgrade_menu|show_help|pay_card_30|pay_stars_30|pay_crypto|request_sub_manual)$"))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))

    # تحويل الصور وإرسال المستندات
    app.add_handler(CallbackQueryHandler(image_conversion_callback, pattern="^(conv_img_|send_raw_doc_)"))

    # قائمة اختيار نوع الوسائط والدقات
    app.add_handler(CallbackQueryHandler(media_selection_callback, pattern="^(choose_type_|locked_prem_)"))

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
