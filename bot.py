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
AWAITING_CHAT_ID = set()    # Tracks users who clicked "Save Chat ID" button


def parse_quiz_file(file_content: str) -> list:
    """
    Parses pipe-separated quizzes where options are comma-separated inside part 1:
    Question | Opt1, Opt2, Opt3, Opt4 | Ans | Explanation
    """
    quizzes = []
    lines = file_content.strip().split('\n')

    option_map = {
        'A': 0, 'B': 1, 'C': 2, 'D': 3,
        '1': 0, '2': 1, '3': 2, '4': 3
    }

    for line in lines:
        line = line.strip()
        if not line or '|' not in line:
            continue

        parts = [p.strip() for p in line.split('|')]

        if len(parts) >= 3:
            # 1. Clean Question Text
            raw_q = parts[0]
            question_text = re.sub(r'^Q\d+\.\s*', '', raw_q)

            # 2. Extract Options (Handles comma-separated options in pipe part 1)
            raw_options_str = parts[1]
            raw_options_list = raw_options_str.split(',')

            options = []
            for opt in raw_options_list:
                # Remove prefixes like "A) ", "B) ", "1. ", "A. " cleanly
                clean_opt = re.sub(r'^[A-Da-d0-4][\.\)\-\s]\s*', '', opt.strip())
                if clean_opt:
                    options.append(clean_opt)

            # 3. Extract Correct Answer
            raw_ans = parts[2].strip().upper()
            ans_clean = re.sub(r'[^A-D1-4]', '', raw_ans)
            correct_id = option_map.get(ans_clean, 0)

            # 4. Extract Explanation (Optional)
            explanation = parts[3].strip() if len(parts) > 3 else ""

            # Validate options count (Telegram requires 2 to 10 options)
            if question_text and len(options) >= 2 and correct_id < len(options):
                quizzes.append({
                    "question": question_text,
                    "options": options,
                    "correct_option_id": correct_id,
                    "explanation": explanation[:200]
                })

    return quizzes


# --- COMMAND & EVENT HANDLERS ---

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
        f"1. **Chat ID Set Karein:** Button par click karein aur direct ID (e.g., `-1004399820534`) bhejein.\n"
        f"2. Pipe separated `.txt` File Bhejein (`Question | Opt 1, Opt 2, Opt 3, Opt 4 | Ans | Exp`).\n"
        f"3. `/quiz` command se upload start karein ya `/stop` se rokein.",
        reply_markup=keyboard
    )


@app.on_callback_query(filters.regex("btn_save_chat_id"))
async def cb_save_chat(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    AWAITING_CHAT_ID.add(user_id)
    await callback_query.message.reply_text(
        "✏️ **Direct Target Chat ID Bhejein:**\n\n"
        "Abhi bina kisi command ke seedha apni Chat ID enter karke bhej dein (Jaise: `-1004399820534`)."
    )
    await callback_query.answer()


@app.on_message(filters.text & ~filters.command(["start", "quiz", "stop"]))
async def handle_direct_chat_id_input(client: Client, message: Message):
    user_id = message.from_user.id

    if user_id in AWAITING_CHAT_ID:
        raw_input = message.text.strip()
        
        try:
            chat_id_int = int(raw_input)
            USER_CHAT_CONFIG[user_id] = chat_id_int
            AWAITING_CHAT_ID.remove(user_id)
            
            await message.reply_text(
                f"✅ **Chat ID Successfully Saved!**\n\n"
                f"🎯 **Target Chat ID:** `{chat_id_int}`\n\n"
                f"Ab aap apni `.txt` quiz file bhej sakte hain."
            )
        except ValueError:
            await message.reply_text("❌ **Invalid Format!** Kripya sirf numeric Chat ID bhejein (Jaise: `-1004399820534`).")


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
            "Pehle Save Chat ID button par click karke direct Chat ID set karein.",
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
        await message.reply_text("❌ Target Chat ID set nahi hai! Pehle Chat ID set karein.")
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
            await status_msg.edit_text("❌ File format invalid hai ya pipe (`|`) split questions parse nahi ho paye.")
            os.remove(file_path)
            return

        try:
            target_chat_id = int(target_chat)
            await client.get_chat(target_chat_id)
        except Exception as p_err:
            await status_msg.edit_text(f"❌ **Peer Access Error:** `{p_err}`\n\nBot ko channel me Admin banakar ek test message bhejein.")
            os.remove(file_path)
            return

        await status_msg.edit_text(
            f"🚀 **Uploading Started!**\n"
            f"📊 Total Questions: `{len(quizzes)}`\n"
            f"🎯 Target Chat: `{target_chat_id}`\n\n"
            f"🛑 Upload rokne ke liye `/stop` command bhejein."
        )

        STOP_TASKS[user_id] = True
        success_count = 0
        is_stopped = False

        for idx, q in enumerate(quizzes, start=1):
            if not STOP_TASKS.get(user_id, False):
                is_stopped = True
                await status_msg.edit_text(f"🛑 Upload Stopped! Posted **{success_count}/{len(quizzes)}** Quizzes.")
                break

            try:
                await client.send_poll(
                    chat_id=target_chat_id,
                    question=f"{idx}. {q['question']}",
                    options=q['options'],
                    type="quiz",
                    correct_option_id=int(q['correct_option_id']),
                    explanation=q.get('explanation', ''),
                    is_anonymous=True
                )
                success_count += 1
                await asyncio.sleep(2.5)
            except Exception as e:
                print(f"Error Q{idx}: {e}")
                await status_msg.edit_text(f"❌ **Upload Failed at Q{idx}!**\nError: `{str(e)}`")
                is_stopped = True
                break

        if not is_stopped:
            await status_msg.edit_text(
                f"✅ **Upload Completed Successfully!**\n\n"
                f"📊 Total Questions Posted: **{success_count}/{len(quizzes)}**\n"
                f"🎯 Destination: `{target_chat_id}`"
            )

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