"""
Solvency - SMS-Based Budget Tracking & Financial Intelligence
=============================================================
Flask backend: parses SMS transactions, stores in SQLite,
provides analytics, budget simulation, fraud/waste detection,
and spending personality. No external paid APIs - runs locally.

Now extended with:
- Email-based login: user enters email, receives password in mailbox
- Per-user data: transactions and fixed deposits are tied to each account
"""

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    session,
    redirect,
    url_for,
)
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import re
import os
import json
import urllib.request
from collections import defaultdict
from email.message import EmailMessage
import smtplib
import secrets
import string
from functools import wraps

# Gemini/GenAI client is optional; app should still run without it.
try:
    import google.generativeai as genai
except ImportError:  # module may not be installed in offline or minimal environments
    genai = None


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env_local():
    for name in ('env.local', '.env'):
        path = os.path.join(BASE_DIR, name)
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith('#'):
                        continue
                    if '=' in s:
                        k, v = s.split('=', 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v


_load_env_local()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GENAI_API_KEY")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GENAI_API_KEY")

# Only create a client if the import succeeded and we have a key
if genai and GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GENAI_MODEL = genai.GenerativeModel('gemini-1.5-flash')
        print("Gemini AI initialized successfully")
    except Exception as e:
        GENAI_MODEL = None
        print(f"Failed to initialize Gemini AI client: {e}")
else:
    GENAI_MODEL = None
    if not genai:
        print("google.generativeai module not available; AI features disabled")
    elif not GEMINI_API_KEY:
        print("GEMINI_API_KEY / GENAI_API_KEY not found")

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'), static_folder=os.path.join(BASE_DIR, 'static'))

# Secret key for sessions (should be overridden via environment in production)
app.secret_key = os.environ.get("SOLVENCY_SECRET_KEY", "dev-change-me")

# Outgoing email configuration (adjust via environment variables)
EMAIL_SENDER = os.environ.get("SOLVENCY_EMAIL_SENDER")  # e.g. your Gmail
EMAIL_PASSWORD = os.environ.get("SOLVENCY_EMAIL_PASSWORD")  # app password
SMTP_SERVER = os.environ.get("SOLVENCY_SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SOLVENCY_SMTP_PORT", "587"))

# Google OAuth Configuration
# Authorized JavaScript origins (browser):
#   - http://localhost:5000
#   - http://127.0.0.1:5000
#   - https://ktctgjrzrudaystdmxhs.supabase.co
#   - https://solvency-lfj8.onrender.com
# Authorized redirect URIs (web server):
#   - http://localhost:5000/auth/supabase/callback
#   - http://127.0.0.1:5000/auth/supabase/callback
#   - https://ktctgjrzrudaystdmxhs.supabase.co/auth/v1/callback
#   - https://solvency-lfj8.onrender.com/auth/google
GOOGLE_CLIENT_ID = "717049491439-bk2knn2l7b6bm9htc2p2osgoleg2pf51v.apps.googleusercontent.com"

# Database path - uses project directory
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'budget_tracker.db')

# Category keywords for auto-detection from merchant names
# Maps category -> list of keywords that indicate this category
CATEGORY_KEYWORDS = {
    'Food': ['zomato', 'swiggy', 'dominos', 'mcdonalds', 'kfc', 'pizza', 'restaurant', 
             'cafe', 'starbucks', 'costa', 'dunzo', 'blinkit', 'zepto', 'food', 'grocery'],
    'Travel': ['uber', 'ola', 'rapido', 'irctc', 'railway', 'flight', 'petrol', 
               'fuel', 'parking', 'metro', 'bus'],
    'Shopping': ['amazon', 'flipkart', 'myntra', 'ajio', 'meesho', 'mall', 
                 'shopping', 'decathlon'],
    'Bills': ['electricity', 'recharge', 'jio', 'airtel', 'broadband', 'water',
              'gas', 'insurance', 'rent', 'emi', 'loan', 'subscription', 'netflix',
              'spotify', 'youtube'],
    'Other': []  # Default fallback
}

# Simple in-memory conversational history:
# chat_memory[user_id] = list of {"role": "user"|"assistant", "content": str}
chat_memory = {}

def get_current_user_id():
    """Return the currently logged-in user's ID, or None."""
    return session.get('user_id')


def login_required_json(fn):
    """Decorator for JSON API endpoints that require login."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        return fn(*args, **kwargs)

    return wrapper


def generate_temp_password(length: int = 8) -> str:
    """Generate a simple temporary password."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def send_login_password_email(to_email: str, plain_password: str) -> None:
    """
    Send an email with the generated password.
    If email credentials are not configured, the password is printed to the console.
    """
    if not EMAIL_SENDER or not EMAIL_PASSWORD:
        print(f"[EMAIL DEBUG] Send to {to_email}: password = {plain_password}")
        return
    msg = EmailMessage()
    msg['Subject'] = 'Your Solvency login password'
    msg['From'] = EMAIL_SENDER
    msg['To'] = to_email
    msg.set_content(
        "Welcome to Solvency!\n\n"
        f"Here is your password: {plain_password}\n\n"
        "Use this email and password to log in to Solvency anytime.\n"
    )
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
        smtp.send_message(msg)


OTP_LIFETIME_MINUTES = 10


def generate_otp(length: int = 6) -> str:
    """Generate a numeric OTP (e.g. 6 digits)."""
    return ''.join(secrets.choice(string.digits) for _ in range(length))


def send_otp_email(to_email: str, otp: str) -> None:
    """Send OTP to email for signup verification. Falls back to console if SMTP not set."""
    if not EMAIL_SENDER or not EMAIL_PASSWORD:
        print(f"[EMAIL DEBUG] OTP for {to_email}: {otp} (valid for {OTP_LIFETIME_MINUTES} min)")
        return
    msg = EmailMessage()
    msg['Subject'] = 'Your Solvency verification code'
    msg['From'] = EMAIL_SENDER
    msg['To'] = to_email
    msg.set_content(
        "Your Solvency verification code is:\n\n"
        f"  {otp}\n\n"
        f"This code is valid for {OTP_LIFETIME_MINUTES} minutes. Do not share it with anyone.\n"
    )
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
        smtp.send_message(msg)


