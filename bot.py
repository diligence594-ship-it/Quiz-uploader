import os
import re
import asyncio
from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# ============================================================
# FLASK SERVER - RENDER HEALTH CHECK
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
# BOT CONFIGURATION
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
# USER STATE
# ============================================================

USER_CHAT_CONFIG = {}
USER_ACTIVE_FILES = {}
STOP_TASKS = {}
AWAITING_CHAT_ID = set()


# ============================================================
# QUIZ PARSER
# ============================================================

def parse_quiz_file(file_content: str) -> list:
    """
    EXACT SUPPORTED FORMAT:

    Question | (A),(B),(C),(D) | B | Explanation

    Example:

    India ki capital kya hai? | (Mumbai),(Delhi),(Kolkata),(Chennai) | B | Delhi is the capital of India.

    Correct answer mapping:
    A = 0
    B = 1
    C = 2
    D = 3

    Options may also contain their labels:

    India ki capital kya hai? | (A) Mumbai,(B) Delhi,(C) Kolkata,(D) Chennai | B | Explanation
    """

    quizzes = []

    for line_no, line in enumerate(file_content.splitlines(), start=1):

        line = line.strip()

        if not line:
            continue

        if "|" not in line:
            continue

        # Split only on pipe.
        parts = [p.strip() for p in line.split("|")]

        if len(parts) < 3:
            print(f"[SKIP] Line {line_no}: less than 3 pipe sections")
            continue

        # --------------------------------------------------------
        # QUESTION
        # --------------------------------------------------------

        question = parts[0].strip()

        # Remove Q1., Q2), Q3: etc.
        question = re.sub(
            r"^\s*Q\s*\d+\s*[\.\):\-]\s*",
            "",
            question,
            flags=re.IGNORECASE
        ).strip()

        if not question:
            print(f"[SKIP] Line {line_no}: empty question")
            continue

        # --------------------------------------------------------
        # OPTIONS
        # --------------------------------------------------------

        raw_options = parts[1].strip()

        # Main format:
        # (A),(B),(C),(D)
        #
        # Also supports:
        # (A) Delhi,(B) Mumbai,(C) Kolkata,(D) Chennai
        #
        # And:
        # A) Delhi,B) Mumbai,C) Kolkata,D) Chennai

        option_parts = [
            x.strip()
            for x in raw_options.split(",")
            if x.strip()
        ]

        options = []

        for opt in option_parts:

            clean_opt = opt.strip()

            # Remove labels:
            # (A)
            # (B)
            # A)
            # B.
            # 1)
            # etc.
            clean_opt = re.sub(
                r"^\s*\(?\s*[A-Da-d1-4]\s*\)?\s*[\.\):\-]?\s*",
                "",
                clean_opt
            ).strip()

            # IMPORTANT:
            # If your file literally contains "(A),(B),(C),(D)",
            # after removing labels the options would become empty.
            #
            # In that case, the text inside the parentheses itself
            # is the option.
            if not clean_opt:
                label_match = re.search(
                    r"\(\s*([A-Da-d1-4])\s*\)",
                    opt
                )

                if label_match:
                    clean_opt = label_match.group(1).upper()

            if clean_opt:
                options.append(clean_opt)

        # --------------------------------------------------------
        # CORRECT ANSWER
        # --------------------------------------------------------

        raw_answer = parts[2].strip().upper()

        # Expected:
        # B
        #
        # Also supports:
        # B)
        # B.
        # Answer: B
        # Correct Answer: B
        # Option B
        # 2

        answer_match = re.search(
            r"(?:CORRECT\s*ANSWER|ANSWER|ANS|OPTION)?"
            r"\s*[:\-]?\s*[\(\[]?\s*([A-D1-4])\s*[\)\]]?",
            raw_answer,
            flags=re.IGNORECASE
        )

        if not answer_match:
            print(
                f"[SKIP] Line {line_no}: invalid correct answer -> "
                f"{raw_answer}"
            )
            continue

        answer_letter = answer_match.group(1).upper()

        answer_map = {
            "A": 0,
            "B": 1,
            "C": 2,
            "D": 3,
            "1": 0,
            "2": 1,
            "3": 2,
            "4": 3,
        }

        correct_option_id = answer_map.get(answer_letter)

        if correct_option_id is None:
            print(
                f"[SKIP] Line {line_no}: cannot map answer "
                f"{answer_letter}"
            )
            continue

        # --------------------------------------------------------
        # SPECIAL CASE FOR LITERAL:
        # (A),(B),(C),(D)
        # --------------------------------------------------------

        if len(options) < 2:

            label_only_matches = re.findall(
                r"\(\s*([A-Da-d])\s*\)",
                raw_options
            )

            if len(label_only_matches) >= 2:
                options = [
                    x.upper()
                    for x in label_only_matches
                ]

        # Telegram allows 2-10 options.
        if len(options) < 2:
            print(
                f"[SKIP] Line {line_no}: only "
                f"{len(options)} valid options found"
            )
            continue

        if len(options) > 10:
            options = options[:10]

        # Correct answer must exist in the actual options.
        if correct_option_id >= len(options):
            print(
                f"[SKIP] Line {line_no}: correct option "
                f"{answer_letter} index={correct_option_id}, "
                f"but only {len(options)} options exist"
            )
            continue

        # --------------------------------------------------------
        # EXPLANATION
        # --------------------------------------------------------

        explanation = ""

        if len(parts) >= 4:
            explanation = parts[3].strip()

        # Telegram explanation limit.
        explanation = explanation[:200]

        quiz = {
            "question": question,
            "options": options,
            "correct_option_id": correct_option_id,
            "explanation": explanation,
        }

        quizzes.append(quiz)

        # Debug output in Render logs.
        print(
            f"[PARSED Q{len(quizzes)}] "
            f"Answer={answer_letter} "
            f"correct_option_id={correct_option_id} "
            f"options={options}"
        )

    return quizzes


