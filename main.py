import asyncio
import random
import sqlite3
import json
import string
import time
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PreCheckoutQuery,
    LabeledPrice,
)

# ========== КОНФИГ ==========
BOT_TOKEN = "8200340859:AAFziC0Vk2KH71AwnCPvQBkyCfBl50eVMrs"
ADMIN_ID = 8293927811
ADMIN_USERNAME = "bestcod3r"  # Твой юзернейм для переводов

# Валюты
GRAM_NAME = "💎 Грам"
GOLD_NAME = "🏅 Iris-Gold"

# Стартовые балансы
START_GRAM = 1000.0
START_GOLD = 0.0

# Курс пополнения: 1 Star = сколько
STAR_TO_GRAM = 2222.0
STAR_TO_GOLD = 0.7

# Лимиты
MIN_BET_GRAM = 0.10
MAX_BET_GRAM = 100000.0
MIN_BET_GOLD = 0.01
MAX_BET_GOLD = 5000.0

# ========== СОЗДАЁМ DP ==========
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ========== БАЗА ДАННЫХ ==========
DB_PATH = "casino.db"

def init_db():
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(users)")
            columns = [col[1] for col in cur.fetchall()]
            conn.close()
            if "gram" not in columns:
                os.remove(DB_PATH)
                print("🗑️ Старая БД удалена, создаю новую")
        except:
            os.remove(DB_PATH)
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Таблица users с двумя валютами
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            gram REAL DEFAULT 1000,
            gold REAL DEFAULT 0,
            total_bets INTEGER DEFAULT 0,
            total_wins INTEGER DEFAULT 0,
            last_bonus INTEGER DEFAULT 0,
            total_deposited_gram REAL DEFAULT 0,
            total_deposited_gold REAL DEFAULT 0
        )
    ''')
    
    # Проверяем колонки
    cur.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cur.fetchall()]
    
    if "gram" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN gram REAL DEFAULT 1000")
    if "gold" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN gold REAL DEFAULT 0")
    if "total_bets" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN total_bets INTEGER DEFAULT 0")
    if "total_wins" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN total_wins INTEGER DEFAULT 0")
    if "last_bonus" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN last_bonus INTEGER DEFAULT 0")
    if "total_deposited_gram" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN total_deposited_gram REAL DEFAULT 0")
    if "total_deposited_gold" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN total_deposited_gold REAL DEFAULT 0")
    
    # Таблица заявок на пополнение
    cur.execute('''
        CREATE TABLE IF NOT EXISTS deposit_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            currency TEXT,
            amount REAL,
            stars_amount INTEGER,
            screenshot_id TEXT,
            status TEXT,
            created_at INTEGER,
            processed_at INTEGER
        )
    ''')
    
    # Таблица переводов Gold
    cur.execute('''
        CREATE TABLE IF NOT EXISTS gold_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user TEXT,
            to_user TEXT,
            amount REAL,
            status TEXT,
            created_at INTEGER,
            confirmed_at INTEGER
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS checks (
            code TEXT PRIMARY KEY,
            creator_id TEXT,
            per_user REAL,
            currency TEXT,
            remaining INTEGER,
            claimed TEXT
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS promos (
            name TEXT PRIMARY KEY,
            reward_gram REAL,
            reward_gold REAL,
            remaining_activations INTEGER,
            claimed TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")

init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def now_ts():
    return int(time.time())

def fmt_gram(value: float) -> str:
    value = round(float(value), 2)
    if value >= 1000:
        return f"{value/1000:.1f}K {GRAM_NAME}"
    return f"{value:.2f} {GRAM_NAME}"

def fmt_gold(value: float) -> str:
    value = round(float(value), 2)
    if value >= 1000:
        return f"{value/1000:.1f}K {GOLD_NAME}"
    return f"{value:.2f} {GOLD_NAME}"

def fmt_money(currency: str, value: float) -> str:
    if currency == "gram":
        return fmt_gram(value)
    return fmt_gold(value)

def escape_html(text: str) -> str:
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def mention_user(user_id: int, name: str = None) -> str:
    name = escape_html(name or f"Игрок{user_id}")
    return f'<a href="tg://user?id={user_id}">{name}</a>'

def ensure_user(user_id: int):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO users (user_id, gram, gold) VALUES (?, ?, ?)", 
                 (str(user_id), START_GRAM, START_GOLD))
    conn.commit()
    conn.close()

def get_user(user_id: int):
    conn = get_db()
    ensure_user(user_id)
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
    conn.close()
    return row

def update_balance(user_id: int, currency: str, delta: float) -> float:
    conn = get_db()
    conn.execute(f"UPDATE users SET {currency} = {currency} + ? WHERE user_id = ?", 
                 (round(delta, 2), str(user_id)))
    conn.commit()
    row = conn.execute(f"SELECT {currency} FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
    conn.close()
    return row[currency]

def add_deposit_request(user_id: int, currency: str, amount: float, stars: int, screenshot_id: str = None):
    conn = get_db()
    conn.execute('''
        INSERT INTO deposit_requests (user_id, currency, amount, stars_amount, screenshot_id, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
    ''', (str(user_id), currency, amount, stars, screenshot_id, now_ts()))
    conn.commit()
    request_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return request_id

def approve_deposit(request_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM deposit_requests WHERE id = ?", (request_id,)).fetchone()
    if not row:
        conn.close()
        return False
    
    user_id = row["user_id"]
    currency = row["currency"]
    amount = row["amount"]
    
    conn.execute(f"UPDATE users SET {currency} = {currency} + ?, total_deposited_{currency} = total_deposited_{currency} + ? WHERE user_id = ?",
                 (amount, amount, user_id))
    conn.execute("UPDATE deposit_requests SET status = 'approved', processed_at = ? WHERE id = ?", (now_ts(), request_id))
    conn.commit()
    conn.close()
    return True

def add_bet_record(user_id: int, bet: float, win: bool, game: str, currency: str):
    conn = get_db()
    conn.execute("UPDATE users SET total_bets = total_bets + 1 WHERE user_id = ?", (str(user_id),))
    if win:
        conn.execute("UPDATE users SET total_wins = total_wins + 1 WHERE user_id = ?", (str(user_id),))
    conn.commit()
    conn.close()

def get_top_players(currency: str, limit: int = 10):
    conn = get_db()
    rows = conn.execute(f"SELECT user_id, {currency} FROM users ORDER BY {currency} DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return rows

# ========== ЧЕКИ ==========
def generate_check_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def create_check(user_id: int, amount: float, currency: str, count: int) -> tuple:
    total = amount * count
    user = get_user(user_id)
    if user[currency] < total:
        return False, f"❌ Недостаточно {fmt_money(currency, total)}!"
    
    update_balance(user_id, currency, -total)
    code = generate_check_code()
    
    conn = get_db()
    conn.execute(
        "INSERT INTO checks (code, creator_id, per_user, currency, remaining, claimed) VALUES (?, ?, ?, ?, ?, ?)",
        (code, str(user_id), amount, currency, count, "[]")
    )
    conn.commit()
    conn.close()
    return True, code

def claim_check(user_id: int, code: str) -> tuple:
    conn = get_db()
    row = conn.execute("SELECT * FROM checks WHERE code = ?", (code.upper(),)).fetchone()
    if not row:
        conn.close()
        return False, "❌ Чек не найден!", 0, ""
    
    if row["remaining"] <= 0:
        conn.close()
        return False, "❌ Чек уже использован!", 0, ""
    
    claimed = json.loads(row["claimed"])
    if str(user_id) in claimed:
        conn.close()
        return False, "❌ Ты уже активировал этот чек!", 0, ""
    
    claimed.append(str(user_id))
    reward = row["per_user"]
    currency = row["currency"]
    update_balance(user_id, currency, reward)
    
    conn.execute(
        "UPDATE checks SET remaining = remaining - 1, claimed = ? WHERE code = ?",
        (json.dumps(claimed), code.upper())
    )
    conn.commit()
    conn.close()
    return True, f"✅ Активирован чек на {fmt_money(currency, reward)}!", reward, currency

def get_user_checks(user_id: int):
    conn = get_db()
    rows = conn.execute("SELECT code, per_user, currency, remaining FROM checks WHERE creator_id = ?", (str(user_id),)).fetchall()
    conn.close()
    return rows

# ========== ПРОМОКОДЫ ==========
def create_promo(code: str, reward_gram: float, reward_gold: float, activations: int):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO promos (name, reward_gram, reward_gold, remaining_activations, claimed) VALUES (?, ?, ?, ?, ?)",
        (code.upper(), reward_gram, reward_gold, activations, "[]")
    )
    conn.commit()
    conn.close()

def redeem_promo(user_id: int, code: str) -> tuple:
    conn = get_db()
    row = conn.execute("SELECT * FROM promos WHERE name = ?", (code.upper(),)).fetchone()
    if not row:
        conn.close()
        return False, "❌ Промокод не найден!", 0, 0
    
    if row["remaining_activations"] <= 0:
        conn.close()
        return False, "❌ Промокод уже использован!", 0, 0
    
    claimed = json.loads(row["claimed"])
    if str(user_id) in claimed:
        conn.close()
        return False, "❌ Ты уже активировал этот промокод!", 0, 0
    
    claimed.append(str(user_id))
    reward_gram = row["reward_gram"] or 0
    reward_gold = row["reward_gold"] or 0
    
    if reward_gram > 0:
        update_balance(user_id, "gram", reward_gram)
    if reward_gold > 0:
        update_balance(user_id, "gold", reward_gold)
    
    conn.execute(
        "UPDATE promos SET remaining_activations = remaining_activations - 1, claimed = ? WHERE name = ?",
        (json.dumps(claimed), code.upper())
    )
    conn.commit()
    conn.close()
    return True, f"✅ Промокод активирован!", reward_gram, reward_gold

# ========== КЛАВИАТУРЫ ==========
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="🎮 Игры", callback_data="games")],
        [InlineKeyboardButton(text="💎 Пополнить", callback_data="deposit")],
        [InlineKeyboardButton(text="🎁 Бонус", callback_data="bonus")],
        [InlineKeyboardButton(text="🏆 Топ игроков", callback_data="top")],
        [InlineKeyboardButton(text="🧾 Чеки", callback_data="checks_menu")],
        [InlineKeyboardButton(text="🎟 Промокод", callback_data="promo_menu")]
    ])