def get_db_connection():
    """Create and return a database connection with row factory for dict-like access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    Initialize database tables if they don't exist.
    Creates transactions table (with debit/credit type), settings, and fixed_deposits.
    """
    conn = get_db_connection()
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            merchant TEXT NOT NULL,
            category TEXT NOT NULL,
            date TEXT NOT NULL,
            mode TEXT DEFAULT 'Unknown',
            transaction_type TEXT DEFAULT 'debit',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id INTEGER
        )
        '''
    )
    # Migration: add transaction_type or user_id if missing (existing DBs)
    try:
        conn.execute("ALTER TABLE transactions ADD COLUMN transaction_type TEXT DEFAULT 'debit'")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute("ALTER TABLE transactions ADD COLUMN user_id INTEGER")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS fixed_deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            interest_rate REAL NOT NULL,
            tenure_months INTEGER NOT NULL,
            start_date TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id INTEGER
        )
        '''
    )
    # Migration: add user_id to fixed_deposits if missing
    try:
        conn.execute("ALTER TABLE fixed_deposits ADD COLUMN user_id INTEGER")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    # Users table for email/password-based login
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    # Signup OTPs: email, otp, expires_at (for email verification before setting password)
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS signup_otps (
            email TEXT PRIMARY KEY,
            otp TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        '''
    )
    # Set default monthly budget, income, and starting balance if not set
    for key, default in [('monthly_budget', '50000'), ('monthly_income', '60000'), ('starting_balance', '0')]:
        cursor = conn.execute('SELECT 1 FROM settings WHERE key = ?', (key,))
        if cursor.fetchone() is None:
            conn.execute('INSERT INTO settings (key, value) VALUES (?, ?)', (key, default))
    conn.commit()
    conn.close()


def auto_detect_category(merchant: str) -> str:
    """
    Auto-detect category from merchant name using keyword matching.
    Returns one of: Food, Travel, Shopping, Bills, Other
    """
    merchant_lower = merchant.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if category == 'Other':
            continue
        for kw in keywords:
            if kw in merchant_lower:
                return category
    return 'Other'


def parse_sms(message: str) -> dict:
    """
    Parse SMS-style transaction message and extract structured data.
    Detects DEBIT (money out) vs CREDIT (money in) from message text.
    
    Handles formats like:
    - "INR 450 spent on ZOMATO via UPI on 03-02-26. Bal: 5230" -> debit
    - "Rs 50000 credited to a/c on 01-02-26. Bal: 100000" -> credit
    - "Rs 1200 debited for AMAZON on 02-02-2026" -> debit
    
    Returns dict with: amount, merchant, category, date, mode, transaction_type
    Returns None if parsing fails.
    """
    result = {}
    message = message.strip()
    msg_lower = message.lower()

    # Detect transaction type: CREDIT (money in) vs DEBIT (money out)
    credit_keywords = ['credited', 'credit of', 'received', 'deposit', 'deposited', 'credited to', 'salary', 'refund']
    debit_keywords = ['debited', 'debit of', 'spent', 'withdrawn', 'paid', 'paid to', 'withdrawal']
    if any(kw in msg_lower for kw in credit_keywords):
        result['transaction_type'] = 'credit'
    elif any(kw in msg_lower for kw in debit_keywords):
        result['transaction_type'] = 'debit'
    else:
        result['transaction_type'] = 'debit'  # default: assume expense

    # Extract amount - looks for INR, Rs, ₹ or standalone number
    amount_patterns = [
        r'(?:INR|Rs\.?|₹)\s*([\d,]+(?:\.\d{2})?)',
        r'([\d,]+(?:\.\d{2})?)\s*(?:spent|debited|paid|credited|received|deposit)',
        r'(?:spent|debited|paid|credited|received)\s*(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d{2})?)',
        r'(?:credit of|debit of)\s*(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d{2})?)',
    ]
    amount = None
    for pattern in amount_patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            amount = float(match.group(1).replace(',', ''))
            break
    if amount is None:
        match = re.search(r'\b(\d{2,}(?:\.\d{2})?)\b', message)
        if match:
            amount = float(match.group(1).replace(',', ''))
    if amount is None:
        return None
    result['amount'] = amount

    # Extract merchant
    merchant_patterns = [
        r'(?:spent on|debited for|paid to|credited to|at)\s+([A-Za-z0-9\s]+?)(?:\s+via|\s+on\s+\d|\.|$)',
        r'on\s+([A-Za-z0-9]+)\s+via',
        r'for\s+([A-Za-z0-9\s]+?)(?:\s+on\s+\d|\.|$)',
    ]
    merchant = 'Bank' if result['transaction_type'] == 'credit' else 'Unknown'
    for pattern in merchant_patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            merchant = match.group(1).strip()
            if len(merchant) > 2 and merchant.lower() not in ['inr', 'rs', 'the']:
                break
    if merchant == 'Unknown':
        words = message.split()
        for w in words:
            if w.isupper() and len(w) > 2 and not w.isdigit():
                merchant = w
                break
    result['merchant'] = merchant

    # Payment mode
    mode = 'Unknown'
    if re.search(r'\bUPI\b', message, re.I):
        mode = 'UPI'
    elif re.search(r'\bcard\b|debit card|credit card', message, re.I):
        mode = 'Card'
    elif re.search(r'\bcash\b', message, re.I):
        mode = 'Cash'
    elif re.search(r'\bnet ?banking\b|netbanking', message, re.I):
        mode = 'Net Banking'
    result['mode'] = mode

    # Date
    date_str = None
    date_patterns = [
        r'(\d{2})-(\d{2})-(\d{2,4})',
        r'(\d{2})/(\d{2})/(\d{2,4})',
    ]
    for pattern in date_patterns:
        match = re.search(pattern, message)
        if match:
            d, m, y = match.groups()
            year = int(y)
            if year < 100:
                year += 2000 if year < 50 else 1900
            try:
                date_obj = datetime(year, int(m), int(d))
                date_str = date_obj.strftime('%Y-%m-%d')
                break
            except ValueError:
                continue
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')
    result['date'] = date_str

    result['category'] = 'Other' if result['transaction_type'] == 'credit' else auto_detect_category(result['merchant'])
    return result


def get_settings():
    """Retrieve settings (budget, income) from database."""
    conn = get_db_connection()
    cursor = conn.execute('SELECT key, value FROM settings')
    settings = {row['key']: row['value'] for row in cursor.fetchall()}
    conn.close()
    return settings


def get_current_balance():
    """Current balance = starting_balance + total_credits - total_debits (all time) for this user."""
    settings = get_settings()
    starting = float(settings.get('starting_balance', 0))
    conn = get_db_connection()
    user_id = get_current_user_id()
    cursor = conn.execute(
        '''
        SELECT transaction_type, COALESCE(SUM(amount), 0) as total
        FROM transactions
        WHERE user_id = ?
        GROUP BY transaction_type
        ''',
        (user_id,),
    )
    credits = debits = 0
    for row in cursor.fetchall():
        if row['transaction_type'] == 'credit':
            credits = row['total']
        else:
            debits = row['total']
    conn.close()
    return round(starting + credits - debits, 2)


def get_monthly_credits():
    """Total credits (money in) for current month for this user."""
    conn = get_db_connection()
    now = datetime.now()
    start_of_month = now.replace(day=1).strftime('%Y-%m-%d')
    end_of_month = (now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    end_str = end_of_month.strftime('%Y-%m-%d')
    user_id = get_current_user_id()
    cursor = conn.execute(
        '''
        SELECT COALESCE(SUM(amount), 0) as total
        FROM transactions
        WHERE date >= ?
          AND date <= ?
          AND transaction_type = 'credit'
          AND user_id = ?
        ''',
        (start_of_month, end_str, user_id),
    )
    total = cursor.fetchone()['total']
    conn.close()
    return float(total)


def get_daily_spending():
    """Daily spending (debits only) for current month for this user."""
    conn = get_db_connection()
    now = datetime.now()
    start_of_month = now.replace(day=1).strftime('%Y-%m-%d')
    user_id = get_current_user_id()
    cursor = conn.execute(
        '''
        SELECT date, SUM(amount) as total
        FROM transactions
        WHERE date >= ?
          AND COALESCE(transaction_type, 'debit') = 'debit'
          AND user_id = ?
        GROUP BY date
        ORDER BY date
        ''',
        (start_of_month, user_id),
    )
    daily = [{'date': row['date'], 'total': row['total']} for row in cursor.fetchall()]
    conn.close()
    return daily


def get_monthly_spending():
    """Total debits (expenses) for current month for this user."""
    conn = get_db_connection()
    now = datetime.now()
    start_of_month = now.replace(day=1).strftime('%Y-%m-%d')
    end_of_month = (now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    end_str = end_of_month.strftime('%Y-%m-%d')
    user_id = get_current_user_id()
    cursor = conn.execute(
        '''
        SELECT COALESCE(SUM(amount), 0) as total
        FROM transactions
        WHERE date >= ?
          AND date <= ?
          AND COALESCE(transaction_type, 'debit') = 'debit'
          AND user_id = ?
        ''',
        (start_of_month, end_str, user_id),
    )
    total = cursor.fetchone()['total']
    conn.close()
    return float(total)


def _get_category_totals_for_user(user_id: int) -> dict:
    """
    Helper: spending by category (debits only) for current month for a specific user.
    Returns dict: {category: total_amount}.
    """
    conn = get_db_connection()
    now = datetime.now()
    start_of_month = now.replace(day=1).strftime('%Y-%m-%d')
    cursor = conn.execute(
        '''
        SELECT category, SUM(amount) as total
        FROM transactions
        WHERE date >= ?
          AND COALESCE(transaction_type, 'debit') = 'debit'
          AND user_id = ?
        GROUP BY category
        ''',
        (start_of_month, user_id),
    )
    totals = {row['category']: row['total'] for row in cursor.fetchall()}
    conn.close()
    return totals


def get_recent_transactions(user_id: int, limit: int = 10) -> str:
    """
    Retrieve recent transactions for a user and format as readable lines.
    Example line: "2026-03-01 - ZOMATO - ₹450 (Food)".
    """
    conn = get_db_connection()
    cursor = conn.execute(
        '''
        SELECT merchant, amount, category, date
        FROM transactions
        WHERE user_id = ?
        ORDER BY date DESC, id DESC
        LIMIT ?
        ''',
        (user_id, limit),
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return "No recent transactions available."

    lines = []
    for row in rows:
        date_str = row['date']
        merchant = row['merchant']
        amount = row['amount']
        category = row['category']
        lines.append(f"{date_str} - {merchant} - ₹{amount:.0f} ({category})")

    return "\n".join(lines)


def get_spending_by_category(user_id: int) -> str:
    """
    Human-readable category-wise spending for current month for a user.
    Example:
    Food: ₹4500
    Travel: ₹2000
    """
    totals = _get_category_totals_for_user(user_id)
    if not totals:
        return "No spending recorded this month."

    lines = []
    for category, total in totals.items():
        lines.append(f"{category}: ₹{float(total):.0f}")
    return "\n".join(lines)


def get_total_spent_this_month(user_id: int) -> str:
    """
    Human-readable total amount spent (debits) this month for a user.
    """
    conn = get_db_connection()
    now = datetime.now()
    start_of_month = now.replace(day=1).strftime('%Y-%m-%d')
    end_of_month = (now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    end_str = end_of_month.strftime('%Y-%m-%d')
    cursor = conn.execute(
        '''
        SELECT COALESCE(SUM(amount), 0) as total
        FROM transactions
        WHERE date >= ?
          AND date <= ?
          AND COALESCE(transaction_type, 'debit') = 'debit'
          AND user_id = ?
        ''',
        (start_of_month, end_str, user_id),
    )
    row = cursor.fetchone()
    conn.close()
    total = float(row['total'] if row else 0)
    return f"You have spent ₹{total:.0f} this month."


def get_top_merchant(user_id: int):
    """
    Return info about the top spending merchant for the current month for a user.
    Returns dict with keys: merchant, total, count, description; or None.
    """
    conn = get_db_connection()
    now = datetime.now()
    start_of_month = now.replace(day=1).strftime('%Y-%m-%d')
    cursor = conn.execute(
        '''
        SELECT merchant, COUNT(*) as count, SUM(amount) as total
        FROM transactions
        WHERE date >= ?
          AND COALESCE(transaction_type, 'debit') = 'debit'
          AND user_id = ?
        GROUP BY merchant
        ORDER BY total DESC
        LIMIT 1
        ''',
        (start_of_month, user_id),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    total = float(row['total'] or 0)
    desc = f"Top merchant this month is {row['merchant']} with {int(row['count'])} transactions totaling ₹{total:.0f}."
    return {
        'merchant': row['merchant'],
        'count': int(row['count']),
        'total': total,
        'description': desc,
    }


def get_largest_transaction(user_id: int):
    """
    Return the largest single debit transaction for a user.
    Returns dict with keys: amount, merchant, category, date, description; or None.
    """
    conn = get_db_connection()
    cursor = conn.execute(
        '''
        SELECT merchant, amount, category, date
        FROM transactions
        WHERE user_id = ?
          AND COALESCE(transaction_type, 'debit') = 'debit'
        ORDER BY amount DESC
        LIMIT 1
        ''',
        (user_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    amount = float(row['amount'])
    desc = f"Your largest transaction was ₹{amount:.0f} at {row['merchant']} on {row['date']} ({row['category']})."
    return {
        'amount': amount,
        'merchant': row['merchant'],
        'category': row['category'],
        'date': row['date'],
        'description': desc,
    }


def get_monthly_spending_chart(user_id: int) -> dict:
    """
    Return category-wise spending for current month as chart data.
    {
      "labels": ["Food", "Travel", "Shopping", "Bills", "Other"],
      "values": [4500, 2000, 3000, 1500, 0]
    }
    """
    # Use a fixed category ordering for consistent charts.
    categories = ['Food', 'Travel', 'Shopping', 'Bills', 'Other']
    totals = _get_category_totals_for_user(user_id)
    values = [float(totals.get(cat, 0) or 0) for cat in categories]
    return {
        'labels': categories,
        'values': values,
    }


def get_repeated_merchants():
    """Repeated debit merchants this month for this user."""
    conn = get_db_connection()
    now = datetime.now()
    start_of_month = now.replace(day=1).strftime('%Y-%m-%d')
    user_id = get_current_user_id()
    cursor = conn.execute(
        '''
        SELECT merchant, COUNT(*) as count, SUM(amount) as total
        FROM transactions
        WHERE date >= ?
          AND COALESCE(transaction_type, 'debit') = 'debit'
          AND user_id = ?
        GROUP BY merchant
        HAVING COUNT(*) > 1
        ORDER BY total DESC
        ''',
        (start_of_month, user_id),
    )
    repeated = [{'merchant': row['merchant'], 'count': row['count'], 'total': row['total']}
                for row in cursor.fetchall()]
    conn.close()
    return repeated


def predict_end_of_month_expense():
    """
    Predict end-of-month expense based on current spending trend.
    Uses: (current_spent / days_elapsed) * total_days_in_month
    """
    monthly_spent = get_monthly_spending()
    now = datetime.now()
    days_elapsed = now.day
    if days_elapsed == 0:
        days_elapsed = 1
    days_in_month = (now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    total_days = days_in_month.day
    if days_elapsed > 0:
        daily_avg = monthly_spent / days_elapsed
        predicted = daily_avg * total_days
    else:
        predicted = monthly_spent
    return round(predicted, 2)


def get_spending_personality(income_spent_percent: float) -> str:
    """
    Determine spending personality based on percentage of income spent.
    - Saver: < 50%
    - Balanced: 50% - 75%
    - Impulsive: > 75%
    """
    if income_spent_percent < 50:
        return 'Saver'
    elif income_spent_percent <= 75:
        return 'Balanced'
    else:
        return 'Impulsive'


def get_category_breakdown():
    """Spending by category (debits only) for current month for this user."""
    conn = get_db_connection()
    now = datetime.now()
    start_of_month = now.replace(day=1).strftime('%Y-%m-%d')
    user_id = get_current_user_id()
    cursor = conn.execute(
        '''
        SELECT category, SUM(amount) as total
        FROM transactions
        WHERE date >= ?
          AND COALESCE(transaction_type, 'debit') = 'debit'
          AND user_id = ?
        GROUP BY category
        ''',
        (start_of_month, user_id),
    )
    breakdown = {row['category']: row['total'] for row in cursor.fetchall()}
    conn.close()
    for cat in ['Food', 'Travel', 'Shopping', 'Bills', 'Other']:
        if cat not in breakdown:
            breakdown[cat] = 0
    return breakdown


def get_fraud_waste_alerts(threshold: float = 100.0) -> list:
    """Small repeated debit transactions (potential fraud/leaks) for this user."""
    conn = get_db_connection()
    now = datetime.now()
    start_of_month = now.replace(day=1).strftime('%Y-%m-%d')
    user_id = get_current_user_id()
    cursor = conn.execute(
        '''
        SELECT merchant, COUNT(*) as count, SUM(amount) as total
        FROM transactions
        WHERE date >= ?
          AND amount < ?
          AND COALESCE(transaction_type, 'debit') = 'debit'
          AND user_id = ?
        GROUP BY merchant
        HAVING COUNT(*) >= 2
        ORDER BY total DESC
        ''',
        (start_of_month, threshold, user_id),
    )
    alerts = [{
        'merchant': row['merchant'],
        'count': row['count'],
        'total': row['total'],
        'avg_per_txn': round(row['total'] / row['count'], 2)
    } for row in cursor.fetchall()]
    conn.close()
    return alerts


def get_financial_insights(user_id: int, monthly_budget: float, monthly_income: float, monthly_expenses: float) -> list:
    """
    Generate simple textual financial insights for the user.
    Examples:
    - highest spending category
    - overspending vs budget
    - savings potential
    - repeated merchants
    """
    insights: list[str] = []

    # Highest spending category
    category_totals = _get_category_totals_for_user(user_id)
    if category_totals:
        highest_category, highest_total = max(category_totals.items(), key=lambda item: item[1])
        if highest_total and highest_total > 0:
            insights.append(
                f"You spent most on {highest_category} this month (₹{float(highest_total):.0f}). "
                f"Reducing {highest_category.lower()} spending by ₹200 per day could save about ₹6000 monthly."
            )

    # Overspending vs budget
    if monthly_budget and monthly_budget > 0:
        if monthly_expenses > monthly_budget:
            diff = monthly_expenses - monthly_budget
            insights.append(
                f"You have exceeded your monthly budget by ₹{diff:.0f}. Consider tightening discretionary spending."
            )
        else:
            remaining = monthly_budget - monthly_expenses
            insights.append(
                f"You are within your monthly budget with about ₹{remaining:.0f} remaining."
            )

    # Savings potential
    if monthly_income and monthly_income > 0:
        potential_savings = monthly_income - monthly_expenses
        if potential_savings > 0:
            insights.append(
                f"Your potential savings this month are around ₹{potential_savings:.0f}. "
                f"Automating this amount to a savings or investment account can build wealth steadily."
            )
        else:
            insights.append(
                "Your expenses are at or above your income this month. Try to cut variable costs to create room for savings."
            )

    # Repeated merchants (possible leakage)
    repeated = get_repeated_merchants()
    for item in repeated:
        merchant = item['merchant']
        count = item['count']
        total = item['total']
        insights.append(
            f"You have {count} transactions at {merchant} this month totaling ₹{float(total):.0f}. "
            f"Review if all of these are necessary."
        )

    return insights


def get_budget_warning(total_spent: float, monthly_budget: float, warn_threshold: float = 0.8) -> dict:
    """
    Returns warning state when spending exceeds threshold (default 80%) of budget.
    """
    if monthly_budget <= 0:
        return {'active': False, 'percent': 0, 'message': ''}
    percent = (total_spent / monthly_budget) * 100
    active = percent >= (warn_threshold * 100)
    if percent >= 100:
        message = f'Budget exceeded by {percent - 100:.1f}%!'
    elif active:
        message = f'Warning: {percent:.1f}% of budget spent'
    else:
        message = ''
    return {'active': active, 'percent': round(percent, 1), 'message': message}


def get_financial_health_score() -> dict:
    settings = get_settings()
    monthly_budget = float(settings.get('monthly_budget', 0) or 0)
    monthly_income = float(settings.get('monthly_income', 0) or 0)
    monthly_spent = get_monthly_spending()
    current_balance = get_current_balance()

    def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
        return max(low, min(high, value))

    if monthly_budget > 0:
        usage_percent = (monthly_spent / monthly_budget) * 100
        budget_score = clamp(100 - usage_percent)
    else:
        budget_score = 50.0

    if monthly_income > 0:
        savings = monthly_income - monthly_spent
        savings_rate = (savings / monthly_income) * 100
        savings_score = clamp(savings_rate)
        balance_ratio = (current_balance / monthly_income) * 100
        balance_score = clamp(balance_ratio)
    else:
        savings_score = 50.0
        balance_score = 50.0

    total_score = 0.4 * budget_score + 0.3 * savings_score + 0.3 * balance_score
    score_int = int(round(clamp(total_score)))

    if score_int > 80:
        category = 'Excellent'
    elif score_int >= 50:
        category = 'Balanced'
    else:
        category = 'Risky'

    return {'score': score_int, 'category': category}


def parse_simulate_command(cmd: str) -> dict:
    """
    Parse budget simulation command like:
    - "reduce food by 100 per day"
    - "cut shopping by 500 per week"
    - "save 200 daily on travel"
    Returns: {category, reduction_per_day}
    """
    cmd = cmd.lower().strip()
    categories = ['food', 'travel', 'shopping', 'bills', 'other']
    category = None
    for cat in categories:
        if cat in cmd:
            category = cat.capitalize()
            break
    if not category:
        return None
    # Extract amount and period
    amount_match = re.search(r'(\d+)\s*(?:per|a)?\s*(?:day|daily|week|weekly|month|monthly)', cmd)
    if amount_match:
        amount = float(amount_match.group(1))
        m = amount_match.group(0)
        if 'week' in m:
            amount /= 7
        elif 'month' in m:
            amount /= 30
    else:
        amount_match = re.search(r'(?:by|save|cut|reduce)\s*(\d+)', cmd)
        if amount_match:
            amount = float(amount_match.group(1))
        else:
            amount_match = re.search(r'(\d+)', cmd)
            if not amount_match:
                return None
            amount = float(amount_match.group(1))
    return {'category': category, 'reduction_per_day': amount}


def simulate_savings(category: str, reduction_per_day: float) -> dict:
    """
    Calculate new savings if user reduces spending in a category.
    Returns projected monthly savings and new predicted totals.
    """
    category_breakdown = get_category_breakdown()
    current_spent = get_monthly_spending()
    predicted_eom = predict_end_of_month_expense()
    settings = get_settings()
    monthly_budget = float(settings.get('monthly_budget', 50000))
    
    current_category_spend = category_breakdown.get(category, 0)
    days_in_month = (datetime.now().replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    total_days = days_in_month.day
    
    # Monthly savings = reduction per day * 30 (full month projection)
    monthly_savings = reduction_per_day * total_days
    # New predicted EOM if user cuts spending from now
    new_predicted_total = max(0, predicted_eom - monthly_savings)
    new_remaining = monthly_budget - new_predicted_total
    
    return {
        'category': category,
        'reduction_per_day': reduction_per_day,
        'monthly_savings': round(monthly_savings, 2),
        'current_category_spend': current_category_spend,
        'new_predicted_total': round(new_predicted_total, 2),
        'new_budget_remaining': round(new_remaining, 2)
    }


def get_ai_insights():
    user_id = get_current_user_id()
    if not user_id:
        return []
    now = datetime.now()
    this_month_start = now.replace(day=1)
    if this_month_start.month == 1:
        last_month_year = this_month_start.year - 1
        last_month_month = 12
    else:
        last_month_year = this_month_start.year
        last_month_month = this_month_start.month - 1
    last_month_start = datetime(last_month_year, last_month_month, 1)
    last_month_end = this_month_start - timedelta(days=1)
    this_start_str = this_month_start.strftime('%Y-%m-%d')
    last_start_str = last_month_start.strftime('%Y-%m-%d')
    last_end_str = last_month_end.strftime('%Y-%m-%d')
    conn = get_db_connection()
    cursor = conn.execute(
        '''
        SELECT category, SUM(amount) as total
        FROM transactions
        WHERE date >= ?
          AND COALESCE(transaction_type, 'debit') = 'debit'
          AND user_id = ?
        GROUP BY category
        ''',
        (this_start_str, user_id),
    )
    this_by_category = {row['category']: row['total'] for row in cursor.fetchall()}
    cursor = conn.execute(
        '''
        SELECT category, SUM(amount) as total
        FROM transactions
        WHERE date >= ?
          AND date <= ?
          AND COALESCE(transaction_type, 'debit') = 'debit'
          AND user_id = ?
        GROUP BY category
        ''',
        (last_start_str, last_end_str, user_id),
    )
    last_by_category = {row['category']: row['total'] for row in cursor.fetchall()}
    insights = []
    all_categories = set(this_by_category.keys()) | set(last_by_category.keys())
    for category in all_categories:
        current_total = float(this_by_category.get(category, 0) or 0)
        last_total = float(last_by_category.get(category, 0) or 0)
        if last_total <= 0 or current_total <= 0:
            continue
        change = current_total - last_total
        if change <= 0:
            continue
        percent = (change / last_total) * 100
        if percent > 10:
            insights.append(f"You spent {percent:.1f}% more on {category} compared to last month.")
    total_this = float(sum(this_by_category.values()) if this_by_category else 0)
    total_last = float(sum(last_by_category.values()) if last_by_category else 0)
    biggest_category = None
    if this_by_category:
        biggest_category, biggest_total = max(this_by_category.items(), key=lambda item: item[1])
        if biggest_total and biggest_total > 0:
            insights.append(f"Your biggest expense this month is {biggest_category}.")
    elif total_this <= 0 and total_last > 0 and last_by_category:
        biggest_category, biggest_last_total = max(last_by_category.items(), key=lambda item: item[1])
        if biggest_last_total and biggest_last_total > 0:
            insights.append(f"You have no spending yet this month. Last month your biggest expense was {biggest_category}.")
    cursor = conn.execute(
        '''
        SELECT merchant, SUM(amount) as total
        FROM transactions
        WHERE date >= ?
          AND COALESCE(transaction_type, 'debit') = 'debit'
          AND user_id = ?
        GROUP BY merchant
        ORDER BY total DESC
        LIMIT 1
        ''',
        (this_start_str, user_id),
    )
    row = cursor.fetchone()
    if (not row or not row['merchant'] or not row['total'] or row['total'] <= 0) and total_this <= 0 and total_last > 0:
        cursor = conn.execute(
            '''
            SELECT merchant, SUM(amount) as total
            FROM transactions
            WHERE date >= ?
              AND date <= ?
              AND COALESCE(transaction_type, 'debit') = 'debit'
              AND user_id = ?
            GROUP BY merchant
            ORDER BY total DESC
            LIMIT 1
            ''',
            (last_start_str, last_end_str, user_id),
        )
        row = cursor.fetchone()
    conn.close()
    if row and row['merchant'] and row['total'] and row['total'] > 0:
        if total_this > 0:
            insights.append(f"Top spending merchant this month is {row['merchant']}.")
        else:
            insights.append(f"Top spending merchant last month was {row['merchant']}.")
    return insights


def generate_sql_from_question(user_message: str, user_id: int):
    """
    Very small, safe natural-language → SQL helper.
    Currently supports questions like:
      "How much did I spend on food last week?"
    Returns (sql, params, description) or (None, None, None) if not recognized.
    """
    text = (user_message or "").lower()

    if "how much" in text and "spend" in text and "last week" in text:
        # Try to detect a known category from text.
        known_categories = ['Food', 'Travel', 'Shopping', 'Bills', 'Other']
        detected_category = None
        for cat in known_categories:
            if cat.lower() in text:
                detected_category = cat
                break

        # If not an explicit category, fall back to Food as a common case.
        if not detected_category:
            for cat, keywords in CATEGORY_KEYWORDS.items():
                for kw in keywords:
                    if kw in text:
                        detected_category = cat
                        break
                if detected_category:
                    break
        if not detected_category:
            detected_category = 'Food'

        sql = '''
            SELECT COALESCE(SUM(amount), 0) as total
            FROM transactions
            WHERE COALESCE(transaction_type, 'debit') = 'debit'
              AND category = ?
              AND date >= date('now', '-7 days')
              AND user_id = ?
        '''
        params = (detected_category, user_id)
        desc = f"Total spent on {detected_category} in the last 7 days."
        return sql, params, desc

    return None, None, None


@app.route('/')
def index():
    """
    Entry point: landing page with site name and info; Login and Sign up in header.
    If the user is already logged in, show the dashboard instead.
    """
    if 'user_id' in session:
        return render_template('index.html')
    return render_template('landing.html', google_client_id=GOOGLE_CLIENT_ID)


@app.route('/login')
def login_page():
    """Login page: email + password (form posts to /auth/login)."""
    error = request.args.get('error')
    message = request.args.get('message')
    return render_template('login.html', error=error, message=message, google_client_id=GOOGLE_CLIENT_ID)


@app.route('/signup')
def signup_page():
    """Signup page: multi-step (email → OTP → set password) via same template."""
    return render_template('signup.html', error=None, message=None, google_client_id=GOOGLE_CLIENT_ID)


@app.route('/api/signup/send-otp', methods=['POST'])
def api_send_otp():
    """Send OTP to email for signup. Expects JSON: { email }."""
    data = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    if not email:
        return jsonify({'success': False, 'error': 'Email is required'}), 400
    conn = get_db_connection()
    cursor = conn.execute('SELECT id FROM users WHERE email = ?', (email,))
    if cursor.fetchone():
        conn.close()
        return jsonify({'success': False, 'error': 'An account with this email already exists. Please log in.'}), 400
    otp = generate_otp(6)
    expires_at = (datetime.now() + timedelta(minutes=OTP_LIFETIME_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        'INSERT OR REPLACE INTO signup_otps (email, otp, expires_at) VALUES (?, ?, ?)',
        (email, otp, expires_at),
    )
    conn.commit()
    conn.close()
    send_otp_email(email, otp)
    return jsonify({'success': True, 'message': 'Verification code sent to your email.'})


@app.route('/api/signup/verify-otp', methods=['POST'])
def api_verify_otp():
    """Verify OTP and allow user to set password. Expects JSON: { email, otp }."""
    data = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    otp = (data.get('otp') or '').strip()
    if not email or not otp:
        return jsonify({'success': False, 'error': 'Email and OTP are required'}), 400
    conn = get_db_connection()
    cursor = conn.execute(
        'SELECT otp, expires_at FROM signup_otps WHERE email = ?',
        (email,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return jsonify({'success': False, 'error': 'No verification request found for this email. Please request a new code.'}), 400
    if datetime.now().strftime('%Y-%m-%d %H:%M:%S') > row['expires_at']:
        return jsonify({'success': False, 'error': 'Verification code has expired. Please request a new one.'}), 400
    if row['otp'] != otp:
        return jsonify({'success': False, 'error': 'Invalid verification code.'}), 400
    session['signup_email_verified'] = email
    return jsonify({'success': True, 'message': 'Verified. Set your password below.'})


@app.route('/api/signup/set-password', methods=['POST'])
def api_set_password():
    """Create account with email (from verified session) and password. Expects JSON: { password }."""
    email = session.get('signup_email_verified')
    if not email:
        return jsonify({'success': False, 'error': 'Verification expired. Please start signup again.'}), 400
    data = request.get_json() or {}
    password = data.get('password') or ''
    if len(password) < 6:
        return jsonify({'success': False, 'error': 'Password must be at least 6 characters.'}), 400
    password_hash = generate_password_hash(password)
    conn = get_db_connection()
    try:
        conn.execute(
            'INSERT INTO users (email, password_hash) VALUES (?, ?)',
            (email, password_hash),
        )
        conn.execute('DELETE FROM signup_otps WHERE email = ?', (email,))
        conn.commit()
        row_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'success': False, 'error': 'An account with this email already exists.'}), 400
    conn.close()
    session.pop('signup_email_verified', None)
    session['user_id'] = row_id
    session['user_email'] = email
    return jsonify({'success': True, 'redirect': url_for('index')})


@app.route('/api/receive_sms', methods=['POST'])
@login_required_json
def receive_sms():
    """
    API endpoint to receive SMS-style transaction messages.
    Expects JSON: {"message": "INR 450 spent on ZOMATO via UPI on 03-02-26. Bal: 5230"}
    Parses the message, stores in DB, returns extracted data.
    """
    data = request.get_json()
    if not data or 'message' not in data:
        return jsonify({'error': 'Missing "message" in JSON body'}), 400
    
    message = data['message']
    parsed = parse_sms(message)
    
    if parsed is None:
        return jsonify({'error': 'Could not parse transaction from message'}), 400
    
    txn_type = parsed.get('transaction_type', 'debit')
    conn = get_db_connection()
    user_id = get_current_user_id()
    conn.execute('''
        INSERT INTO transactions (amount, merchant, category, date, mode, transaction_type, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (parsed['amount'], parsed['merchant'], parsed['category'],
          parsed['date'], parsed['mode'], txn_type, user_id))
    conn.commit()
    row_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    conn.close()
    parsed['id'] = row_id
    parsed['transaction_type'] = txn_type
    return jsonify({'success': True, 'transaction': parsed})


@app.route('/api/parse_only', methods=['POST'])
@login_required_json
def parse_only():
    """Parse SMS without storing - for preview before submit."""
    data = request.get_json()
    if not data or 'message' not in data:
        return jsonify({'error': 'Missing "message" in JSON body'}), 400
    
    parsed = parse_sms(data['message'])
    if parsed is None:
        return jsonify({'error': 'Could not parse transaction'}), 400
    
    return jsonify({'transaction': parsed})


@app.route('/api/dashboard')
@login_required_json
def dashboard():
    """Return all dashboard data: totals, categories, predictions, warnings, fraud alerts."""
    settings = get_settings()
    monthly_budget = float(settings.get('monthly_budget', 50000))
    monthly_income = float(settings.get('monthly_income', 60000))
    
    total_spent = get_monthly_spending()
    category_breakdown = get_category_breakdown()
    daily_spending = get_daily_spending()
    repeated = get_repeated_merchants()
    predicted_eom = predict_end_of_month_expense()
    fraud_waste = get_fraud_waste_alerts(threshold=100)
    budget_warning = get_budget_warning(total_spent, monthly_budget, warn_threshold=0.8)
    
    income_percent = (total_spent / monthly_income * 100) if monthly_income > 0 else 0
    personality = get_spending_personality(income_percent)
    
    current_balance = get_current_balance()
    monthly_credits = get_monthly_credits()
    financial_health = get_financial_health_score()
    return jsonify({
        'total_expenses': total_spent,
        'monthly_credits': monthly_credits,
        'current_balance': current_balance,
        'monthly_budget': monthly_budget,
        'monthly_income': monthly_income,
        'starting_balance': settings.get('starting_balance', '0'),
        'budget_remaining': monthly_budget - total_spent,
        'category_breakdown': category_breakdown,
        'daily_spending': daily_spending,
        'repeated_merchants': repeated,
        'predicted_eom_expense': predicted_eom,
        'spending_personality': personality,
        'income_spent_percent': round(income_percent, 1),
        'budget_warning': budget_warning,
        'fraud_waste_alerts': fraud_waste,
        'financial_health': financial_health,
        'ai_insights': get_ai_insights(),
        'fixed_deposits': get_fixed_deposits()
    })


@app.route('/api/transactions')
@login_required_json
def transactions():
    """Return list of recent transactions with type (debit/credit)."""
    conn = get_db_connection()
    cursor = conn.execute('''
        SELECT id, amount, merchant, category, date, mode,
               COALESCE(transaction_type, 'debit') as transaction_type
        FROM transactions
        WHERE user_id = ?
        ORDER BY date DESC, id DESC
        LIMIT 100
    ''', (get_current_user_id(),))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'transactions': rows})