# ============================================================
# /START
# ============================================================

@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):

    user_id = message.from_user.id

    saved_chat = USER_CHAT_CONFIG.get(
        user_id,
        "Not Set"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⚙️ Save Chat ID",
                callback_data="btn_save_chat_id"
            )
        ]
    ])

    await message.reply_text(
        f"👋 **Welcome to Quiz Uploader Bot!**\n\n"
        f"🎯 **Saved Target Chat ID:** `{saved_chat}`\n\n"
        f"📌 **Instructions:**\n"
        f"1. Save Chat ID button par click karein.\n"
        f"2. Apni `.txt` quiz file bhejein.\n"
        f"3. `/quiz` command ya button se upload start karein.\n"
        f"4. `/stop` se running upload rok sakte hain.\n\n"
        f"📝 **TXT Format:**\n"
        f"`Question | (A),(B),(C),(D) | B | Explanation`",
        reply_markup=keyboard
    )


# ============================================================
# SAVE CHAT ID BUTTON
# ============================================================

@app.on_callback_query(
    filters.regex("^btn_save_chat_id$")
)
async def cb_save_chat(
    client: Client,
    callback_query: CallbackQuery
):

    user_id = callback_query.from_user.id

    AWAITING_CHAT_ID.add(user_id)

    await callback_query.message.reply_text(
        "✏️ **Direct Target Chat ID Bhejein:**\n\n"
        "Example:\n"
        "`-1004399820534`"
    )

    await callback_query.answer()


# ============================================================
# DIRECT CHAT ID INPUT
# ============================================================

@app.on_message(
    filters.text &
    ~filters.command(["start", "quiz", "stop"])
)
async def handle_direct_chat_id_input(
    client: Client,
    message: Message
):

    user_id = message.from_user.id

    if user_id not in AWAITING_CHAT_ID:
        return

    raw_input = message.text.strip()

    try:

        chat_id_int = int(raw_input)

        USER_CHAT_CONFIG[user_id] = chat_id_int

        AWAITING_CHAT_ID.remove(user_id)

        await message.reply_text(
            f"✅ **Chat ID Successfully Saved!**\n\n"
            f"🎯 Target Chat ID:\n"
            f"`{chat_id_int}`\n\n"
            f"Ab `.txt` quiz file bhej sakte hain."
        )

    except ValueError:

        await message.reply_text(
            "❌ **Invalid Chat ID!**\n\n"
            "Sirf numeric Chat ID bhejein.\n"
            "Example: `-1004399820534`"
        )


