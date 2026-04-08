import asyncio
import random
import sqlite3
import json
import string
import time
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
CURRENCY_NAME = "🥇 Gold"
START_BALANCE = 1000.0
MIN_BET = 0.10
MAX_BET = 100000.0
MIN_DEPOSIT = 0.10

# Курс: 1 Star = 1 Gold
STARS_TO_GOLD = 1

# ========== СОЗДАЁМ DP ==========
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ========== БАЗА ДАННЫХ ==========
DB_PATH = "casino.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            coins REAL DEFAULT 1000,
            total_bets INTEGER DEFAULT 0,
            total_wins INTEGER DEFAULT 0,
            last_bonus INTEGER DEFAULT 0,
            total_deposited REAL DEFAULT 0
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS checks (
            code TEXT PRIMARY KEY,
            creator_id TEXT,
            per_user REAL,
            remaining INTEGER,
            claimed TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS promos (
            name TEXT PRIMARY KEY,
            reward REAL,
            remaining_activations INTEGER,
            claimed TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            stars_amount INTEGER,
            gold_amount REAL,
            status TEXT,
            created_at INTEGER,
            paid_at INTEGER
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def now_ts():
    return int(time.time())

def fmt_money(value: float) -> str:
    value = round(float(value), 2)
    if value >= 1000:
        return f"{value/1000:.1f}K {CURRENCY_NAME}"
    return f"{value:.2f} {CURRENCY_NAME}"

def escape_html(text: str) -> str:
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def mention_user(user_id: int, name: str = None) -> str:
    name = escape_html(name or f"Игрок{user_id}")
    return f'<a href="tg://user?id={user_id}">{name}</a>'

def ensure_user(user_id: int):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO users (user_id, coins) VALUES (?, ?)", (str(user_id), START_BALANCE))
    conn.commit()
    conn.close()

def get_user(user_id: int):
    conn = get_db()
    ensure_user(user_id)
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
    conn.close()
    return row

def update_balance(user_id: int, delta: float) -> float:
    conn = get_db()
    conn.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (round(delta, 2), str(user_id)))
    conn.commit()
    row = conn.execute("SELECT coins FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
    conn.close()
    return row["coins"]

def add_deposit_record(user_id: int, stars: int, gold: float):
    conn = get_db()
    conn.execute('''
        INSERT INTO deposits (user_id, stars_amount, gold_amount, status, created_at, paid_at)
        VALUES (?, ?, ?, 'pending', ?, NULL)
    ''', (str(user_id), stars, gold, now_ts()))
    conn.commit()
    deposit_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return deposit_id

def mark_deposit_paid(deposit_id: int, user_id: int):
    conn = get_db()
    conn.execute('''
        UPDATE deposits SET status = 'paid', paid_at = ? WHERE id = ?
    ''', (now_ts(), deposit_id))
    gold = conn.execute("SELECT gold_amount FROM deposits WHERE id = ?", (deposit_id,)).fetchone()[0]
    conn.execute("UPDATE users SET total_deposited = total_deposited + ? WHERE user_id = ?", (gold, str(user_id)))
    conn.commit()
    conn.close()

def add_bet_record(user_id: int, bet: float, win: bool, game: str):
    conn = get_db()
    conn.execute("UPDATE users SET total_bets = total_bets + 1 WHERE user_id = ?", (str(user_id),))
    if win:
        conn.execute("UPDATE users SET total_wins = total_wins + 1 WHERE user_id = ?", (str(user_id),))
    conn.commit()
    conn.close()

def get_top_players(limit: int = 10):
    conn = get_db()
    rows = conn.execute("SELECT user_id, coins FROM users ORDER BY coins DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return rows

# ========== ЧЕКИ ==========
def generate_check_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def create_check(user_id: int, amount: float, count: int) -> tuple:
    total = amount * count
    user = get_user(user_id)
    if user["coins"] < total:
        return False, "❌ Недостаточно средств!"
    
    update_balance(user_id, -total)
    code = generate_check_code()
    
    conn = get_db()
    conn.execute(
        "INSERT INTO checks (code, creator_id, per_user, remaining, claimed) VALUES (?, ?, ?, ?, ?)",
        (code, str(user_id), amount, count, "[]")
    )
    conn.commit()
    conn.close()
    return True, code

def claim_check(user_id: int, code: str) -> tuple:
    conn = get_db()
    row = conn.execute("SELECT * FROM checks WHERE code = ?", (code.upper(),)).fetchone()
    if not row:
        conn.close()
        return False, "❌ Чек не найден!", 0
    
    if row["remaining"] <= 0:
        conn.close()
        return False, "❌ Чек уже использован!", 0
    
    claimed = json.loads(row["claimed"])
    if str(user_id) in claimed:
        conn.close()
        return False, "❌ Ты уже активировал этот чек!", 0
    
    claimed.append(str(user_id))
    reward = row["per_user"]
    update_balance(user_id, reward)
    
    conn.execute(
        "UPDATE checks SET remaining = remaining - 1, claimed = ? WHERE code = ?",
        (json.dumps(claimed), code.upper())
    )
    conn.commit()
    conn.close()
    return True, f"✅ Активирован чек на {fmt_money(reward)}!", reward

def get_user_checks(user_id: int):
    conn = get_db()
    rows = conn.execute("SELECT code, per_user, remaining FROM checks WHERE creator_id = ?", (str(user_id),)).fetchall()
    conn.close()
    return rows

# ========== ПРОМОКОДЫ ==========
def create_promo(code: str, reward: float, activations: int):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO promos (name, reward, remaining_activations, claimed) VALUES (?, ?, ?, ?)",
        (code.upper(), reward, activations, "[]")
    )
    conn.commit()
    conn.close()

def redeem_promo(user_id: int, code: str) -> tuple:
    conn = get_db()
    row = conn.execute("SELECT * FROM promos WHERE name = ?", (code.upper(),)).fetchone()
    if not row:
        conn.close()
        return False, "❌ Промокод не найден!", 0
    
    if row["remaining_activations"] <= 0:
        conn.close()
        return False, "❌ Промокод уже использован!", 0
    
    claimed = json.loads(row["claimed"])
    if str(user_id) in claimed:
        conn.close()
        return False, "❌ Ты уже активировал этот промокод!", 0
    
    claimed.append(str(user_id))
    reward = row["reward"]
    update_balance(user_id, reward)
    
    conn.execute(
        "UPDATE promos SET remaining_activations = remaining_activations - 1, claimed = ? WHERE name = ?",
        (json.dumps(claimed), code.upper())
    )
    conn.commit()
    conn.close()
    return True, f"✅ Промокод активирован! +{fmt_money(reward)}", reward

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
        [InlineKeyboardButton(text="⭐ 1 Star = 1 Gold", callback_data="deposit_custom")],
        [InlineKeyboardButton(text="⭐ 10 Stars = 10 Gold", callback_data="deposit_10")],
        [InlineKeyboardButton(text="⭐ 50 Stars = 50 Gold", callback_data="deposit_50")],
        [InlineKeyboardButton(text="⭐ 100 Stars = 100 Gold", callback_data="deposit_100")],
        [InlineKeyboardButton(text="⭐ 500 Stars = 500 Gold", callback_data="deposit_500")],
        [InlineKeyboardButton(text="⭐ 1000 Stars = 1000 Gold", callback_data="deposit_1000")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
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

class CheckClaimStates(StatesGroup):
    waiting_code = State()

class PromoStates(StatesGroup):
    waiting_code = State()

class BetStates(StatesGroup):
    waiting_amount = State()
    waiting_crash_mult = State()

class DepositStates(StatesGroup):
    waiting_custom_amount = State()

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
    await message.answer_sticker("CAACAgIAAxkBAAEI3Ppm-t0AAcwFpwGZtsqH0outXE-Z670AAmUgAAKBfylK3PLk7j0nC4U2BA")
    await message.answer(
        f"🌟 <b>Добро пожаловать в Casino Bot!</b>\n\n"
        f"🎰 Твой баланс: {fmt_money(get_user(message.from_user.id)['coins'])}\n\n"
        f"👇 Используй кнопки ниже для игры:",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery):
    await call.message.edit_text(
        f"🌟 <b>Главное меню</b>\n\n💰 Баланс: {fmt_money(get_user(call.from_user.id)['coins'])}",
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
        f"🆔 ID: <code>{call.from_user.id}</code>\n"
        f"💰 Баланс: {fmt_money(user['coins'])}\n"
        f"💎 Всего пополнено: {fmt_money(user['total_deposited'] or 0)}\n"
        f"🎲 Всего ставок: {bets}\n"
        f"🏆 Побед: {wins} ({wr:.1f}%)\n"
        f"💎 Валюта: {CURRENCY_NAME}\n\n"
        f"📊 Ставки: от {fmt_money(MIN_BET)} до {fmt_money(MAX_BET)}",
        reply_markup=back_button()
    )
    await call.answer()

@dp.callback_query(F.data == "top")
async def top_cmd(call: CallbackQuery):
    players = get_top_players(10)
    text = "🏆 <b>Топ игроков</b>\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, p in enumerate(players):
        medal = medals[i] if i < 3 else f"{i+1}."
        text += f"{medal} {mention_user(int(p['user_id']))} — {fmt_money(p['coins'])}\n"
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
    
    reward = random.randint(100, 500)
    new_balance = update_balance(user_id, reward)
    conn = get_db()
    conn.execute("UPDATE users SET last_bonus = ? WHERE user_id = ?", (now, str(user_id)))
    conn.commit()
    conn.close()
    
    await call.message.edit_text(
        f"🎁 <b>Ежедневный бонус!</b>\n\n"
        f"✨ Ты получил: +{fmt_money(reward)}\n"
        f"💰 Новый баланс: {fmt_money(new_balance)}",
        reply_markup=back_button()
    )
    await call.answer()

# ========== ПОПОЛНЕНИЕ ==========
@dp.callback_query(F.data == "deposit")
async def deposit_menu_cmd(call: CallbackQuery):
    await call.message.edit_text(
        f"💎 <b>Пополнение баланса</b>\n\n"
        f"⭐ <b>Курс:</b> 1 Star = 1 Gold\n"
        f"💰 <b>Минимальная сумма:</b> {fmt_money(MIN_DEPOSIT)}\n\n"
        f"Выбери сумму пополнения:",
        reply_markup=deposit_menu()
    )
    await call.answer()

@dp.callback_query(F.data == "deposit_custom")
async def custom_deposit(call: CallbackQuery, state: FSMContext):
    await state.set_state(DepositStates.waiting_custom_amount)
    await call.message.edit_text(
        f"💎 <b>Введи сумму в Stars</b>\n\n"
        f"⭐ 1 Star = 1 Gold\n"
        f"💰 Минимальная сумма: {fmt_money(MIN_DEPOSIT)}\n\n"
        f"Пример: <code>10</code> или <code>100.50</code>",
        reply_markup=back_button()
    )
    await call.answer()

@dp.message(DepositStates.waiting_custom_amount)
async def process_custom_deposit(msg: Message, state: FSMContext):
    try:
        stars = float(msg.text.replace(",", "."))
        if stars < MIN_DEPOSIT:
            await msg.answer(f"❌ Минимальная сумма пополнения: {fmt_money(MIN_DEPOSIT)}")
            return
        
        stars_int = int(stars)
        gold = stars_int * STARS_TO_GOLD
        
        deposit_id = add_deposit_record(msg.from_user.id, stars_int, gold)
        
        prices = [LabeledPrice(label=f"{stars_int} Stars", amount=stars_int)]
        
        await msg.answer_invoice(
            title=f"💎 Пополнение {CURRENCY_NAME}",
            description=f"Получи {fmt_money(gold)} за {stars_int} Stars!\nКурс: 1 Star = 1 Gold",
            payload=f"deposit_{deposit_id}",
            provider_token="",
            currency="XTR",
            prices=prices,
            start_parameter="deposit"
        )
        await state.clear()
    except:
        await msg.answer("❌ Введи корректную сумму!")

@dp.callback_query(F.data.startswith("deposit_"))
async def create_stars_invoice(call: CallbackQuery):
    if call.data == "deposit_custom":
        return
    
    stars = int(call.data.split("_")[1])
    gold = stars * STARS_TO_GOLD
    
    deposit_id = add_deposit_record(call.from_user.id, stars, gold)
    
    prices = [LabeledPrice(label=f"{stars} Stars", amount=stars)]
    
    await call.message.answer_invoice(
        title=f"💎 Пополнение {CURRENCY_NAME}",
        description=f"Получи {fmt_money(gold)} за {stars} Stars!\nКурс: 1 Star = 1 Gold",
        payload=f"deposit_{deposit_id}",
        provider_token="",
        currency="XTR",
        prices=prices,
        start_parameter="deposit"
    )
    await call.answer()

@dp.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    payment = message.successful_payment
    payload = payment.invoice_payload
    
    if payload.startswith("deposit_"):
        deposit_id = int(payload.split("_")[1])
        stars = payment.total_amount
        gold = stars * STARS_TO_GOLD
        
        new_balance = update_balance(message.from_user.id, gold)
        mark_deposit_paid(deposit_id, message.from_user.id)
        
        await message.answer(
            f"✅ <b>Пополнение успешно!</b>\n\n"
            f"⭐ Оплачено: {stars} Stars\n"
            f"💰 Получено: {fmt_money(gold)}\n"
            f"💎 Новый баланс: {fmt_money(new_balance)}\n\n"
            f"🎮 Приятной игры!",
            reply_markup=main_menu()
        )
        
        await message.bot.send_message(
            ADMIN_ID,
            f"💎 <b>Новое пополнение!</b>\n\n"
            f"👤 Пользователь: {mention_user(message.from_user.id, message.from_user.first_name)}\n"
            f"⭐ Stars: {stars}\n"
            f"💰 Gold: {fmt_money(gold)}"
        )

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
    await state.set_state(CheckCreateStates.waiting_amount)
    await call.message.edit_text(
        f"💸 <b>Создание чека</b>\n\n"
        f"Введи сумму для одной активации (мин. {fmt_money(MIN_BET)}):",
        reply_markup=back_button()
    )
    await call.answer()

@dp.message(CheckCreateStates.waiting_amount)
async def check_amount(msg: Message, state: FSMContext):
    try:
        amount = float(msg.text.replace(",", "."))
        if amount < MIN_BET:
            await msg.answer(f"❌ Минимальная сумма: {fmt_money(MIN_BET)}")
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
        
        ok, result = create_check(msg.from_user.id, amount, count)
        await state.clear()
        if ok:
            await msg.answer(
                f"✅ <b>Чек создан!</b>\n\n"
                f"🎫 Код: <code>{result}</code>\n"
                f"💰 Сумма: {fmt_money(amount)}\n"
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
    ok, result, reward = claim_check(msg.from_user.id, code)
    await state.clear()
    if ok:
        await msg.answer(f"✅ {result}\n💰 Новый баланс: {fmt_money(get_user(msg.from_user.id)['coins'])}")
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
            text += f"🎫 <code>{c['code']}</code> | {fmt_money(c['per_user'])} | осталось: {c['remaining']}\n"
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
    ok, result, reward = redeem_promo(msg.from_user.id, code)
    await state.clear()
    if ok:
        await msg.answer(f"🎉 {result}\n💰 Новый баланс: {fmt_money(get_user(msg.from_user.id)['coins'])}")
    else:
        await msg.answer(f"❌ {result}")

# ========== АДМИН КОМАНДЫ ==========
@dp.message(Command("addpromo"))
async def add_promo(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.answer("⛔ Только для админа!")
        return
    parts = msg.text.split()
    if len(parts) != 4:
        await msg.answer("📝 Формат: /addpromo КОД СУММА АКТИВАЦИИ")
        return
    code = parts[1].upper()
    try:
        reward = float(parts[2])
        activations = int(parts[3])
        create_promo(code, reward, activations)
        await msg.answer(f"✅ Промокод создан!\n🎫 {code}\n💰 {fmt_money(reward)}\n🎯 {activations} активаций")
    except:
        await msg.answer("❌ Ошибка! Пример: /addpromo GOLD100 100 50")

@dp.message(Command("give"))
async def give_money(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.answer("⛔ Только для админа!")
        return
    parts = msg.text.split()
    if len(parts) != 3:
        await msg.answer("📝 Формат: /give ID СУММА")
        return
    try:
        target_id = int(parts[1])
        amount = float(parts[2])
        new_balance = update_balance(target_id, amount)
        await msg.answer(f"✅ Выдано {fmt_money(amount)} пользователю {target_id}\n💰 Новый баланс: {fmt_money(new_balance)}")
    except:
        await msg.answer("❌ Ошибка! Пример: /give 123456789 1000")

# ========== ИГРЫ ==========
@dp.callback_query(F.data == "games")
async def games_list(call: CallbackQuery):
    await call.message.edit_text(
        "🎮 <b>Выбери игру</b>\n\n"
        f"💰 Ставки: от {fmt_money(MIN_BET)} до {fmt_money(MAX_BET)}",
        reply_markup=games_menu()
    )
    await call.answer()

@dp.callback_query(F.data == "game_roulette")
async def roulette_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(BetStates.waiting_amount)
    await state.update_data(game="roulette")
    await call.message.edit_text(
        f"🎡 <b>Рулетка</b>\n\n"
        f"Введи сумму ставки (от {fmt_money(MIN_BET)} до {fmt_money(MAX_BET)}):",
        reply_markup=back_button()
    )
    await call.answer()

@dp.callback_query(F.data == "game_crash")
async def crash_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(BetStates.waiting_amount)
    await state.update_data(game="crash")
    await call.message.edit_text(
        f"📈 <b>Краш</b>\n\n"
        f"Введи сумму ставки (от {fmt_money(MIN_BET)} до {fmt_money(MAX_BET)}):",
        reply_markup=back_button()
    )
    await call.answer()

@dp.callback_query(F.data == "game_cube")
async def cube_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(BetStates.waiting_amount)
    await state.update_data(game="cube")
    await call.message.edit_text(
        f"🎲 <b>Кубик</b>\n\n"
        f"Введи сумму ставки (от {fmt_money(MIN_BET)} до {fmt_money(MAX_BET)}):\n"
        f"Угадай число 1-6 → x3.5",
        reply_markup=back_button()
    )
    await call.answer()

@dp.callback_query(F.data == "game_dice")
async def dice_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(BetStates.waiting_amount)
    await state.update_data(game="dice")
    await call.message.edit_text(
        f"🎯 <b>Кости</b>\n\n"
        f"Введи сумму ставки (от {fmt_money(MIN_BET)} до {fmt_money(MAX_BET)}):\n"
        f"Ставки: больше 7 (x1.9), меньше 7 (x1.9), равно 7 (x5.0)",
        reply_markup=back_button()
    )
    await call.answer()

@dp.callback_query(F.data == "game_football")
async def football_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(BetStates.waiting_amount)
    await state.update_data(game="football")
    await call.message.edit_text(
        f"⚽ <b>Футбол</b>\n\n"
        f"Введи сумму ставки (от {fmt_money(MIN_BET)} до {fmt_money(MAX_BET)}):\n"
        f"Автоматический бросок, гол → x1.85",
        reply_markup=back_button()
    )
    await call.answer()

@dp.callback_query(F.data == "game_basket")
async def basket_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(BetStates.waiting_amount)
    await state.update_data(game="basket")
    await call.message.edit_text(
        f"🏀 <b>Баскетбол</b>\n\n"
        f"Введи сумму ставки (от {fmt_money(MIN_BET)} до {fmt_money(MAX_BET)}):\n"
        f"Автоматический бросок, точный → x1.85",
        reply_markup=back_button()
    )
    await call.answer()

# Обработка ставок
@dp.message(BetStates.waiting_amount)
async def process_bet(msg: Message, state: FSMContext):
    try:
        bet = float(msg.text.replace(",", "."))
        
        if bet < MIN_BET:
            await msg.answer(f"❌ Минимальная ставка: {fmt_money(MIN_BET)}")
            return
        if bet > MAX_BET:
            await msg.answer(f"❌ Максимальная ставка: {fmt_money(MAX_BET)}")
            return
        
        user = get_user(msg.from_user.id)
        if user["coins"] < bet:
            await msg.answer(f"❌ Недостаточно средств! Твой баланс: {fmt_money(user['coins'])}")
            return
        
        data = await state.get_data()
        game = data["game"]
        
        # Футбол
        if game == "football":
            result = await msg.answer_dice(emoji="⚽")
            value = result.dice.value
            win = value >= 4
            payout = bet * 1.85 if win else 0
            new_balance = update_balance(msg.from_user.id, -bet + payout)
            add_bet_record(msg.from_user.id, bet, win, "football")
            
            outcome = "ГОЛ 🎉" if win else "МИМО 😔"
            await msg.answer(
                f"⚽ <b>Футбол</b>\n\n"
                f"🎲 Результат: <b>{outcome}</b>\n"
                f"💰 Ставка: {fmt_money(bet)}\n"
                f"{'🎉' if win else '😔'} Итог: <b>{'ПОБЕДА' if win else 'ПРОИГРЫШ'}</b>\n"
                f"💸 Выплата: {fmt_money(payout)}\n"
                f"💎 Новый баланс: {fmt_money(new_balance)}"
            )
            await state.clear()
            return
        
        # Баскетбол
        if game == "basket":
            result = await msg.answer_dice(emoji="🏀")
            value = result.dice.value
            win = value in [4, 5]
            payout = bet * 1.85 if win else 0
            new_balance = update_balance(msg.from_user.id, -bet + payout)
            add_bet_record(msg.from_user.id, bet, win, "basket")
            
            outcome = "ТОЧНЫЙ БРОСОК 🎉" if win else "ПРОМАХ 😔"
            await msg.answer(
                f"🏀 <b>Баскетбол</b>\n\n"
                f"🎲 Результат: <b>{outcome}</b>\n"
                f"💰 Ставка: {fmt_money(bet)}\n"
                f"{'🎉' if win else '😔'} Итог: <b>{'ПОБЕДА' if win else 'ПРОИГРЫШ'}</b>\n"
                f"💸 Выплата: {fmt_money(payout)}\n"
                f"💎 Новый баланс: {fmt_money(new_balance)}"
            )
            await state.clear()
            return
        
        # Кубик
        if game == "cube":
            await state.update_data(bet=bet)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="1️⃣", callback_data="cube_1"), InlineKeyboardButton(text="2️⃣", callback_data="cube_2"),
                 InlineKeyboardButton(text="3️⃣", callback_data="cube_3")],
                [InlineKeyboardButton(text="4️⃣", callback_data="cube_4"), InlineKeyboardButton(text="5️⃣", callback_data="cube_5"),
                 InlineKeyboardButton(text="6️⃣", callback_data="cube_6")],
                [InlineKeyboardButton(text="◀️ Отмена", callback_data="games")]
            ])
            await msg.answer("🎲 Угадай число:", reply_markup=kb)
            await state.clear()
            return
        
        # Кости
        if game == "dice":
            await state.update_data(bet=bet)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📈 Больше 7 (x1.9)", callback_data="dice_high")],
                [InlineKeyboardButton(text="📉 Меньше 7 (x1.9)", callback_data="dice_low")],
                [InlineKeyboardButton(text="🎯 Равно 7 (x5.0)", callback_data="dice_seven")],
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
                [InlineKeyboardButton(text="🔴 Красное (x2)", callback_data="roulette_red"),
                 InlineKeyboardButton(text="⚫ Чёрное (x2)", callback_data="roulette_black")],
                [InlineKeyboardButton(text="2️⃣ Чёт (x2)", callback_data="roulette_even"),
                 InlineKeyboardButton(text="1️⃣ Нечет (x2)", callback_data="roulette_odd")],
                [InlineKeyboardButton(text="0️⃣ Зеро (x35)", callback_data="roulette_zero")],
                [InlineKeyboardButton(text="◀️ Отмена", callback_data="games")]
            ])
            await msg.answer("🎡 Выбери тип ставки:", reply_markup=kb)
            await state.clear()
            return
        
    except:
        await msg.answer("❌ Введи корректную сумму!")