@app.route('/api/simulate', methods=['POST'])
@login_required_json
def simulate():
    """
    Budget simulator: parses natural language like "reduce food by 100 per day"
    and returns projected new savings.
    """
    data = request.get_json()
    if not data or 'command' not in data:
        return jsonify({'error': 'Missing "command" in JSON body'}), 400
    
    parsed = parse_simulate_command(data['command'])
    if parsed is None:
        return jsonify({
            'error': 'Could not parse. Try: "reduce food by 100 per day" or "cut shopping by 500 per week"'
        }), 400
    
    result = simulate_savings(parsed['category'], parsed['reduction_per_day'])
    return jsonify({'success': True, 'simulation': result})


@app.route('/api/reset_all', methods=['POST'])
@login_required_json
def reset_all():
    """
    Reset everything: delete all transactions, all fixed deposits,
    and reset settings to defaults (0 for starting_balance, 50000 budget, 60000 income).
    """
    conn = get_db_connection()
    user_id = get_current_user_id()
    # Delete only this user's data
    conn.execute('DELETE FROM transactions WHERE user_id = ?', (user_id,))
    conn.execute('DELETE FROM fixed_deposits WHERE user_id = ?', (user_id,))
    conn.execute('''
        INSERT OR REPLACE INTO settings (key, value) VALUES
        ('monthly_budget', '0'),
        ('monthly_income', '0'),
        ('starting_balance', '0')
    ''')
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'All data reset to zero'})