def games_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎡 Рулетка", callback_data="game_roulette"), InlineKeyboardButton(text="📈 Краш", callback_data="game_crash")],
        [InlineKeyboardButton(text="🎲 Кубик", callback_data="game_cube"), InlineKeyboardButton(text="🎯 Кости", callback_data="game_dice")],
        [InlineKeyboardButton(text="⚽ Футбол", callback_data="game_football"), InlineKeyboardButton(text="🏀 Баскетбол", callback_data="game_basket")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
    ])

def deposit_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Пополнить Граммы", callback_data="deposit_gram")],
        [InlineKeyboardButton(text="🏅 Пополнить Iris-Gold", callback_data="deposit_gold")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
    ])

def deposit_gram_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 Star = 2222 Грамма", callback_data="deposit_gram_1")],
        [InlineKeyboardButton(text="⭐ 10 Stars = 22220 Грамм", callback_data="deposit_gram_10")],
        [InlineKeyboardButton(text="⭐ 50 Stars = 111100 Грамм", callback_data="deposit_gram_50")],
        [InlineKeyboardButton(text="⭐ 100 Stars = 222200 Грамм", callback_data="deposit_gram_100")],
        [InlineKeyboardButton(text="⭐ Своя сумма", callback_data="deposit_gram_custom")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="deposit")]
    ])