# ============================================================
# DOCUMENT UPLOAD
# ============================================================

@app.on_message(filters.document)
async def handle_document_upload(
    client: Client,
    message: Message
):

    user_id = message.from_user.id

    file_name = message.document.file_name or ""

    if not file_name.lower().endswith(".txt"):

        await message.reply_text(
            "❌ Sirf `.txt` quiz files allowed hain."
        )

        return

    USER_ACTIVE_FILES[user_id] = message

    target_chat = USER_CHAT_CONFIG.get(user_id)

    if not target_chat:

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⚙️ Save Chat ID",
                    callback_data="btn_save_chat_id"
                )
            ]
        ])

        await message.reply_text(
            "⚠️ **Target Chat ID Set Nahi Hai!**\n\n"
            "Pehle Chat ID save karein.",
            reply_markup=keyboard
        )

        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🚀 Start Upload (/quiz)",
                callback_data="btn_trigger_quiz"
            )
        ]
    ])

    await message.reply_text(
        f"📄 **File Received:** `{file_name}`\n"
        f"🎯 **Target Chat:** `{target_chat}`\n\n"
        f"`/quiz` type karein ya button press karein.",
        reply_markup=keyboard
    )


# ============================================================
# QUIZ UPLOAD PROCESS
# ============================================================