@app.route('/api/settings', methods=['GET', 'POST'])
@login_required_json
def settings_api():
    """Get or update global settings (budget, income, starting balance)."""
    if request.method == 'GET':
        return jsonify(get_settings())

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid data'}), 400

    conn = get_db_connection()
    for key in ['monthly_budget', 'monthly_income', 'starting_balance']:
        if key in data:
            conn.execute(
                'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
                (key, str(data[key])),
            )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'settings': get_settings()})


def get_fixed_deposits():
    """List all FDs with maturity amount (simple interest: P * (1 + r*t/100)) for this user."""
    conn = get_db_connection()
    user_id = get_current_user_id()
    cursor = conn.execute(
        '''
        SELECT id, amount, interest_rate, tenure_months, start_date, created_at
        FROM fixed_deposits
        WHERE user_id = ?
        ORDER BY start_date DESC
        ''',
        (user_id,),
    )
    fds = []
    for row in cursor.fetchall():
        p = row['amount']
        r = row['interest_rate']
        t = row['tenure_months'] / 12.0
        maturity = round(p * (1 + r * t / 100), 2)
        fds.append({
            'id': row['id'],
            'amount': p,
            'interest_rate': r,
            'tenure_months': row['tenure_months'],
            'start_date': row['start_date'],
            'maturity_amount': maturity,
            'interest_earned': round(maturity - p, 2)
        })
    conn.close()
    return fds