def deposit_gold_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 Star = 0.7 Gold", callback_data="deposit_gold_1")],
        [InlineKeyboardButton(text="⭐ 10 Stars = 7 Gold", callback_data="deposit_gold_10")],
        [InlineKeyboardButton(text="⭐ 50 Stars = 35 Gold", callback_data="deposit_gold_50")],
        [InlineKeyboardButton(text="⭐ 100 Stars = 70 Gold", callback_data="deposit_gold_100")],
        [InlineKeyboardButton(text="⭐ Своя сумма", callback_data="deposit_gold_custom")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="deposit")]
    ])

def checks_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать чек", callback_data="check_create")],
        [InlineKeyboardButton(text="💸 Активировать чек", callback_data="check_claim")],
        [InlineKeyboardButton(text="📋 Мои чеки", callback_data="check_my")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
    ])

def back_button():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]])

# ========== СОСТОЯНИЯ ==========
class CheckCreateStates(StatesGroup):
    waiting_amount = State()
    waiting_count = State()
    waiting_currency = State()

class CheckClaimStates(StatesGroup):
    waiting_code = State()

class PromoStates(StatesGroup):
    waiting_code = State()

class BetStates(StatesGroup):
    waiting_amount = State()
    waiting_currency = State()
    waiting_crash_mult = State()

class DepositStates(StatesGroup):
    waiting_custom_amount = State()
    waiting_currency = State()
    waiting_screenshot = State()

class GoldTransferStates(StatesGroup):
    waiting_amount = State()
    waiting_username = State()

# ========== ИГРЫ ==========
RED_NUMBERS = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}

def roulette_spin(choice: str):
    num = random.randint(0, 36)
    color = "green" if num == 0 else ("red" if num in RED_NUMBERS else "black")
    win = False
    mult = 0
    if choice == "red" and color == "red":
        win, mult = True, 2
    elif choice == "black" and color == "black":
        win, mult = True, 2
    elif choice == "even" and num != 0 and num % 2 == 0:
        win, mult = True, 2
    elif choice == "odd" and num % 2 == 1:
        win, mult = True, 2
    elif choice == "zero" and num == 0:
        win, mult = True, 35
    return win, mult, num, color

def crash_game():
    r = random.random()
    if r < 0.05: return round(random.uniform(1.00, 1.50), 2)
    elif r < 0.30: return round(random.uniform(1.51, 2.50), 2)
    elif r < 0.60: return round(random.uniform(2.51, 4.00), 2)
    elif r < 0.85: return round(random.uniform(4.01, 7.00), 2)
    else: return round(random.uniform(7.01, 50.00), 2)

# ========== КОМАНДЫ ==========
@dp.message(CommandStart())
async def start_cmd(message: Message):
    ensure_user(message.from_user.id)
    try:
        await message.answer_sticker("CAACAgIAAxkBAAEI3Ppm-t0AAcwFpwGZtsqH0outXE-Z670AAmUgAAKBfylK3PLk7j0nC4U2BA")
    except:
        pass
    
    user = get_user(message.from_user.id)
    await message.answer(
        f"🌟 <b>Добро пожаловать в Casino Bot!</b>\n\n"
        f"💰 Баланс:\n"
        f"💎 {GRAM_NAME}: {fmt_gram(user['gram'])}\n"
        f"🏅 {GOLD_NAME}: {fmt_gold(user['gold'])}\n\n"
        f"👇 Используй кнопки ниже для игры:",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery):
    user = get_user(call.from_user.id)
    await call.message.edit_text(
        f"🌟 <b>Главное меню</b>\n\n"
        f"💰 Баланс:\n"
        f"💎 {GRAM_NAME}: {fmt_gram(user['gram'])}\n"
        f"🏅 {GOLD_NAME}: {fmt_gold(user['gold'])}",
        reply_markup=main_menu()
    )
    await call.answer()

@dp.callback_query(F.data == "profile")
async def profile_cmd(call: CallbackQuery):
    user = get_user(call.from_user.id)
    wins = user["total_wins"] or 0
    bets = user["total_bets"] or 1
    wr = (wins / bets) * 100
    await call.message.edit_text(
        f"👤 <b>Твой профиль</b>\n\n"
        f"🆔 ID: <code>{call.from_user.id}</code>\n\n"
        f"💰 Баланс:\n"
        f"💎 {GRAM_NAME}: {fmt_gram(user['gram'])}\n"
        f"🏅 {GOLD_NAME}: {fmt_gold(user['gold'])}\n\n"
        f"💎 Всего пополнено {GRAM_NAME}: {fmt_gram(user['total_deposited_gram'] or 0)}\n"
        f"🏅 Всего пополнено {GOLD_NAME}: {fmt_gold(user['total_deposited_gold'] or 0)}\n\n"
        f"🎲 Всего ставок: {bets}\n"
        f"🏆 Побед: {wins} ({wr:.1f}%)\n\n"
        f"📊 Ставки {GRAM_NAME}: от {fmt_gram(MIN_BET_GRAM)} до {fmt_gram(MAX_BET_GRAM)}\n"
        f"📊 Ставки {GOLD_NAME}: от {fmt_gold(MIN_BET_GOLD)} до {fmt_gold(MAX_BET_GOLD)}",
        reply_markup=back_button()
    )
    await call.answer()

@dp.callback_query(F.data == "top")
async def top_cmd(call: CallbackQuery):
    top_gram = get_top_players("gram", 5)
    top_gold = get_top_players("gold", 5)
    
    text = "🏆 <b>Топ игроков</b>\n\n"
    text += "💎 <b>По Граммам:</b>\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, p in enumerate(top_gram):
        medal = medals[i] if i < 3 else f"{i+1}."
        text += f"{medal} {mention_user(int(p['user_id']))} — {fmt_gram(p['gram'])}\n"
    
    text += "\n🏅 <b>По Iris-Gold:</b>\n"
    for i, p in enumerate(top_gold):
        medal = medals[i] if i < 3 else f"{i+1}."
        text += f"{medal} {mention_user(int(p['user_id']))} — {fmt_gold(p['gold'])}\n"
    
    await call.message.edit_text(text, reply_markup=back_button())
    await call.answer()