# Краш - ввод множителя
@dp.message(BetStates.waiting_crash_mult)
async def process_crash_mult(msg: Message, state: FSMContext):
    try:
        mult = float(msg.text.replace(",", "."))
        if mult < 1.10 or mult > 10.00:
            await msg.answer("❌ Множитель должен быть от 1.10 до 10.00")
            return
        
        data = await state.get_data()
        bet = data["bet"]
        
        crash_mult = crash_game()
        win = crash_mult >= mult
        payout = bet * mult if win else 0
        new_balance = update_balance(msg.from_user.id, -bet + payout)
        add_bet_record(msg.from_user.id, bet, win, "crash")
        
        await msg.answer(
            f"📈 <b>Краш</b>\n\n"
            f"🎲 Множитель игры: <b>x{crash_mult:.2f}</b>\n"
            f"🎯 Твой множитель: <b>x{mult:.2f}</b>\n"
            f"{'🎉' if win else '😔'} Итог: <b>{'ПОБЕДА' if win else 'ПРОИГРЫШ'}</b>\n"
            f"💰 Ставка: {fmt_money(bet)}\n"
            f"💸 Выплата: {fmt_money(payout)}\n"
            f"💎 Новый баланс: {fmt_money(new_balance)}"
        )
        await state.clear()
    except:
        await msg.answer("❌ Введи корректный множитель!")