@app.route('/api/fixed_deposits', methods=['GET', 'POST'])
@login_required_json
def fixed_deposits_api():
    """Get all FDs or add a new one. POST: amount, interest_rate, tenure_months, start_date."""
    if request.method == 'GET':
        return jsonify({'fixed_deposits': get_fixed_deposits()})
    data = request.get_json()
    if not data or 'amount' not in data or 'interest_rate' not in data:
        return jsonify({'error': 'amount and interest_rate required'}), 400
    amount = float(data['amount'])
    interest_rate = float(data['interest_rate'])
    tenure_months = int(data.get('tenure_months', 12))
    start_date = data.get('start_date') or datetime.now().strftime('%Y-%m-%d')
    user_id = get_current_user_id()
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO fixed_deposits (amount, interest_rate, tenure_months, start_date, user_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (amount, interest_rate, tenure_months, start_date, user_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'fixed_deposits': get_fixed_deposits()})


@app.route('/auth/login', methods=['POST'])
def auth_login():
    """Handle login form submissions from the login page."""
    email = (request.form.get('email') or '').strip().lower()
    password = request.form.get('password') or ''

    if not email or not password:
        return render_template(
            'login.html',
            error='Please enter both email and password.',
            message=None,
        )

    conn = get_db_connection()
    cursor = conn.execute(
        'SELECT id, password_hash FROM users WHERE email = ?',
        (email,),
    )
    row = cursor.fetchone()
    conn.close()

    if not row or not check_password_hash(row['password_hash'], password):
        return render_template(
            'login.html',
            error='Incorrect email or password.',
            message=None,
        )

    session['user_id'] = row['id']
    session['user_email'] = email
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    """Log the user out and return to the landing page."""
    session.clear()
    return redirect(url_for('index'))