@dp.callback_query(F.data == "bonus")
async def bonus_cmd(call: CallbackQuery):
    user_id = call.from_user.id
    user = get_user(user_id)
    last_bonus = user["last_bonus"] or 0
    now = now_ts()
    
    if now - last_bonus < 43200:
        left = 43200 - (now - last_bonus)
        hours = left // 3600
        minutes = (left % 3600) // 60
        await call.message.edit_text(
            f"⏰ <b>Бонус ещё не доступен!</b>\n\n"
            f"Приходи через {hours}ч {minutes}мин",
            reply_markup=back_button()
        )
        await call.answer()
        return
    
    reward_gram = random.randint(100, 500)
    reward_gold = random.uniform(0.5, 2.0)
    
    update_balance(user_id, "gram", reward_gram)
    update_balance(user_id, "gold", reward_gold)
    
    conn = get_db()
    conn.execute("UPDATE users SET last_bonus = ? WHERE user_id = ?", (now, str(user_id)))
    conn.commit()
    conn.close()
    
    await call.message.edit_text(
        f"🎁 <b>Ежедневный бонус!</b>\n\n"
        f"✨ Ты получил:\n"
        f"💎 +{fmt_gram(reward_gram)}\n"
        f"🏅 +{fmt_gold(reward_gold)}",
        reply_markup=back_button()
    )
    await call.answer()

# ========== ПОПОЛНЕНИЕ ==========
@dp.callback_query(F.data == "deposit")
async def deposit_menu_cmd(call: CallbackQuery):
    await call.message.edit_text(
        f"💎 <b>Пополнение баланса</b>\n\n"
        f"⭐ <b>Курс:</b>\n"
        f"• 1 Star = {fmt_gram(STAR_TO_GRAM)}\n"
        f"• 1 Star = {fmt_gold(STAR_TO_GOLD)}\n\n"
        f"Выбери валюту для пополнения:",
        reply_markup=deposit_menu()
    )
    await call.answer()

@dp.callback_query(F.data == "deposit_gram")
async def deposit_gram_menu(call: CallbackQuery):
    await call.message.edit_text(
        f"💎 <b>Пополнение {GRAM_NAME}</b>\n\n"
        f"⭐ <b>Курс:</b> 1 Star = {fmt_gram(STAR_TO_GRAM)}\n\n"
        f"💰 После оплаты пришли скриншот для подтверждения\n\n"
        f"Выбери сумму:",
        reply_markup=deposit_gram_menu()
    )
    await call.answer()

@dp.callback_query(F.data == "deposit_gold")
async def deposit_gold_menu(call: CallbackQuery):
    await call.message.edit_text(
        f"🏅 <b>Пополнение {GOLD_NAME}</b>\n\n"
        f"⭐ <b>Курс:</b> 1 Star = {fmt_gold(STAR_TO_GOLD)}\n\n"
        f"💰 После оплаты пришли скриншот для подтверждения\n\n"
        f"Выбери сумму:",
        reply_markup=deposit_gold_menu()
    )
    await call.answer()

@dp.callback_query(F.data.startswith("deposit_gram_"))
async def process_gram_deposit(call: CallbackQuery, state: FSMContext):
    if call.data == "deposit_gram_custom":
        await state.set_state(DepositStates.waiting_custom_amount)
        await state.update_data(currency="gram")
        await call.message.edit_text(
            f"💎 Введи сумму в Stars (мин. 1):\n\n"
            f"⭐ 1 Star = {fmt_gram(STAR_TO_GRAM)}",
            reply_markup=back_button()
        )
        await call.answer()
        return
    
    stars = int(call.data.split("_")[2])
    gram = stars * STAR_TO_GRAM
    
    await state.update_data(currency="gram", stars=stars, amount=gram)
    await state.set_state(DepositStates.waiting_screenshot)
    
    await call.message.edit_text(
        f"💎 <b>Пополнение {GRAM_NAME}</b>\n\n"
        f"⭐ Stars: {stars}\n"
        f"💰 Получишь: {fmt_gram(gram)}\n\n"
        f"📤 <b>Инструкция:</b>\n"
        f"1. Отправь {stars} Stars на @{ADMIN_USERNAME}\n"
        f"2. Пришли скриншот перевода в этот чат\n\n"
        f"⏳ После проверки админом средства поступят на баланс",
        reply_markup=back_button()
    )
    await call.answer()

@dp.callback_query(F.data.startswith("deposit_gold_"))
async def process_gold_deposit(call: CallbackQuery, state: FSMContext):
    if call.data == "deposit_gold_custom":
        await state.set_state(DepositStates.waiting_custom_amount)
        await state.update_data(currency="gold")
        await call.message.edit_text(
            f"🏅 Введи сумму в Stars (мин. 1):\n\n"
            f"⭐ 1 Star = {fmt_gold(STAR_TO_GOLD)}",
            reply_markup=back_button()
        )
        await call.answer()
        return
    
    stars = int(call.data.split("_")[2])
    gold = stars * STAR_TO_GOLD
    
    await state.update_data(currency="gold", stars=stars, amount=gold)
    await state.set_state(DepositStates.waiting_screenshot)
    
    await call.message.edit_text(
        f"🏅 <b>Пополнение {GOLD_NAME}</b>\n\n"
        f"⭐ Stars: {stars}\n"
        f"💰 Получишь: {fmt_gold(gold)}\n\n"
        f"📤 <b>Инструкция:</b>\n"
        f"1. Отправь {stars} Stars на @{ADMIN_USERNAME}\n"
        f"2. Пришли скриншот перевода в этот чат\n\n"
        f"⏳ После проверки админом средства поступят на баланс",
        reply_markup=back_button()
    )
    await call.answer()