async def start_quiz_process(
    client: Client,
    union_obj
):

    if isinstance(union_obj, CallbackQuery):

        message = union_obj.message
        user_id = union_obj.from_user.id

        await union_obj.answer()

    else:

        message = union_obj
        user_id = union_obj.from_user.id

    target_chat = USER_CHAT_CONFIG.get(user_id)

    doc_message = USER_ACTIVE_FILES.get(user_id)

    # --------------------------------------------------------
    # CHECK CHAT ID
    # --------------------------------------------------------

    if not target_chat:

        await message.reply_text(
            "❌ Target Chat ID set nahi hai!"
        )

        return

    # --------------------------------------------------------
    # CHECK FILE
    # --------------------------------------------------------

    if not doc_message:

        await message.reply_text(
            "❌ Koi `.txt` file nahi mili!"
        )

        return

    # --------------------------------------------------------
    # PREVENT DOUBLE UPLOAD
    # --------------------------------------------------------

    if STOP_TASKS.get(user_id) is True:

        await message.reply_text(
            "⚠️ Ek quiz upload already running hai."
        )

        return

    status_msg = await message.reply_text(
        "📥 **Downloading & Processing File...**"
    )

    file_path = None

    try:

        # ----------------------------------------------------
        # DOWNLOAD FILE
        # ----------------------------------------------------

        file_path = await doc_message.download()

        with open(
            file_path,
            "r",
            encoding="utf-8-sig",
            errors="replace"
        ) as f:

            content = f.read()

        # ----------------------------------------------------
        # PARSE QUIZZES
        # ----------------------------------------------------

        quizzes = parse_quiz_file(content)

        if not quizzes:

            await status_msg.edit_text(
                "❌ **Koi valid quiz nahi mili.**\n\n"
                "Expected format:\n"
                "`Question | (A),(B),(C),(D) | B | Explanation`"
            )

            return

        # ----------------------------------------------------
        # CHECK TARGET CHAT
        # ----------------------------------------------------

        try:

            target_chat_id = int(target_chat)

            await client.get_chat(target_chat_id)

        except Exception as e:

            await status_msg.edit_text(
                f"❌ **Peer Access Error:**\n"
                f"`{e}`\n\n"
                f"Bot ko target channel/group me Admin banayein."
            )

            return

        # ----------------------------------------------------
        # START
        # ----------------------------------------------------

        STOP_TASKS[user_id] = True

        success_count = 0
        failed_count = 0

        await status_msg.edit_text(
            f"🚀 **Uploading Started!**\n\n"
            f"📊 Total Questions: `{len(quizzes)}`\n"
            f"🎯 Target Chat: `{target_chat_id}`\n\n"
            f"🛑 `/stop` se upload rok sakte hain."
        )

        # ----------------------------------------------------
        # UPLOAD EACH QUIZ
        # ----------------------------------------------------

        for idx, quiz in enumerate(
            quizzes,
            start=1
        ):

            # Stop check
            if not STOP_TASKS.get(
                user_id,
                False
            ):

                await status_msg.edit_text(
                    f"🛑 **Upload Stopped!**\n\n"
                    f"📊 Posted: "
                    f"**{success_count}/{len(quizzes)}**"
                )

                return

            try:

                # ====================================================
                # IMPORTANT TELEGRAM QUIZ SETTINGS
                #
                # type="quiz"
                # correct_option_id = 0/1/2/3
                # explanation = text shown after answering
                # ====================================================

                await client.send_poll(
                    chat_id=target_chat_id,

                    question=f"{idx}. {quiz['question']}",

                    options=quiz["options"],

                    type="quiz",

                    correct_option_id=int(
                        quiz["correct_option_id"]
                    ),

                    explanation=quiz["explanation"],

                    is_anonymous=True
                )

                success_count += 1

                print(
                    f"[UPLOADED] Q{idx} | "
                    f"Correct ID = "
                    f"{quiz['correct_option_id']} | "
                    f"Explanation = "
                    f"{bool(quiz['explanation'])}"
                )

            except Exception as e:

                failed_count += 1

                print(
                    f"[ERROR] Q{idx}: {repr(e)}"
                )

                await status_msg.edit_text(
                    f"❌ **Upload Failed at Q{idx}!**\n\n"
                    f"Error:\n"
                    f"`{e}`\n\n"
                    f"📊 Uploaded: `{success_count}`\n"
                    f"⚠️ Failed: `{failed_count}`"
                )

                return

            # Delay to reduce flood risk.
            await asyncio.sleep(2.5)

        # ----------------------------------------------------
        # COMPLETED
        # ----------------------------------------------------

        await status_msg.edit_text(
            f"✅ **Upload Completed Successfully!**\n\n"
            f"📊 Total Questions Posted: "
            f"**{success_count}/{len(quizzes)}**\n"
            f"🎯 Destination: `{target_chat_id}`"
        )

    except Exception as e:

        print(
            f"[QUIZ PROCESS ERROR] {repr(e)}"
        )

        try:

            await status_msg.edit_text(
                f"❌ **Error:**\n`{e}`"
            )

        except Exception:
            pass

    finally:

        STOP_TASKS.pop(
            user_id,
            None
        )

        if (
            file_path and
            os.path.exists(file_path)
        ):

            try:
                os.remove(file_path)
            except Exception:
                pass


# ============================================================
# /QUIZ COMMAND
# ============================================================

@app.on_message(
    filters.command("quiz")
)
async def quiz_command(
    client: Client,
    message: Message
):

    await start_quiz_process(
        client,
        message
    )


# ============================================================
# QUIZ BUTTON
# ============================================================

@app.on_callback_query(
    filters.regex("^btn_trigger_quiz$")
)
async def quiz_callback(
    client: Client,
    callback_query: CallbackQuery
):

    await start_quiz_process(
        client,
        callback_query
    )


# ============================================================
# /STOP
# ============================================================

@app.on_message(
    filters.command("stop")
)
async def stop_quiz_process(
    client: Client,
    message: Message
):

    user_id = message.from_user.id

    if (
        user_id in STOP_TASKS and
        STOP_TASKS[user_id]
    ):

        STOP_TASKS[user_id] = False

        await message.reply_text(
            "🛑 **Stop Signal Sent!**\n\n"
            "Running upload process cancel ho raha hai..."
        )

    else:

        await message.reply_text(
            "⚠️ Koi active quiz upload process running nahi hai."
        )


# ============================================================
# RUN BOT
# ============================================================

if __name__ == "__main__":
    app.run()
