import os
import re
import asyncio
from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery


# ============================================================
# RENDER HEALTH CHECK
# ============================================================

web_app = Flask(__name__)

@web_app.route("/")
def health_check():
    return "Quiz Bot Active & Running!", 200


def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)


Thread(target=run_web_server, daemon=True).start()


# ============================================================
# BOT CONFIG
# ============================================================

API_ID = int(os.environ.get("API_ID", "12345678"))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")

app = Client(
    "quiz_uploader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)


# ============================================================
# STATE
# ============================================================

USER_CHAT_CONFIG = {}
USER_ACTIVE_FILES = {}
STOP_TASKS = {}
AWAITING_CHAT_ID = set()


# ============================================================
# PARSER
# ============================================================

def parse_options(option_text):
    """
    Parses options exactly like:

    A) अनुच्छेद 12-18, B) अनुच्छेद 14-18,
    C) अनुच्छेद 19-22, D) अनुच्छेद 23-24

    Returns ONLY the option text, in A/B/C/D order.
    """

    option_text = option_text.strip()

    # This regex finds A), B), C), D) regardless of spaces.
    matches = list(
        re.finditer(
            r"(?:^|,\s*)([A-Da-d])\s*[\)\.:\-]\s*",
            option_text
        )
    )

    if not matches:
        return []

    options = []

    for i, match in enumerate(matches):
        start = match.end()

        if i + 1 < len(matches):
            end = matches[i + 1].start()

            # Remove the comma immediately before next option.
            text = option_text[start:end].strip().rstrip(",").strip()
        else:
            text = option_text[start:].strip().rstrip(",").strip()

        if text:
            options.append(text)

    return options


def parse_answer(answer_text):
    """
    Converts:
        A -> 0
        B -> 1
        C -> 2
        D -> 3

    Also accepts:
        1/2/3/4
        Answer: B
        Correct Answer: B
        (B)
        B)
    """

    answer_text = answer_text.strip().upper()

    # First look specifically for A/B/C/D or 1/2/3/4.
    match = re.search(r"\b([A-D]|[1-4])\b", answer_text)

    if not match:
        match = re.search(r"([A-D]|[1-4])", answer_text)

    if not match:
        return None, None

    answer = match.group(1).upper()

    mapping = {
        "A": 0,
        "B": 1,
        "C": 2,
        "D": 3,
        "1": 0,
        "2": 1,
        "3": 2,
        "4": 3,
    }

    return answer, mapping.get(answer)


def parse_quiz_file(file_content):
    """
    REQUIRED FORMAT:

    Q1. Question | A) Option A, B) Option B, C) Option C, D) Option D | B | Explanation

    IMPORTANT:
    The answer field is converted directly:
        A = first option
        B = second option
        C = third option
        D = fourth option
    """

    quizzes = []

    for line_no, line in enumerate(file_content.splitlines(), start=1):

        line = line.strip()

        if not line or "|" not in line:
            continue

        # Exactly split the 4 logical sections.
        parts = [x.strip() for x in line.split("|", 3)]

        if len(parts) < 3:
            print(f"[SKIP {line_no}] Invalid pipe format")
            continue

        # --------------------------------------------------------
        # QUESTION
        # --------------------------------------------------------

        question = parts[0].strip()

        question = re.sub(
            r"^\s*Q\s*\d+\s*[\.\):\-]\s*",
            "",
            question,
            flags=re.IGNORECASE
        ).strip()

        if not question:
            print(f"[SKIP {line_no}] Empty question")
            continue

        # --------------------------------------------------------
        # OPTIONS
        # --------------------------------------------------------

        options = parse_options(parts[1])

        if len(options) != 4:
            print(
                f"[SKIP {line_no}] Expected 4 options, "
                f"found {len(options)}: {options}"
            )
            continue

        # --------------------------------------------------------
        # ANSWER
        # --------------------------------------------------------

        answer_letter, correct_option_id = parse_answer(parts[2])

        if correct_option_id is None:
            print(
                f"[SKIP {line_no}] Invalid answer: {parts[2]}"
            )
            continue

        # Make absolutely sure the ID is inside the options.
        if not 0 <= correct_option_id < len(options):
            print(
                f"[SKIP {line_no}] Invalid correct ID: "
                f"{correct_option_id}"
            )
            continue

        # --------------------------------------------------------
        # EXPLANATION
        # --------------------------------------------------------

        explanation = ""

        if len(parts) == 4:
            explanation = parts[3].strip()

        explanation = explanation[:200]

        quiz = {
            "question": question,
            "options": options,
            "correct_option_id": correct_option_id,
            "correct_answer": answer_letter,
            "explanation": explanation
        }

        quizzes.append(quiz)

        # VERY IMPORTANT DEBUG LOG
        print(
            f"[PARSED Q{len(quizzes)}] "
            f"ANSWER={answer_letter} | "
            f"CORRECT_ID={correct_option_id} | "
            f"OPTIONS={options}"
        )

    return quizzes


# ============================================================
# START
# ============================================================

@app.on_message(filters.command("start"))
async def start_cmd(client, message):

    user_id = message.from_user.id

    saved_chat = USER_CHAT_CONFIG.get(user_id, "Not Set")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⚙️ Save Chat ID",
                callback_data="save_chat_id"
            )
        ]
    ])

    await message.reply_text(
        f"👋 **Quiz Uploader Bot**\n\n"
        f"🎯 Saved Chat ID: `{saved_chat}`\n\n"
        f"📝 TXT format:\n"
        f"`Q1. Question | A) Option A, B) Option B, C) Option C, D) Option D | B | Explanation`\n\n"
        f"➡️ File bhejo → `/quiz`",
        reply_markup=keyboard
    )