@dp.message(DepositStates.waiting_custom_amount)
async def process_custom_amount(msg: Message, state: FSMContext):
    try:
        stars = float(msg.text.replace(",", "."))
        if stars < 1:
            await msg.answer("❌ Минимальная сумма: 1 Star")
            return
        
        data = await state.get_data()
        currency = data["currency"]
        
        if currency == "gram":
            amount = stars * STAR_TO_GRAM
            await state.update_data(stars=int(stars), amount=amount)
            await state.set_state(DepositStates.waiting_screenshot)
            await msg.answer(
                f"💎 <b>Пополнение {GRAM_NAME}</b>\n\n"
                f"⭐ Stars: {int(stars)}\n"
                f"💰 Получишь: {fmt_gram(amount)}\n\n"
                f"📤 <b>Инструкция:</b>\n"
                f"1. Отправь {int(stars)} Stars на @{ADMIN_USERNAME}\n"
                f"2. Пришли скриншот перевода в этот чат\n\n"
                f"⏳ После проверки админом средства поступят на баланс",
                reply_markup=back_button()
            )
        else:
            amount = stars * STAR_TO_GOLD
            await state.update_data(stars=int(stars), amount=amount)
            await state.set_state(DepositStates.waiting_screenshot)
            await msg.answer(
                f"🏅 <b>Пополнение {GOLD_NAME}</b>\n\n"
                f"⭐ Stars: {int(stars)}\n"
                f"💰 Получишь: {fmt_gold(amount)}\n\n"
                f"📤 <b>Инструкция:</b>\n"
                f"1. Отправь {int(stars)} Stars на @{ADMIN_USERNAME}\n"
                f"2. Пришли скриншот перевода в этот чат\n\n"
                f"⏳ После проверки админом средства поступят на баланс",
                reply_markup=back_button()
            )
    except:
        await msg.answer("❌ Введи корректную сумму!")

@dp.message(DepositStates.waiting_screenshot)
async def process_screenshot(msg: Message, state: FSMContext):
    if not msg.photo:
        await msg.answer("❌ Пожалуйста, отправь скриншот перевода")
        return
    
    data = await state.get_data()
    currency = data["currency"]
    stars = data["stars"]
    amount = data["amount"]
    
    # Сохраняем заявку
    request_id = add_deposit_request(msg.from_user.id, currency, amount, stars, msg.photo[-1].file_id)
    
    # Отправляем админу
    caption = (
        f"📥 <b>НОВАЯ ЗАЯВКА НА ПОПОЛНЕНИЕ</b>\n\n"
        f"👤 Пользователь: {mention_user(msg.from_user.id, msg.from_user.first_name)}\n"
        f"🆔 ID: <code>{msg.from_user.id}</code>\n"
        f"💎 Валюта: {currency.upper()}\n"
        f"⭐ Stars: {stars}\n"
        f"💰 Сумма: {fmt_money(currency, amount)}\n"
        f"🆔 Заявка: #{request_id}\n\n"
        f"✅ /approve_{request_id} - подтвердить\n"
        f"❌ /decline_{request_id} - отклонить"
    )
    
    await msg.bot.send_photo(ADMIN_ID, photo=msg.photo[-1].file_id, caption=caption)
    
    await msg.answer(
        f"✅ <b>Заявка #{request_id} отправлена!</b>\n\n"
        f"💰 Админ проверит перевод и пополнит баланс в ближайшее время.\n\n"
        f"Спасибо за ожидание! 🎮",
        reply_markup=main_menu()
    )
    await state.clear()