def verify_google_id_token(id_token: str) -> dict | None:
    """
    Verify Google ID token by calling tokeninfo endpoint.
    Ensure audience matches configured GOOGLE_CLIENT_ID.
    Returns token info dict with 'email' on success, else None.
    """
    try:
        url = f"https://oauth2.googleapis.com/tokeninfo?id_token={id_token}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        aud = data.get('aud')
        email = data.get('email')
        if not aud or not email:
            return None
        if GOOGLE_CLIENT_ID and aud != GOOGLE_CLIENT_ID:
            return None
        return data
    except Exception:
        return None

@app.route('/auth/google', methods=['POST'])
def auth_google():
    """
    Google sign-in: accepts JSON { id_token }, verifies with Google,
    creates or finds user by email, and starts session.
    """
    payload = request.get_json() or {}
    id_token = (payload.get('id_token') or '').strip()
    if not id_token:
        return jsonify({'success': False, 'error': 'Missing id_token'}), 400
    info = verify_google_id_token(id_token)
    if not info:
        return jsonify({'success': False, 'error': 'Invalid Google token'}), 401
    email = info.get('email').lower()
    conn = get_db_connection()
    cursor = conn.execute('SELECT id FROM users WHERE email = ?', (email,))
    row = cursor.fetchone()
    if row:
        user_id = row['id']
    else:
        temp_pw = generate_temp_password(12)
        pw_hash = generate_password_hash(temp_pw)
        conn.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', (email, pw_hash))
        conn.commit()
        user_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    conn.close()
    session['user_id'] = user_id
    session['user_email'] = email
    return jsonify({'success': True, 'redirect': url_for('index')})