# ============================================================
# SAVE CHAT ID
# ============================================================

@app.on_callback_query(filters.regex("^save_chat_id$"))
async def save_chat_id_button(client, callback_query):

    user_id = callback_query.from_user.id

    AWAITING_CHAT_ID.add(user_id)

    await callback_query.message.reply_text(
        "✏️ **Target Chat ID bhejein:**\n\n"
        "Example: `-1004399820534`"
    )

    await callback_query.answer()


# ============================================================
# CHAT ID INPUT
# ============================================================

@app.on_message(
    filters.text &
    ~filters.command(["start", "quiz", "stop"])
)
async def chat_id_input(client, message):

    user_id = message.from_user.id

    if user_id not in AWAITING_CHAT_ID:
        return

    try:

        chat_id = int(message.text.strip())

        USER_CHAT_CONFIG[user_id] = chat_id
        AWAITING_CHAT_ID.remove(user_id)

        await message.reply_text(
            f"✅ **Chat ID Saved!**\n\n"
            f"`{chat_id}`"
        )

    except ValueError:

        await message.reply_text(
            "❌ Invalid Chat ID.\n"
            "Example: `-1004399820534`"
        )


# ============================================================
# TXT FILE
# ============================================================

@app.on_message(filters.document)
async def document_upload(client, message):

    user_id = message.from_user.id

    file_name = message.document.file_name or ""

    if not file_name.lower().endswith(".txt"):

        await message.reply_text(
            "❌ Sirf `.txt` file allowed hai."
        )

        return

    USER_ACTIVE_FILES[user_id] = message

    target_chat = USER_CHAT_CONFIG.get(user_id)

    if not target_chat:

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⚙️ Save Chat ID",
                    callback_data="save_chat_id"
                )
            ]
        ])

        await message.reply_text(
            "⚠️ Pehle Target Chat ID save karein.",
            reply_markup=keyboard
        )

        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🚀 Start Upload",
                callback_data="start_quiz"
            )
        ]
    ])

    await message.reply_text(
        f"📄 File: `{file_name}`\n"
        f"🎯 Chat: `{target_chat}`\n\n"
        f"`/quiz` bhejein ya button press karein.",
        reply_markup=keyboard
    )


# ============================================================
# QUIZ PROCESS
# ============================================================