# ========== АДМИН КОМАНДЫ ДЛЯ ПОПОЛНЕНИЯ ==========
@dp.message(Command("approve"))
async def approve_deposit(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.answer("⛔ Только для админа!")
        return
    
    parts = msg.text.split()
    if len(parts) != 2:
        await msg.answer("📝 Формат: /approve ID_ЗАЯВКИ")
        return
    
    try:
        request_id = int(parts[1])
        if approve_deposit(request_id):
            # Уведомляем пользователя
            conn = get_db()
            row = conn.execute("SELECT user_id, currency, amount FROM deposit_requests WHERE id = ?", (request_id,)).fetchone()
            conn.close()
            
            if row:
                await msg.bot.send_message(
                    int(row["user_id"]),
                    f"✅ <b>Ваша заявка #{request_id} одобрена!</b>\n\n"
                    f"💰 Начислено: {fmt_money(row['currency'], row['amount'])}\n\n"
                    f"🎮 Приятной игры!"
                )
            await msg.answer(f"✅ Заявка #{request_id} подтверждена!")
        else:
            await msg.answer(f"❌ Заявка #{request_id} не найдена")
    except:
        await msg.answer("❌ Ошибка!")

@dp.message(Command("decline"))
async def decline_deposit(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.answer("⛔ Только для админа!")
        return
    
    parts = msg.text.split()
    if len(parts) != 2:
        await msg.answer("📝 Формат: /decline ID_ЗАЯВКИ")
        return
    
    try:
        request_id = int(parts[1])
        conn = get_db()
        conn.execute("UPDATE deposit_requests SET status = 'declined', processed_at = ? WHERE id = ?", (now_ts(), request_id))
        row = conn.execute("SELECT user_id FROM deposit_requests WHERE id = ?", (request_id,)).fetchone()
        conn.commit()
        conn.close()
        
        if row:
            await msg.bot.send_message(
                int(row["user_id"]),
                f"❌ <b>Ваша заявка #{request_id} отклонена!</b>\n\n"
                f"Пожалуйста, свяжитесь с админом для уточнения деталей."
            )
        await msg.answer(f"❌ Заявка #{request_id} отклонена!")
    except:
        await msg.answer("❌ Ошибка!")

# ========== ПЕРЕВОДЫ GOLD ==========
@dp.message(Command("transfer"))
async def transfer_gold_start(msg: Message, state: FSMContext):
    await state.set_state(GoldTransferStates.waiting_amount)
    await state.update_data(currency="gold")
    await msg.answer(
        f"🏅 <b>Перевод {GOLD_NAME}</b>\n\n"
        f"Введи сумму для перевода (мин. 0.01):",
        reply_markup=back_button()
    )

@dp.message(GoldTransferStates.waiting_amount)
async def transfer_gold_amount(msg: Message, state: FSMContext):
    try:
        amount = float(msg.text.replace(",", "."))
        if amount < 0.01:
            await msg.answer("❌ Минимальная сумма перевода: 0.01 Gold")
            return
        
        user = get_user(msg.from_user.id)
        if user["gold"] < amount:
            await msg.answer(f"❌ Недостаточно средств! У тебя: {fmt_gold(user['gold'])}")
            return
        
        await state.update_data(amount=amount)
        await state.set_state(GoldTransferStates.waiting_username)
        await msg.answer(
            f"🏅 Введи @username получателя:\n\n"
            f"💰 Сумма: {fmt_gold(amount)}"
        )
    except:
        await msg.answer("❌ Введи корректную сумму!")

@dp.message(GoldTransferStates.waiting_username)
async def transfer_gold_username(msg: Message, state: FSMContext):
    username = msg.text.strip()
    if not username.startswith("@"):
        username = "@" + username
    
    data = await state.get_data()
    amount = data["amount"]
    
    # Находим пользователя по username
    try:
        # Пробуем получить user_id через бота
        chat = await msg.bot.get_chat(username)
        target_id = chat.id
        
        if target_id == msg.from_user.id:
            await msg.answer("❌ Нельзя перевести самому себе!")
            await state.clear()
            return
        
        # Списываем у отправителя
        update_balance(msg.from_user.id, "gold", -amount)
        # Зачисляем получателю
        update_balance(target_id, "gold", amount)
        
        await msg.answer(
            f"✅ <b>Перевод выполнен!</b>\n\n"
            f"🏅 Получатель: {username}\n"
            f"💰 Сумма: {fmt_gold(amount)}\n"
            f"💎 Твой баланс: {fmt_gold(get_user(msg.from_user.id)['gold'])}"
        )
        
        await msg.bot.send_message(
            target_id,
            f"✅ <b>Вам поступил перевод!</b>\n\n"
            f"👤 От: {mention_user(msg.from_user.id, msg.from_user.first_name)}\n"
            f"💰 Сумма: {fmt_gold(amount)}\n"
            f"💎 Новый баланс: {fmt_gold(get_user(target_id)['gold'])}"
        )
        
        await state.clear()
    except Exception as e:
        await msg.answer(f"❌ Пользователь {username} не найден!")
        await state.clear()

# ========== ЧЕКИ ==========
@dp.callback_query(F.data == "checks_menu")
async def checks_menu_cmd(call: CallbackQuery):
    await call.message.edit_text(
        "🧾 <b>Чеки</b>\n\n"
        "Создавай чеки для друзей или активируй чужие!",
        reply_markup=checks_menu_kb()
    )
    await call.answer()

@dp.callback_query(F.data == "check_create")
async def check_create(call: CallbackQuery, state: FSMContext):
    await state.set_state(CheckCreateStates.waiting_currency)
    await call.message.edit_text(
        f"🧾 <b>Создание чека</b>\n\n"
        f"Выбери валюту:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Граммы", callback_data="check_currency_gram")],
            [InlineKeyboardButton(text="🏅 Iris-Gold", callback_data="check_currency_gold")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="checks_menu")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data.startswith("check_currency_"))
async def check_currency(call: CallbackQuery, state: FSMContext):
    currency = call.data.split("_")[2]
    await state.update_data(currency=currency)
    await state.set_state(CheckCreateStates.waiting_amount)
    
    min_amount = MIN_BET_GRAM if currency == "gram" else MIN_BET_GOLD
    await call.message.edit_text(
        f"💸 <b>Создание чека</b>\n\n"
        f"Валюта: {GRAM_NAME if currency == 'gram' else GOLD_NAME}\n"
        f"Введи сумму для одной активации (мин. {fmt_money(currency, min_amount)}):",
        reply_markup=back_button()
    )
    await call.answer()

@dp.message(CheckCreateStates.waiting_amount)
async def check_amount(msg: Message, state: FSMContext):
    try:
        amount = float(msg.text.replace(",", "."))
        data = await state.get_data()
        currency = data["currency"]
        
        min_amount = MIN_BET_GRAM if currency == "gram" else MIN_BET_GOLD
        if amount < min_amount:
            await msg.answer(f"❌ Минимальная сумма: {fmt_money(currency, min_amount)}")
            return
        
        await state.update_data(amount=amount)
        await state.set_state(CheckCreateStates.waiting_count)
        await msg.answer("📦 Введи количество активаций (1-100):")
    except:
        await msg.answer("❌ Введи число, например: 100")

@dp.message(CheckCreateStates.waiting_count)
async def check_count(msg: Message, state: FSMContext):
    try:
        count = int(msg.text)
        if count < 1 or count > 100:
            await msg.answer("❌ Количество от 1 до 100")
            return
        
        data = await state.get_data()
        amount = data["amount"]
        currency = data["currency"]
        
        ok, result = create_check(msg.from_user.id, amount, currency, count)
        await state.clear()
        if ok:
            await msg.answer(
                f"✅ <b>Чек создан!</b>\n\n"
                f"🎫 Код: <code>{result}</code>\n"
                f"💰 Сумма: {fmt_money(currency, amount)}\n"
                f"💎 Валюта: {GRAM_NAME if currency == 'gram' else GOLD_NAME}\n"
                f"📦 Активаций: {count}"
            )
        else:
            await msg.answer(f"❌ {result}")
    except:
        await msg.answer("❌ Введи целое число")

