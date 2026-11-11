---
name: english-lesson-delivery
description: Send an English lesson to the single authorized Telegram owner as two separate voice messages, each followed by text, with the English text hidden under a spoiler. Use when the user requests this lesson response format.
---

# Ordered English lesson delivery

This skill contains a delivery program. Use the terminal tool to run it. It does NOT require
Hermes's send_message tool and does NOT return MEDIA tags. It sends via Telegram Bot API itself.
Only the single numeric user in TELEGRAM_ALLOWED_USERS may receive lessons. Multiple-user
profiles are deliberately refused. Never read or print tokens or the contents of .env/auth files.

## Lesson preparation

Act as a patient English teacher. Compose feedback_ru (Russian explanation with corrected English
examples) and reply_en (natural English continuation and one suitable question), max 2000
characters per field. Preserve intended meaning and distinguish errors from optional style.
Do not invent grammar mistakes. Do not infer pronunciation from text: incoming voice transcripts
are not audio evidence. Diagnosis of level is provisional. Adapt to the user's goals and style.

## Delivery

1. For a user explicitly requesting this voice lesson format, create a UTF-8 JSON file in a
   private temporary directory. Use a stable request_id for this ONE lesson (incoming Telegram
   message ID where available; otherwise generate a UUID once and keep it for this attempt).
   Shape:
   {"request_id":"unique-lesson-id","feedback_ru":"...","reply_en":"..."}
   Do not pass shell-interpolated prose. Write JSON using a file-writing tool or a quoted heredoc.
2. Run the executable `send-lesson` inside THIS skill directory:
   `<skill-directory>/send-lesson --input /absolute/path/to/lesson.json`
   Resolve the skill directory from this skill's actual location. Allow up to 180 seconds.
3. Read its JSON result. Success means exactly four Telegram acknowledgements have been recorded:
   voice feedback -> visible feedback text -> English voice -> English text under spoiler.
4. On success, do not repeat any text, MEDIA tags or audio in the assistant's final response.
   If the platform requires a final response, keep it to a short delivery confirmation.
5. On error/timeout stop. Never call the same lesson with a new request_id automatically.
   A message may have been sent even if the acknowledgement was lost. Tell the user the
   confirmed count from the result/state if available; do not claim full delivery.
   A duplicate request with the same completed ID is safe and sends nothing again; incomplete
   attempts require inspection and explicit human instructions.
6. Delete only the temporary input file/directory you created. Keep the delivery state journal.

The program prepares BOTH voice files before sending anything. Each Telegram call waits for a
valid returned message_id. This is confirmation of Telegram acceptance, not proof that the user
read or listened. No timing sleeps are used to guess delivery. All sequences using this tool
share one process lock. Other Hermes outputs/notifications can still arrive independently.

This skill is not a replacement for a complete learning database or validated pronunciation
assessment. It addresses message ordering. Edge TTS is used without an OpenAI audio API key;
its availability and voice quality must be tested on the target server.
