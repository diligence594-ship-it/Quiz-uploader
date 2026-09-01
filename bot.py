import os
import re
import asyncio
from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import (
    Message, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery
)

# --- DUMMY FLASK SERVER FOR RENDER WEB SERVICE HEALTH CHECK ---
web_app = Flask(__name__)

@web_app.route('/')
def health_check():
    return "Quiz Bot Active & Running!", 200

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# Run Flask in background thread
Thread(target=run_web_server, daemon=True).start()


# --- BOT CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", "12345678"))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")

app = Client("quiz_uploader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# In-Memory State Managers
USER_CHAT_CONFIG = {}       # Stores target chat_id per user
USER_ACTIVE_FILES = {}      # Stores uploaded document references
STOP_TASKS = {}             # Tracks active upload loops for /stop command


def parse_quiz_file(file_content: str) -> list:
    quizzes = []
    blocks = re.split(r'\n\s*\n', file_content.strip())
    
    option_map = {
        'A': 0, 'B': 1, 'C': 2, 'D': 3,
        'a': 0, 'b': 1, 'c': 2, 'd': 3,
        '1': 0, '2': 1, '3': 2, '4': 3
    }

    for block in blocks:
        lines = [line.strip() for line in block.split('\n') if line.strip()]
        if len(lines) < 5:
            continue

        question_text = ""
        options = []
        correct_id = None

        for line in lines:
            # Extract Question
            if re.match(r'^(Q\s*\d*[\).:]?|Q:?)\s*', line, re.IGNORECASE):
                question_text = re.sub(r'^(Q\s*\d*[\).:]?|Q:?)\s*', '', line, flags=re.IGNORECASE).strip()
            # Extract Options
            elif re.match(r'^[A-Da-d1-4][\).:]\s*', line):
                opt_text = re.sub(r'^[A-Da-d1-4][\).:]\s*', '', line).strip()
                options.append(opt_text)
            # Extract Correct Answer
            elif re.match(r'^(Ans|Answer)[\).:]\s*', line, re.IGNORECASE):
                ans_char = line.split(':')[-1].strip().upper()
                correct_id = option_map.get(ans_char, None)

        if not question_text and len(lines) >= 5 and len(options) >= 2:
            question_text = lines[0]

        if question_text and len(options) >= 2 and correct_id is not None:
            quizzes.append({
                "question": question_text,
                "options": options,
                "correct_option_id": correct_id
            })

    return quizzes


# --- COMMAND HANDLERS ---

@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    saved_chat = USER_CHAT_CONFIG.get(user_id, "Not Set")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Save Chat ID", callback_data="btn_save_chat_id")]
    ])

    await message.reply_text(
        f"👋 **Welcome to Quiz Uploader Bot!**\n\n"
        f"🎯 **Saved Target Chat ID:** `{saved_chat}`\n\n"
        f"📌 **Instructions:**\n"
        f"1. **Chat ID Set Karein:** Inline button par click karke direct Chat ID (e.g., `-1004399820534`) save karein.\n"
        f"2. `.txt` File Bhejein aur `/quiz` command se upload start karein.\n"
        f"3. Chalte hue upload ko rokne ke liye `/stop` type karein.",
        reply_markup=keyboard
    )