# Кубик
@dp.callback_query(F.data.startswith("cube_"))
async def cube_play(call: CallbackQuery, state: FSMContext):
    guess = int(call.data.split("_")[1])
    
    result = await call.message.answer_dice(emoji="🎲")
    value = result.dice.value
    win = guess == value
    payout = 0
    if win:
        bet = 100  # временно, нужно брать из состояния
        payout = bet * 3.5
    
    await call.message.answer(
        f"🎲 <b>Кубик</b>\n\n"
        f"🎯 Твой выбор: <b>{guess}</b>\n"
        f"🎲 Выпало: <b>{value}</b>\n"
        f"{'🎉' if win else '😔'} Итог: <b>{'ПОБЕДА' if win else 'ПРОИГРЫШ'}</b>\n"
        f"💸 Выплата: {fmt_money(payout)}"
    )
    await call.answer()

# Кости
@dp.callback_query(F.data.startswith("dice_"))
async def dice_play(call: CallbackQuery):
    choice = call.data.split("_")[1]
    
    d1 = await call.message.answer_dice(emoji="🎲")
    d2 = await call.message.answer_dice(emoji="🎲")
    total = d1.dice.value + d2.dice.value
    
    win = False
    mult = 0
    if choice == "high" and total > 7:
        win, mult = True, 1.9
    elif choice == "low" and total < 7:
        win, mult = True, 1.9
    elif choice == "seven" and total == 7:
        win, mult = True, 5.0
    
    await call.message.answer(
        f"🎯 <b>Кости</b>\n\n"
        f"🎲 Выпало: <b>{d1.dice.value}</b> + <b>{d2.dice.value}</b> = <b>{total}</b>\n"
        f"{'🎉' if win else '😔'} Итог: <b>{'ПОБЕДА' if win else 'ПРОИГРЫШ'}</b>"
    )
    await call.answer()

# Рулетка
@dp.callback_query(F.data.startswith("roulette_"))
async def roulette_play(call: CallbackQuery):
    choice = call.data.split("_")[1]
    win, mult, num, color = roulette_spin(choice)
    color_emoji = "🟢" if color == "green" else ("🔴" if color == "red" else "⚫")
    
    await call.message.answer(
        f"🎡 <b>Рулетка</b>\n\n"
        f"🎲 Выпало: <b>{num}</b> {color_emoji}\n"
        f"{'🎉' if win else '😔'} Итог: <b>{'ПОБЕДА' if win else 'ПРОИГРЫШ'}</b>\n"
        f"💰 Множитель: <b>x{mult}</b>"
    )
    await call.answer()

# ========== ЗАПУСК ==========
async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