# ===== AI FINANCE CHATBOT =====
def get_finance_ai_response(user_message: str, user_data: dict, user_id: int = None, category_breakdown: str = "", insights_list: list = None) -> str:
    """
    Generate AI finance advice based on user query and their financial data.
    Uses smart pattern matching for financial queries with enhanced category and insight handling.
    """
    msg_lower = user_message.lower()
    if insights_list is None:
        insights_list = []
    
    # Extract user's financial data
    balance = user_data.get('balance', 0)
    monthly_expenses = user_data.get('monthly_expenses', 0)
    monthly_income = user_data.get('monthly_income', 0)
    budget = user_data.get('budget', 0)
    health_score = user_data.get('health_score', 0)
    
    # Spending percentage
    if monthly_income > 0:
        spending_ratio = (monthly_expenses / monthly_income) * 100
    else:
        spending_ratio = 0
    
    # Category queries with detailed response
    if any(word in msg_lower for word in ['category', 'categories', 'category-wise', 'category wise', 'spending by category', 'where did i spend', 'breakdown', 'by category']):
        if category_breakdown and category_breakdown != "No spending recorded this month.":
            return f"📊 Your Category-Wise Spending:\n\n{category_breakdown}\n\n💡 Focus on your highest spending category and look for ways to reduce it by 5-10%."
        else:
            return "No spending data recorded yet. Start adding transactions to see detailed category-wise breakdowns."
    
    # Insights queries with detailed response
    if any(word in msg_lower for word in ['insight', 'insights', 'smart insight', 'analysis', 'smart advice', 'smart analysis', 'recommendations', 'financial analysis']):
        if insights_list:
            insights_formatted = "\n\n".join([f"💡 {insight}" for insight in insights_list])
            return f"📈 Your Smart Financial Insights:\n\n{insights_formatted}"
        else:
            return "Add more transaction data to unlock personalized financial insights! The more you track, the better insights I can generate."
    
    # Budget queries
    if any(word in msg_lower for word in ['budget', 'spending limit', 'overspend']):
        if monthly_expenses > budget and budget > 0:
            overspend = monthly_expenses - budget
            return f"⚠️ Your monthly spending (₹{monthly_expenses:.0f}) exceeds your budget (₹{budget:.0f}) by ₹{overspend:.0f}. Consider reducing discretionary spending on non-essentials."
        elif budget > 0:
            remaining = budget - monthly_expenses
            return f"✅ Great! You have ₹{remaining:.0f} remaining in your ₹{budget:.0f} budget. Keep up the discipline!"
        else:
            return "📊 Set a monthly budget in Settings to track your spending against targets."
    
    # Savings queries
    if any(word in msg_lower for word in ['save', 'savings', 'accumulate', 'invest']):
        if monthly_income > 0:
            monthly_savings = monthly_income - monthly_expenses
            if monthly_savings > 0:
                return f"💰 Your potential monthly savings: ₹{monthly_savings:.0f} ({(monthly_savings/monthly_income)*100:.1f}% of income). Automate this to a savings account for steady wealth building."
            else:
                return "📍 Your expenses equal or exceed your income. Cut non-essentials first, then set up automatic savings once you have room."
        else:
            return "📝 Add your income in Settings to calculate savings potential."
    
    # Balance queries
    if any(word in msg_lower for word in ['balance', 'account', 'how much money', 'net worth']):
        emergency_fund = monthly_expenses * 3
        return f"💳 Your current balance: ₹{balance:.0f}. Emergency fund target: ₹{emergency_fund:.0f}-₹{monthly_expenses * 6:.0f} (3-6 months of expenses)."
    
    # Investment/FD queries
    if any(word in msg_lower for word in ['invest', 'fd', 'fixed deposit', 'interest', 'returns']):
        return "🏦 Fixed Deposits offer 6-8% annual returns safely. Once you have surplus, ladder your FDs for better liquidity. Use your savings potential to start investing consistently."
    
    # Health score queries
    if any(word in msg_lower for word in ['health', 'score', 'financial health']):
        if health_score >= 80:
            return f"🌟 Excellent! Financial health score: {health_score}/100. You're managing money wisely."
        elif health_score >= 60:
            return f"📈 Good score: {health_score}/100. Reduce expenses slightly to boost it to 80+."
        else:
            return f"⚠️ Score: {health_score}/100. Focus on reducing spending and increasing savings."
    
    # Income queries
    if any(word in msg_lower for word in ['income', 'earning', 'salary']):
        if monthly_income > 0:
            return f"💼 Monthly income: ₹{monthly_income:.0f}. Spending ratio: {spending_ratio:.1f}% (target: below 80%)."
        else:
            return "📝 Add your monthly income in Settings to unlock personalized analysis."
    
    # Specific category advice
    if any(word in msg_lower for word in ['food', 'restaurant', 'eat', 'dining', 'groceries']):
        return "🍽️ Food is often the easiest category to optimize. Meal plan weekly, cook at home, limit eating out to 1-2x/month. Potential savings: 30-40%."
    
    if any(word in msg_lower for word in ['travel', 'transport', 'taxi', 'ride', 'commute', 'fuel']):
        return "🚗 Use public transport for regular commute, carpool when possible, plan trips efficiently. Potential savings: 20-40%."
    
    if any(word in msg_lower for word in ['shopping', 'clothes', 'retail', 'purchase', 'amazon']):
        return "🛍️ Use the 48-hour rule before purchases, compare prices across sites, use cashback apps. Avoid impulse buying - it costs significantly."
    
    # Bill/subscription queries
    if any(word in msg_lower for word in ['bill', 'subscription', 'recurring', 'electricity', 'netflix', 'gym']):
        return "📱 Review ALL subscriptions monthly. Most people waste ₹2,000-5,000/month on unused ones. Cancel what you don't actively use."
    
    # General financial advice
    if any(word in msg_lower for word in ['advice', 'help', 'tip', 'suggest', 'how']):
        return "💡 Top tips: Track every transaction → Set budgets → Build emergency fund → Review monthly → Automate savings → Cut waste → Invest surplus.\n\nWhat specific area would you like help with?"
    
    # Default response
    return "👋 Ask me about:\n• Categories - Which categories am I spending most on?\n• Insights - What are my smart insights?\n• Budget - Am I overspending?\n• Savings - What's my savings potential?\n• Health - How's my financial health?"

