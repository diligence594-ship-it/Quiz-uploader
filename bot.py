import os
import re
import asyncio
from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message

# --- FLASK DUMMY SERVER FOR RENDER HEALTH CHECK ---
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Quiz Uploader Bot is Alive!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# Start Flask in a background thread
Thread(target=run_web, daemon=True).start()

# --- PYROGRAM BOT LOGIC ---
API_ID = int(os.environ.get("API_ID", "12345678"))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")

app = Client("quiz_uploader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

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

@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "**Quiz Uploader Bot Active!**\n\n"
        "**Usage:**\n"
        "1. Send `.txt` file here.\n"
        "2. Caption / Command: `/upload @channel_username`"
    )

@app.on_message(filters.document & filters.command("upload"))
async def handle_quiz_upload(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("❌ Target channel username mention karo.\nFormat: `/upload @channel_username`")
        return

    target_chat = message.command[1]

    if not message.document.file_name.endswith('.txt'):
        await message.reply_text("❌ Only `.txt` files supported.")
        return

    status_msg = await message.reply_text("📥 Downloading TXT file...")
    file_path = await message.download()

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        await status_msg.edit_text("⚙️ Parsing Questions...")
        quizzes = parse_quiz_file(content)

        if not quizzes:
            await status_msg.edit_text("❌ No valid questions found in file.")
            os.remove(file_path)
            return

        await status_msg.edit_text(f"🚀 Found **{len(quizzes)}** questions. Uploading to `{target_chat}`...")

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

        await status_msg.edit_text(f"✅ Finished! Posted **{success_count}/{len(quizzes)}** Quizzes to `{target_chat}`.")

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: `{str(e)}`")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

if __name__ == "__main__":
    app.run()