@dp.callback_query(F.data == "check_claim")
async def check_claim(call: CallbackQuery, state: FSMContext):
    await state.set_state(CheckClaimStates.waiting_code)
    await call.message.edit_text(
        "🎫 <b>Активация чека</b>\n\n"
        "Введи код чека:",
        reply_markup=back_button()
    )
    await call.answer()

@dp.message(CheckClaimStates.waiting_code)
async def claim_code(msg: Message, state: FSMContext):
    code = msg.text.strip().upper()
    ok, result, reward, currency = claim_check(msg.from_user.id, code)
    await state.clear()
    if ok:
        await msg.answer(f"✅ {result}\n💰 Новый баланс: {fmt_money(currency, get_user(msg.from_user.id)[currency])}")
    else:
        await msg.answer(f"❌ {result}")

@dp.callback_query(F.data == "check_my")
async def my_checks(call: CallbackQuery):
    checks = get_user_checks(call.from_user.id)
    if not checks:
        await call.message.edit_text("📭 У тебя пока нет созданных чеков", reply_markup=back_button())
    else:
        text = "🧾 <b>Твои чеки</b>\n\n"
        for c in checks:
            currency_name = GRAM_NAME if c['currency'] == 'gram' else GOLD_NAME
            text += f"🎫 <code>{c['code']}</code> | {fmt_money(c['currency'], c['per_user'])} | {currency_name} | осталось: {c['remaining']}\n"
        await call.message.edit_text(text, reply_markup=back_button())
    await call.answer()

# ========== ПРОМОКОДЫ ==========
@dp.callback_query(F.data == "promo_menu")
async def promo_menu_cmd(call: CallbackQuery, state: FSMContext):
    await state.set_state(PromoStates.waiting_code)
    await call.message.edit_text(
        "🎟 <b>Активация промокода</b>\n\n"
        "Введи промокод:",
        reply_markup=back_button()
    )
    await call.answer()

@dp.message(PromoStates.waiting_code)
async def activate_promo(msg: Message, state: FSMContext):
    code = msg.text.strip().upper()
    ok, result, reward_gram, reward_gold = redeem_promo(msg.from_user.id, code)
    await state.clear()
    if ok:
        text = f"🎉 {result}\n\n"
        if reward_gram > 0:
            text += f"💎 +{fmt_gram(reward_gram)}\n"
        if reward_gold > 0:
            text += f"🏅 +{fmt_gold(reward_gold)}\n"
        user = get_user(msg.from_user.id)
        text += f"\n💰 Новый баланс:\n💎 {fmt_gram(user['gram'])}\n🏅 {fmt_gold(user['gold'])}"
        await msg.answer(text)
    else:
        await msg.answer(f"❌ {result}")

# ========== АДМИН КОМАНДЫ ДЛЯ ПРОМОКОДОВ ==========
@dp.message(Command("addpromo"))
async def add_promo(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.answer("⛔ Только для админа!")
        return
    parts = msg.text.split()
    if len(parts) != 5:
        await msg.answer("📝 Формат: /addpromo КОД ГРАММЫ ГОЛД АКТИВАЦИИ\nПример: /addpromo WELCOME 1000 5 50")
        return
    code = parts[1].upper()
    try:
        reward_gram = float(parts[2])
        reward_gold = float(parts[3])
        activations = int(parts[4])
        create_promo(code, reward_gram, reward_gold, activations)
        await msg.answer(f"✅ Промокод создан!\n🎫 {code}\n💎 {fmt_gram(reward_gram)}\n🏅 {fmt_gold(reward_gold)}\n🎯 {activations} активаций")
    except:
        await msg.answer("❌ Ошибка!")

@dp.message(Command("give"))
async def give_money(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.answer("⛔ Только для админа!")
        return
    parts = msg.text.split()
    if len(parts) != 4:
        await msg.answer("📝 Формат: /give ID ВАЛЮТА СУММА\nВалюта: gram или gold\nПример: /give 123456789 gram 1000")
        return
    try:
        target_id = int(parts[1])
        currency = parts[2].lower()
        if currency not in ["gram", "gold"]:
            await msg.answer("❌ Валюта должна быть gram или gold")
            return
        amount = float(parts[3])
        new_balance = update_balance(target_id, currency, amount)
        await msg.answer(f"✅ Выдано {fmt_money(currency, amount)} пользователю {target_id}\n💰 Новый баланс: {fmt_money(currency, new_balance)}")
    except:
        await msg.answer("❌ Ошибка!")

# ========== ИГРЫ (упрощённо) ==========
@dp.callback_query(F.data == "games")
async def games_list(call: CallbackQuery):
    await call.message.edit_text(
        "🎮 <b>Выбери игру</b>\n\n"
        f"📊 Ставки {GRAM_NAME}: от {fmt_gram(MIN_BET_GRAM)} до {fmt_gram(MAX_BET_GRAM)}\n"
        f"📊 Ставки {GOLD_NAME}: от {fmt_gold(MIN_BET_GOLD)} до {fmt_gold(MAX_BET_GOLD)}",
        reply_markup=games_menu()
    )
    await call.answer()

@dp.callback_query(F.data.startswith("game_"))
async def game_choice(call: CallbackQuery, state: FSMContext):
    game = call.data.split("_")[1]
    await state.update_data(game=game)
    
    await call.message.edit_text(
        f"🎮 <b>Игра {game.upper()}</b>\n\n"
        f"Выбери валюту для ставки:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Граммы", callback_data=f"bet_currency_gram")],
            [InlineKeyboardButton(text="🏅 Iris-Gold", callback_data=f"bet_currency_gold")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="games")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data.startswith("bet_currency_"))
async def bet_currency(call: CallbackQuery, state: FSMContext):
    currency = call.data.split("_")[2]
    await state.update_data(currency=currency)
    await state.set_state(BetStates.waiting_amount)
    
    min_bet = MIN_BET_GRAM if currency == "gram" else MIN_BET_GOLD
    max_bet = MAX_BET_GRAM if currency == "gram" else MAX_BET_GOLD
    
    await call.message.edit_text(
        f"💰 <b>Введи сумму ставки</b>\n\n"
        f"💎 Валюта: {GRAM_NAME if currency == 'gram' else GOLD_NAME}\n"
        f"📊 Лимиты: от {fmt_money(currency, min_bet)} до {fmt_money(currency, max_bet)}",
        reply_markup=back_button()
    )
    await call.answer()