@app.route('/api/chat', methods=['POST'])
def chat():
    """Chat endpoint for AI finance chatbot."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        payload = request.get_json() or {}
        user_message = (payload.get('message') or '').strip()

        if not user_message:
            return jsonify({'error': 'Empty message'}), 400

        user_id = session['user_id']

        # Initialize and update conversational memory
        if user_id not in chat_memory:
            chat_memory[user_id] = []
        chat_memory[user_id].append({
            "role": "user",
            "content": user_message,
        })
        chat_memory[user_id] = chat_memory[user_id][-10:]
        conversation_context = "\n".join(
            f"{m['role']}: {m['content']}" for m in chat_memory[user_id]
        )

        conn = get_db_connection()

        # Get user's financial settings
        cursor = conn.execute('SELECT value FROM settings WHERE key = ?', ('starting_balance',))
        row = cursor.fetchone()
        starting_balance = float(row['value']) if row else 0

        cursor = conn.execute('SELECT value FROM settings WHERE key = ?', ('monthly_budget',))
        row = cursor.fetchone()
        monthly_budget = float(row['value']) if row else 0

        cursor = conn.execute('SELECT value FROM settings WHERE key = ?', ('monthly_income',))
        row = cursor.fetchone()
        monthly_income = float(row['value']) if row else 0

        # Calculate current balance using transaction_type column
        cursor = conn.execute(
            '''
            SELECT COALESCE(
                SUM(
                    CASE
                        WHEN COALESCE(transaction_type, 'debit') = 'debit' THEN -amount
                        WHEN transaction_type = 'credit' THEN amount
                        ELSE 0
                    END
                ),
                0
            ) as balance
            FROM transactions
            WHERE user_id = ?
            ''',
            (user_id,),
        )
        balance_row = cursor.fetchone()
        current_balance = (balance_row['balance'] if balance_row else 0) + starting_balance

        # Calculate monthly expenses
        cursor = conn.execute(
            '''
            SELECT COALESCE(SUM(amount), 0) as expenses
            FROM transactions
            WHERE user_id = ?
              AND COALESCE(transaction_type, 'debit') = 'debit'
              AND strftime("%Y-%m", date) = strftime("%Y-%m", "now")
            ''',
            (user_id,),
        )
        expense_row = cursor.fetchone()
        monthly_expenses = expense_row['expenses'] if expense_row else 0

        # Calculate health score (simple: based on spending ratio)
        if monthly_income > 0:
            spending_ratio = (monthly_expenses / monthly_income) * 100
            if spending_ratio < 50:
                health_score = 90
            elif spending_ratio < 70:
                health_score = 75
            elif spending_ratio < 85:
                health_score = 60
            else:
                health_score = 40
        else:
            health_score = 50

        conn.close()

        # Prepare user data
        user_data = {
            'balance': current_balance,
            'monthly_expenses': monthly_expenses,
            'monthly_income': monthly_income,
            'budget': monthly_budget,
            'health_score': health_score,
        }

        # Tool-style handlers for specific factual queries
        lower_msg = user_message.lower()

        # Category-wise spending queries
        category_keywords = ['category', 'categories', 'category-wise', 'category wise', 'spending by category', 'where did i spend', 'breakdown', 'by category', 'spending breakdown', 'expense breakdown', 'spending categories']
        if any(word in lower_msg for word in category_keywords):
            category_data = get_spending_by_category(user_id)
            if category_data and category_data != "No spending recorded this month.":
                ai_response = f"📊 Your category-wise spending breakdown:\n\n{category_data}\n\nFocus on reducing categories with highest spending to improve your budget."
            else:
                ai_response = "No spending data recorded yet. Start adding transactions to see category breakdowns."
            chat_memory[user_id].append({"role": "assistant", "content": ai_response})
            chat_memory[user_id] = chat_memory[user_id][-10:]
            return jsonify({
                'success': True,
                'message': ai_response,
                'timestamp': datetime.now().isoformat(),
            })

        # Smart insights queries - more robust matching
        insight_keywords = ['insight', 'insights', 'smart insight', 'analysis', 'smart advice', 'smart analysis', 'recommendations', 'financial analysis', 'advise me', 'suggestions', 'financial advice', 'smart suggestions', 'give me insights', 'show insights']
        if any(keyword in lower_msg for keyword in insight_keywords) or ('smart' in lower_msg and any(word in lower_msg for word in ['insight', 'analysis', 'advice', 'suggestions'])):
            insights = get_financial_insights(user_id, monthly_budget, monthly_income, monthly_expenses)
            if insights:
                insights_formatted = "\n\n".join([f"💡 {insight}" for insight in insights])
                ai_response = f"📈 Your Smart Financial Insights:\n\n{insights_formatted}"
            else:
                ai_response = "Add more transaction data to get personalized financial insights!"
            chat_memory[user_id].append({"role": "assistant", "content": ai_response})
            chat_memory[user_id] = chat_memory[user_id][-10:]
            return jsonify({
                'success': True,
                'message': ai_response,
                'timestamp': datetime.now().isoformat(),
            })

        # Largest transaction
        if "largest transaction" in lower_msg or "biggest transaction" in lower_msg:
            largest = get_largest_transaction(user_id)
            if largest:
                ai_response = largest['description']
            else:
                ai_response = "I could not find any transactions yet."

            chat_memory[user_id].append({"role": "assistant", "content": ai_response})
            chat_memory[user_id] = chat_memory[user_id][-10:]
            return jsonify({
                'success': True,
                'message': ai_response,
                'timestamp': datetime.now().isoformat(),
            })

        # Top merchant
        if "top merchant" in lower_msg or "top merchants" in lower_msg:
            top_info = get_top_merchant(user_id)
            if top_info:
                ai_response = top_info['description']
            else:
                ai_response = "I could not identify a top merchant yet."

            chat_memory[user_id].append({"role": "assistant", "content": ai_response})
            chat_memory[user_id] = chat_memory[user_id][-10:]
            return jsonify({
                'success': True,
                'message': ai_response,
                'timestamp': datetime.now().isoformat(),
            })

        # Total spent this month
        if ("how much" in lower_msg or "total spent" in lower_msg or "spend this month" in lower_msg) and "month" in lower_msg:
            ai_response = get_total_spent_this_month(user_id)
            chat_memory[user_id].append({"role": "assistant", "content": ai_response})
            chat_memory[user_id] = chat_memory[user_id][-10:]
            return jsonify({
                'success': True,
                'message': ai_response,
                'timestamp': datetime.now().isoformat(),
            })

        # Natural language → SQL (limited) for questions like "How much did I spend on food last week?"
        sql, params, sql_desc = generate_sql_from_question(user_message, user_id)
        if sql and params:
            conn = get_db_connection()
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            conn.close()
            total = float(row['total'] if row else 0)
            ai_response = f"{sql_desc} Answer: ₹{total:.0f}."
            chat_memory[user_id].append({"role": "assistant", "content": ai_response})
            chat_memory[user_id] = chat_memory[user_id][-10:]
            return jsonify({
                'success': True,
                'message': ai_response,
                'timestamp': datetime.now().isoformat(),
            })

        # Generate additional financial context
        recent_transactions = get_recent_transactions(user_id, limit=10)
        category_breakdown = get_spending_by_category(user_id)
        insights = get_financial_insights(user_id, monthly_budget, monthly_income, monthly_expenses)
        insights_text = "\n".join(f"- {txt}" for txt in insights) if insights else "No additional insights."

        ai_response = None

        # For now, always use fallback to ensure it works
        insights = get_financial_insights(user_id, monthly_budget, monthly_income, monthly_expenses)
        ai_response = get_finance_ai_response(user_message, user_data, user_id, category_breakdown, insights)

        # if GENAI_MODEL is not None:
        #     try:
        #         response = GENAI_MODEL.generate_content(prompt)
        #         ai_response = response.text.strip()
        #     except Exception as e:
        #         print("[AI ERROR]", e)

        # if not ai_response:
        #     # Fallback to pattern matching if AI unavailable
        #     insights = get_financial_insights(user_id, monthly_budget, monthly_income, monthly_expenses)
        #     ai_response = get_finance_ai_response(user_message, user_data, user_id, category_breakdown, insights)

        chat_memory[user_id].append({"role": "assistant", "content": ai_response})
        chat_memory[user_id] = chat_memory[user_id][-10:]

        return jsonify({
            'success': True,
            'message': ai_response,
            'timestamp': datetime.now().isoformat(),
        })

    except Exception as e:
        # Final safety net so the server never crashes because of chatbot errors.
        print(f"[CHAT ERROR] Unexpected error in /api/chat: {e}")
        fallback = "I couldn't generate advice right now, but your financial data looks healthy."
        uid = session.get('user_id')
        if uid:
            if uid not in chat_memory:
                chat_memory[uid] = []
            chat_memory[uid].append({"role": "assistant", "content": fallback})
            chat_memory[uid] = chat_memory[uid][-10:]
        return jsonify({
            'success': True,
            'message': fallback,
            'timestamp': datetime.now().isoformat(),
        })

if __name__ == '__main__':
    init_db()
    print("Solvency - Budget Tracker running at http://127.0.0.1:5000")
    # Bind to all interfaces so it works in embedded IDE browsers too.
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
@app.route('/ping')
def ping():
    return {"status": "ok"}, 200