@app.on_message(filters.command("setchat"))
async def set_chat_command(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("❌ **Invalid Format!**\nUse: `/setchat -1004399820534`")
        return

    raw_chat_id = message.command[1].strip()

    try:
        chat_id_int = int(raw_chat_id)
        USER_CHAT_CONFIG[message.from_user.id] = chat_id_int
        await message.reply_text(f"✅ **Chat ID Successfully Saved!**\nTarget Chat ID: `{chat_id_int}`")
    except ValueError:
        await message.reply_text("❌ **Invalid ID!** Sirf numeric format enter karein (Jaise `-1004399820534`).")


@app.on_callback_query(filters.regex("btn_save_chat_id"))
async def cb_save_chat(client: Client, callback_query: CallbackQuery):
    await callback_query.message.reply_text(
        "✏️ **Direct Chat ID Set Karein:**\n\n"
        "Niche di gayi command copy karke apni ID ke sath bhejein:\n"
        "`/setchat -1004399820534`"
    )
    await callback_query.answer()


@app.on_message(filters.document)
async def handle_document_upload(client: Client, message: Message):
    user_id = message.from_user.id

    if not message.document.file_name.endswith('.txt'):
        await message.reply_text("❌ Sirf `.txt` extension wali quiz files allowed hain.")
        return

    USER_ACTIVE_FILES[user_id] = message
    target_chat = USER_CHAT_CONFIG.get(user_id)

    if not target_chat:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Save Chat ID", callback_data="btn_save_chat_id")]
        ])
        await message.reply_text(
            "⚠️ **Target Chat ID Set Nahi Hai!**\n\n"
            "Pehle Save Chat ID button par click karke valid Chat ID set karein.",
            reply_markup=keyboard
        )
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Start Upload (/quiz)", callback_data="btn_trigger_quiz")]
    ])

    await message.reply_text(
        f"📄 **File Received:** `{message.document.file_name}`\n"
        f"🎯 **Target Chat:** `{target_chat}`\n\n"
        f"Upload shuru karne ke liye `/quiz` type karein ya niche button par click karein.",
        reply_markup=keyboard
    )


@app.on_message(filters.command("quiz"))
@app.on_callback_query(filters.regex("btn_trigger_quiz"))
async def start_quiz_process(client: Client, union_obj):
    if isinstance(union_obj, CallbackQuery):
        message = union_obj.message
        user_id = union_obj.from_user.id
        await union_obj.answer()
    else:
        message = union_obj
        user_id = union_obj.from_user.id

    target_chat = USER_CHAT_CONFIG.get(user_id)
    doc_message = USER_ACTIVE_FILES.get(user_id)

    if not target_chat:
        await message.reply_text("❌ Target Chat ID set nahi hai! Use `/setchat -1004399820534` first.")
        return

    if not doc_message:
        await message.reply_text("❌ Koi `.txt` file nahi mili! Pehle file bhejayein.")
        return

    status_msg = await message.reply_text("📥 Downloading & Processing File...")
    file_path = await doc_message.download()

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        quizzes = parse_quiz_file(content)

        if not quizzes:
            await status_msg.edit_text("❌ File format invalid hai ya questions parse nahi ho paye.")
            os.remove(file_path)
            return

        await status_msg.edit_text(
            f"🚀 **Uploading Started!**\n"
            f"📊 Total Questions: `{len(quizzes)}`\n"
            f"🎯 Target Chat: `{target_chat}`\n\n"
            f"🛑 Upload rokne ke liye `/stop` command bhejein."
        )

        STOP_TASKS[user_id] = True
        success_count = 0

        for idx, q in enumerate(quizzes, start=1):
            if not STOP_TASKS.get(user_id, False):
                await status_msg.edit_text(f"🛑 Upload Stopped! Posted **{success_count}/{len(quizzes)}** Quizzes.")
                break

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
                print(f"Error Q{idx}: {e}")
                # Agar Channel me Bot Admin nahi hai to yeh error dikhayega
                await status_msg.edit_text(f"❌ **Upload Failed at Q{idx}!**\nError: `{str(e)}`\n\n Check karein ki bot target channel me Admin hai aur Send Polls permission active hai.")
                break

        if STOP_TASKS.get(user_id, False) and success_count == len(quizzes):
            await status_msg.edit_text(f"✅ **Upload Completed!**\nPosted **{success_count}/{len(quizzes)}** Quizzes to `{target_chat}`.")

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: `{str(e)}`")
    finally:
        STOP_TASKS.pop(user_id, None)
        if os.path.exists(file_path):
            os.remove(file_path)


@app.on_message(filters.command("stop"))
async def stop_quiz_process(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id in STOP_TASKS and STOP_TASKS[user_id]:
        STOP_TASKS[user_id] = False
        await message.reply_text("🛑 **Stop Signal Sent!** Running upload process cancel ho raha hai...")
    else:
        await message.reply_text("⚠️ Koi active quiz upload process running nahi hai.")


if __name__ == "__main__":
    app.run()