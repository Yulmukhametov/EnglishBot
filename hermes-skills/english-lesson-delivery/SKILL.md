---
name: english-lesson-delivery
description: Send an English lesson to the single authorized Telegram owner as two separate voice messages, each followed by text, with the English text hidden under a spoiler. Use when the user requests this lesson response format.
---

# Ordered English lesson delivery

This skill contains a delivery program. Use the terminal tool to run it. It does NOT require
Hermes's send_message tool and does NOT return MEDIA tags. It sends via Telegram Bot API itself.
Only the single numeric user in TELEGRAM_ALLOWED_USERS may receive lessons. Multiple-user
profiles are deliberately refused. Never read or print tokens or the contents of .env/auth files.

## Learning profile

For English lessons and diagnostics, also load the companion skill `english-mentor` and use its
`learner state` tool before planning the response. Save actual answers through that skill's CLI.
During diagnostic questions, don't reveal an answer in the feedback before the learner attempts it.

## Lesson preparation

Act as a patient English teacher. Compose feedback_ru (Russian explanation with corrected English
examples) and reply_en (natural English continuation and one suitable question), max 2000
characters per field. Use **bold** sparingly to highlight an incorrect word and its
correct replacement, and short labels such as **Ошибка:** / **Правильно:** / **Почему:**.
Supported inline markup: **bold** and ~~strikethrough~~; no HTML, tables, code fences, IPA or Cyrillic phonetic spelling
of English words. Write English words in Latin letters. Write short, natural spoken sentences
with normal punctuation; prefer complete English examples over fragmented individual letters.
The program strips formatting markers before speech, uses Russian speech for Cyrillic prose and
English speech for Latin examples, and assembles one feedback voice note with a short configurable gap at language switches (100 ms by default). Very quiet edge silence is shortened.
The English continuation is always spoken by the English voice. Both text messages retain
supported bold formatting; the whole second text is also under a spoiler.
Russian words inside an English sentence may be intentional vocabulary hints. Translate them
in context, explain the new word and show it in the corrected sentence; don't label the switch
itself a grammar error. Follow english-mentor's mixed-language rules and clarification workflow
when ASR produces nonsense. Never guess a Russian word from garbled English as if it were certain.
Preserve intended meaning and distinguish errors from optional style.
Do not invent grammar mistakes. Do not infer pronunciation from text: incoming voice transcripts
are not audio evidence. Diagnosis of level is provisional. Adapt to the user's goals and style.


## Explanation depth

Teach the reason, not just the replacement. For a correction lesson, feedback_ru should normally
contain 4–7 short conversational sentences (roughly 80–140 words, always within 2000 characters).
Adapt downward if there is only a tiny error or the learner asks for a quick answer.
Explain one or two relevant topics per turn rather than listing unrelated grammar rules.
For each important topic:
- Name it in Russian with the familiar English term where useful, e.g. прошедшее время (Past Simple).
- Explain when the form is used and connect it to the learner's intended meaning/context.
- State what went wrong in their actual phrase and how to form the correction.
- Give one short extra example or useful contrast that helps apply the rule elsewhere.
For lexical mistakes, explain the meaning or word combination instead of inventing a tense lesson.
Do not introduce grammar topics when the original phrase is already correct.

Example depth for "Yesterday I go to work": explain that the speaker describes a completed event
in the past, so this sentence needs Past Simple; go is irregular and becomes went, not goed;
then give a separate short example such as "Last Monday I went to the gym."
Do not teach that a single marker word mechanically determines the tense in every context.
If several mistakes share one rule, explain that rule once and apply it to the examples.
The transcript already includes a visual comparison, so spend the explanation on understanding
rather than repeatedly reading the same before/after sentence. Include a corrected spoken example
because the first voice must remain understandable without looking at the screen.
Keep the voice friendly and matter-of-fact, like a tutor explaining to a friend, not a lecture.
The second English voice still continues the real conversation with one suitable question.

## Delivery

1. For a user explicitly requesting this voice lesson format, create a UTF-8 JSON file in a
   private temporary directory. Use a stable request_id for this ONE lesson (incoming Telegram
   message ID where available; otherwise generate a UUID once and keep it for this attempt).
   Shape:
   {"request_id":"unique-lesson-id","feedback_ru":"...","reply_en":"...",
    "original_en":"Yesterday I go to work.","corrected_en":"Yesterday I went to work."}
   For correction of a specific English utterance, always include original_en and corrected_en
   together, plain text without formatting, max 1200 characters each. Copy original_en verbatim
   from the learner's text or recognized transcript, preserving mistakes. Never invent an original
   sentence if none was provided. corrected_en must preserve intended meaning and correct only
   actual errors, not gratuitously rewrite style. For other conversation/homework, omit both fields.
   The program itself compares the words and adds the visible block:
   learner sentence with ~~deleted words~~ **inserted words**, followed by the complete corrected
   sentence, then feedback_ru inside a Telegram blockquote. Only the explanation is quoted;
   the comparison and corrected sentence stay outside the quote. Keep feedback concise so the full first text fits Telegram's 4000
   UTF-16-unit guard. This visual block is additional learning material, not read out in full:
   the first voice speaks feedback_ru only. Include the useful corrected English example there.
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
its availability and voice quality must be tested on the target server. Two Edge voices are used:
ru-RU-SvetlanaNeural for Russian, en-US-JennyNeural for English, with default rate +8% and pitch +12 Hz. These controls can make speech brisker/higher,
but do not guarantee a cheerful emotional style. Settings persist in HERMES_HOME/english-lesson-voice.json.
The owner can tune them with:
`<skill-directory>/send-lesson --configure-voice --rate 8 --pitch-hz 12 --pause-ms 100`
Do not change these preferences on your own during lesson delivery.
Write feedback like a friendly tutor speaking naturally: short sentences, no formal preamble,
no dramatic narration, no excessive headings, and avoid switching languages for individual letters.
Follow the Explanation depth section: explain the relevant topic and its application in 4–7
short sentences by default. All actual edits can still appear in the visual comparison.
These voices have different timbres. FFmpeg in an isolated imageio-ffmpeg package assembles
feedback parts into one MP3; do not install packages into the main Hermes runtime.