@dp.message(BetStates.waiting_amount)
async def process_bet(msg: Message, state: FSMContext):
    try:
        bet = float(msg.text.replace(",", "."))
        data = await state.get_data()
        game = data["game"]
        currency = data["currency"]
        
        min_bet = MIN_BET_GRAM if currency == "gram" else MIN_BET_GOLD
        max_bet = MAX_BET_GRAM if currency == "gram" else MAX_BET_GOLD
        
        if bet < min_bet:
            await msg.answer(f"❌ Минимальная ставка: {fmt_money(currency, min_bet)}")
            return
        if bet > max_bet:
            await msg.answer(f"❌ Максимальная ставка: {fmt_money(currency, max_bet)}")
            return
        
        user = get_user(msg.from_user.id)
        if user[currency] < bet:
            await msg.answer(f"❌ Недостаточно средств! Твой баланс: {fmt_money(currency, user[currency])}")
            return
        
        # Футбол
        if game == "football":
            result = await msg.answer_dice(emoji="⚽")
            value = result.dice.value
            win = value >= 4
            payout = bet * 1.85 if win else 0
            new_balance = update_balance(msg.from_user.id, currency, -bet + payout)
            add_bet_record(msg.from_user.id, bet, win, "football", currency)
            
            outcome = "ГОЛ 🎉" if win else "МИМО 😔"
            await msg.answer(
                f"⚽ <b>Футбол</b>\n\n"
                f"🎲 Результат: <b>{outcome}</b>\n"
                f"💰 Ставка: {fmt_money(currency, bet)}\n"
                f"{'🎉' if win else '😔'} Итог: <b>{'ПОБЕДА' if win else 'ПРОИГРЫШ'}</b>\n"
                f"💸 Выплата: {fmt_money(currency, payout)}\n"
                f"💎 Новый баланс: {fmt_money(currency, new_balance)}"
            )
            await state.clear()
            return
        
        # Баскетбол
        if game == "basket":
            result = await msg.answer_dice(emoji="🏀")
            value = result.dice.value
            win = value in [4, 5]
            payout = bet * 1.85 if win else 0
            new_balance = update_balance(msg.from_user.id, currency, -bet + payout)
            add_bet_record(msg.from_user.id, bet, win, "basket", currency)
            
            outcome = "ТОЧНЫЙ БРОСОК 🎉" if win else "ПРОМАХ 😔"
            await msg.answer(
                f"🏀 <b>Баскетбол</b>\n\n"
                f"🎲 Результат: <b>{outcome}</b>\n"
                f"💰 Ставка: {fmt_money(currency, bet)}\n"
                f"{'🎉' if win else '😔'} Итог: <b>{'ПОБЕДА' if win else 'ПРОИГРЫШ'}</b>\n"
                f"💸 Выплата: {fmt_money(currency, payout)}\n"
                f"💎 Новый баланс: {fmt_money(currency, new_balance)}"
            )
            await state.clear()
            return
        
        # Кубик
        if game == "cube":
            await state.update_data(bet=bet)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="1️⃣", callback_data=f"cube_1_{currency}"), InlineKeyboardButton(text="2️⃣", callback_data=f"cube_2_{currency}"),
                 InlineKeyboardButton(text="3️⃣", callback_data=f"cube_3_{currency}")],
                [InlineKeyboardButton(text="4️⃣", callback_data=f"cube_4_{currency}"), InlineKeyboardButton(text="5️⃣", callback_data=f"cube_5_{currency}"),
                 InlineKeyboardButton(text="6️⃣", callback_data=f"cube_6_{currency}")],
                [InlineKeyboardButton(text="◀️ Отмена", callback_data="games")]
            ])
            await msg.answer("🎲 Угадай число:", reply_markup=kb)
            await state.clear()
            return
        
        # Кости
        if game == "dice":
            await state.update_data(bet=bet)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📈 Больше 7 (x1.9)", callback_data=f"dice_high_{currency}")],
                [InlineKeyboardButton(text="📉 Меньше 7 (x1.9)", callback_data=f"dice_low_{currency}")],
                [InlineKeyboardButton(text="🎯 Равно 7 (x5.0)", callback_data=f"dice_seven_{currency}")],
                [InlineKeyboardButton(text="◀️ Отмена", callback_data="games")]
            ])
            await msg.answer("🎯 Выбери исход:", reply_markup=kb)
            await state.clear()
            return
        
        # Краш
        if game == "crash":
            await state.update_data(bet=bet)
            await state.set_state(BetStates.waiting_crash_mult)
            await msg.answer(f"📈 Введи множитель выигрыша (1.10 - 10.00):")
            return
        
        # Рулетка
        if game == "roulette":
            await state.update_data(bet=bet)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔴 Красное (x2)", callback_data=f"roulette_red_{currency}"),
                 InlineKeyboardButton(text="⚫ Чёрное (x2)", callback_data=f"roulette_black_{currency}")],
                [InlineKeyboardButton(text="2️⃣ Чёт (x2)", callback_data=f"roulette_even_{currency}"),
                 InlineKeyboardButton(text="1️⃣ Нечет (x2)", callback_data=f"roulette_odd_{currency}")],
                [InlineKeyboardButton(text="0️⃣ Зеро (x35)", callback_data=f"roulette_zero_{currency}")],
                [InlineKeyboardButton(text="◀️ Отмена", callback_data="games")]
            ])
            await msg.answer("🎡 Выбери тип ставки:", reply_markup=kb)
            await state.clear()
            return
        
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")

# ========== ЗАПУСК ==========
async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