async def upload_quizzes(client, source):

    if isinstance(source, CallbackQuery):

        message = source.message
        user_id = source.from_user.id

        await source.answer()

    else:

        message = source
        user_id = source.from_user.id

    target_chat = USER_CHAT_CONFIG.get(user_id)
    document = USER_ACTIVE_FILES.get(user_id)

    if not target_chat:

        await message.reply_text(
            "❌ Target Chat ID set nahi hai."
        )

        return

    if not document:

        await message.reply_text(
            "❌ Pehle `.txt` file bhejein."
        )

        return

    if STOP_TASKS.get(user_id):

        await message.reply_text(
            "⚠️ Upload already running hai."
        )

        return

    status = await message.reply_text(
        "📥 **File download ho rahi hai...**"
    )

    file_path = None

    try:

        file_path = await document.download()

        with open(
            file_path,
            "r",
            encoding="utf-8-sig",
            errors="replace"
        ) as f:
            content = f.read()

        quizzes = parse_quiz_file(content)

        if not quizzes:

            await status.edit_text(
                "❌ Koi valid quiz parse nahi hui.\n\n"
                "Format:\n"
                "`Q1. Question | A) Option A, B) Option B, C) Option C, D) Option D | B | Explanation`"
            )

            return

        target_chat_id = int(target_chat)

        try:
            await client.get_chat(target_chat_id)
        except Exception as e:
            await status.edit_text(
                f"❌ **Target Chat Error:**\n`{e}`\n\n"
                f"Bot ko channel/group me admin banayein."
            )
            return

        STOP_TASKS[user_id] = True

        success = 0
        failed = 0

        await status.edit_text(
            f"🚀 **Upload Started**\n\n"
            f"📊 Questions: `{len(quizzes)}`\n"
            f"🎯 Target: `{target_chat_id}`"
        )

        # --------------------------------------------------------
        # SEND POLLS
        # --------------------------------------------------------

        for index, quiz in enumerate(quizzes, start=1):

            if not STOP_TASKS.get(user_id, False):

                await status.edit_text(
                    f"🛑 **Upload Stopped**\n\n"
                    f"Posted: `{success}/{len(quizzes)}`"
                )

                return

            try:

                correct_id = int(
                    quiz["correct_option_id"]
                )

                options = quiz["options"]

                # ------------------------------------------------
                # FINAL SAFETY CHECK
                # ------------------------------------------------

                if correct_id < 0 or correct_id >= len(options):
                    raise ValueError(
                        f"Invalid correct_option_id={correct_id} "
                        f"for {len(options)} options"
                    )

                print(
                    "--------------------------------------------------"
                )
                print(
                    f"SENDING Q{index}"
                )
                print(
                    f"Question: {quiz['question']}"
                )
                print(
                    f"Options: {options}"
                )
                print(
                    f"Answer: {quiz['correct_answer']}"
                )
                print(
                    f"Correct Option ID: {correct_id}"
                )
                print(
                    f"Explanation: {quiz['explanation']}"
                )
                print(
                    "--------------------------------------------------"
                )

                # ==================================================
                # TELEGRAM QUIZ POLL
                #
                # A -> correct_option_id 0
                # B -> correct_option_id 1
                # C -> correct_option_id 2
                # D -> correct_option_id 3
                # ==================================================

                await client.send_poll(
                    chat_id=target_chat_id,
                    question=f"{index}. {quiz['question']}",
                    options=options,
                    type="quiz",
                    is_anonymous=True,
                    correct_option_id=correct_id,
                    explanation=quiz["explanation"]
                )

                success += 1

            except Exception as e:

                failed += 1

                print(
                    f"[UPLOAD ERROR Q{index}] "
                    f"{repr(e)}"
                )

                await status.edit_text(
                    f"❌ **Upload Failed at Q{index}**\n\n"
                    f"`{e}`\n\n"
                    f"✅ Uploaded: `{success}`\n"
                    f"❌ Failed: `{failed}`"
                )

                return

            await asyncio.sleep(2.5)

        await status.edit_text(
            f"✅ **Upload Completed!**\n\n"
            f"📊 Uploaded: **{success}/{len(quizzes)}**\n"
            f"❌ Failed: **{failed}**\n"
            f"🎯 Target: `{target_chat_id}`"
        )

    except Exception as e:

        print(
            f"[MAIN ERROR] {repr(e)}"
        )

        try:
            await status.edit_text(
                f"❌ **Error:**\n`{e}`"
            )
        except Exception:
            pass

    finally:

        STOP_TASKS.pop(user_id, None)

        if file_path and os.path.exists(file_path):

            try:
                os.remove(file_path)
            except Exception:
                pass


# ============================================================
# /QUIZ
# ============================================================

@app.on_message(filters.command("quiz"))
async def quiz_command(client, message):

    await upload_quizzes(
        client,
        message
    )


# ============================================================
# BUTTON
# ============================================================

@app.on_callback_query(
    filters.regex("^start_quiz$")
)
async def quiz_button(client, callback_query):

    await upload_quizzes(
        client,
        callback_query
    )


# ============================================================
# /STOP
# ============================================================

@app.on_message(filters.command("stop"))
async def stop_command(client, message):

    user_id = message.from_user.id

    if STOP_TASKS.get(user_id):

        STOP_TASKS[user_id] = False

        await message.reply_text(
            "🛑 **Stop signal sent.**"
        )

    else:

        await message.reply_text(
            "⚠️ Koi upload running nahi hai."
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    app.run()
