import os
import re
import asyncio
from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# --- FLASK SERVER FOR RENDER WEB SERVICE ---
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Quiz Uploader Bot is Alive!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

Thread(target=run_web, daemon=True).start()

# --- PYROGRAM BOT CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", "12345678"))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")

app = Client("quiz_uploader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# In-memory database to store chat per user
USER_CHAT_CONFIG = {}
USER_FILES = {}

# TXT Parsing Logic
def parse_quiz_file(file_content: str):
    quizzes = []
    blocks = re.split(r'\n\s*\n', file_content.strip())
    option_map = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'a': 0, 'b': 1, 'c': 2, 'd': 3}

    for block in blocks:
        lines = [l.strip() for l in block.split('\n') if l.strip()]
        if len(lines) < 6:
            continue
            
        question_text = ""
        options = []
        correct_id = None
        
        for line in lines:
            if re.match(r'^(Q\s*\d*[\).:]?|Q:?)\s*', line, re.IGNORECASE):
                question_text = re.sub(r'^(Q\s*\d*[\).:]?|Q:?)\s*', '', line, flags=re.IGNORECASE).strip()
            elif re.match(r'^[A-Da-d][\).:]\s*', line):
                opt_text = re.sub(r'^[A-Da-d][\).:]\s*', '', line).strip()
                options.append(opt_text)
            elif re.match(r'^(Ans|Answer)[\).:]\s*', line, re.IGNORECASE):
                ans_char = line.split(':')[-1].strip()
                correct_id = option_map.get(ans_char, 0)
                
        if question_text and len(options) == 4 and correct_id is not None:
            quizzes.append({
                "question": question_text,
                "options": options,
                "correct_option_id": correct_id
            })
            
    return quizzes

# Commands & Callbacks
@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    current_chat = USER_CHAT_CONFIG.get(user_id, "Not Set")
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Set Target Chat ID", callback_data="ask_chat_id")]
    ])
    
    await message.reply_text(
        f"**Welcome to Quiz Uploader Bot!**\n\n"
        f"📌 **Current Target Chat:** `{current_chat}`\n\n"
        f"**Steps to use:**\n"
        f"1. Click **Set Target Chat ID** or type `/setchat @username` or `-100123456789`.\n"
        f"2. Send your `.txt` quiz file.\n"
        f"3. Click **Upload Quiz** button!",
        reply_markup=keyboard
    )

@app.on_message(filters.command("setchat"))
async def set_chat_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("❌ Please specify Target Chat Username/ID.\nFormat: `/setchat @channel_username` or `/setchat -100123456789`")
        return
    
    target = message.command[1]
    USER_CHAT_CONFIG[message.from_user.id] = target
    await message.reply_text(f"✅ **Target Chat Set Successfully!**\nTarget: `{target}`")

@app.on_callback_query(filters.regex("ask_chat_id"))
async def ask_chat_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.message.reply_text(
        "✏️ **Target Chat Set Karne Ke Liye Command Bhejo:**\n\n"
        "`/setchat @channel_username`\n"
        "ya\n"
        "`/setchat -100123456789` (Group/Channel ID)"
    )
    await callback_query.answer()

@app.on_message(filters.document)
async def handle_document(client: Client, message: Message):
    user_id = message.from_user.id
    target_chat = USER_CHAT_CONFIG.get(user_id)

    if not message.document.file_name.endswith('.txt'):
        await message.reply_text("❌ Only `.txt` files supported.")
        return

    if not target_chat:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Set Target Chat ID", callback_data="ask_chat_id")]
        ])
        await message.reply_text(
            "⚠️ **Target Chat Set Nahi Hai!**\n\n"
            "Pehle neeche diye gaye button par click karke Channel/Group ID set karein, tabhi quiz upload hoga.",
            reply_markup=keyboard
        )
        return

    # Store file message reference
    USER_FILES[user_id] = message

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Start Upload Quiz", callback_data="start_upload")],
        [InlineKeyboardButton("⚙️ Change Chat ID", callback_data="ask_chat_id")]
    ])

    await message.reply_text(
        f"📄 **File Received:** `{message.document.file_name}`\n"
        f"🎯 **Target Chat:** `{target_chat}`\n\n"
        f"Quiz upload shuru karne ke liye neeche button par click karein:",
        reply_markup=keyboard
    )

@app.on_callback_query(filters.regex("start_upload"))
async def start_upload_callback(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    target_chat = USER_CHAT_CONFIG.get(user_id)
    doc_message = USER_FILES.get(user_id)

    if not target_chat:
        await callback_query.answer("❌ Target Chat Set Nahi Hai!", show_alert=True)
        return

    if not doc_message:
        await callback_query.answer("❌ File nahi mili, please firse `.txt` file bhejayein.", show_alert=True)
        return

    await callback_query.message.edit_text("📥 Downloading TXT file...")
    file_path = await doc_message.download()

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        await callback_query.message.edit_text("⚙️ Parsing Questions...")
        quizzes = parse_quiz_file(content)

        if not quizzes:
            await callback_query.message.edit_text("❌ File format galat hai ya koi question nahi mila.")
            os.remove(file_path)
            return

        await callback_query.message.edit_text(f"🚀 Found **{len(quizzes)}** questions. Uploading to `{target_chat}`...")

        success_count = 0
        for idx, q in enumerate(quizzes, start=1):
            try:
                await client.send_poll(
                    chat_id=target_chat,
                    question=f"{idx}. {q['question']}",
                    options=q['options'],
                    type="quiz",
                    correct_option_id=q['correct_option_id'],
                    is_anonymous=True
                )
                success_count += 1
                await asyncio.sleep(2.5)
            except Exception as e:
                print(f"Failed Q{idx}: {e}")
                await asyncio.sleep(3)

        await callback_query.message.edit_text(f"✅ **Process Complete!**\nPosted **{success_count}/{len(quizzes)}** Quizzes to `{target_chat}`.")

    except Exception as e:
        await callback_query.message.edit_text(f"❌ Error: `{str(e)}`")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

if __name__ == "__main__":
    app.run()