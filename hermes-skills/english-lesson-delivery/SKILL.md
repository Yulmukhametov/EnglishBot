---
name: english-lesson-delivery
description: Deliver English tutor responses to the authorized Telegram owner: four ordered messages for corrections, English voice plus hidden text for conversation, or one visible Russian text for clarification.
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

## Choose the response mode FIRST

Do not send four messages automatically on every turn. Choose according to the user's intent:
- `lesson` (default): an actual answer needing correction/explanation. feedback_ru contains ONLY
  analysis of that answer: errors, rule, corrected examples. Never add next-task instructions,
  a transition like "Теперь расскажи...", homework instructions or a new question there.
  The next task/question belongs ONLY in reply_en or gap_exercise (the English voice + hidden text).
- `english_only`: ordinary English conversation or a new task with no useful correction to give.
  Send one English voice followed by its hidden text. Do not invent a Russian intro to fill slots.
- `text_ru`: the learner asks "не понял задание", "объясни по-русски", "переведи задание",
  asks about a rule/word, settings, progress, or supplies onboarding preferences. Send ONE visible
  Russian text message. No voice generation, no hidden duplicate, no new English question.
  Unless the user explicitly requests more, explain only the current instruction/topic. Do not
  solve the exercise for them, assign the next task, or advance the diagnostic stage.

For text_ru, use this input shape (other lesson fields are forbidden):
{"request_id":"unique-clarification-id","mode":"text_ru","text_ru":"Расскажи, что ты делал вчера, в 3–5 предложениях. Если сложно, начни с одного."}
For english_only:
{"request_id":"unique-task-id","mode":"english_only","reply_en":"Tell me what you did yesterday."}
It can also take gap_exercise. Lesson keeps the existing input shape, optionally mode="lesson".
All modes use the same send-lesson executable, acknowledgements, lock and retry protection.
Do not draft/send the answer before invoking the delivery program. On success the program
returns already_delivered=true and final_response="NO_REPLY". End your turn with EXACTLY
NO_REPLY (no quotes, no markdown, no preamble or explanation). Hermes suppresses this token
for the owner DM configured by the installer. A prose paraphrase or delivery confirmation would
become an unwanted second response. A single-text clarification must remain one message.
If delivery failed or was unconfirmed, do not emit NO_REPLY: report the failure honestly, without
repeating the already-sent explanation or rerunning with a new request_id.

## Lesson preparation

Act as a patient English teacher. In lesson mode compose feedback_ru (Russian explanation with corrected English
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


## Natural spoken alternatives

For a meaningful free-form learner sentence, explain actual grammar/lexical errors AND, when
useful, suggest one everyday spoken version separately. Keep original_en verbatim. corrected_en
is the MINIMAL grammatical/lexical correction preserving meaning, tense, certainty and register;
do not mix optional stylistic rewrites into the error diff. If already correct, corrected_en
can equal original_en and feedback_ru should clearly say there is no actual error.

Use optional paired fields natural_en and natural_note_ru to show an alternative and briefly
explain what changes (contraction, shorter wording, common collocation, or register). The program
adds a separate "Как сказать в разговоре" block and speaks it with the first explanation. Do not
repeat the same natural alternative in feedback_ru. One alternative is enough, no slang for its
own sake. Preserve facts, politeness, emphasis and intended situation; avoid unsupported claims
about what all native speakers say. Adapt American/British variants to the learner's preference.

Example: "I will meet you at seven o'clock." is valid, including o'clock. A possible shorter,
casual variant is "I'll meet you at seven." Explain that o'clock is optional here, not obsolete
or incorrect. Don't cross it out as a grammar error. Keep a.m./p.m. or context when needed to
avoid ambiguity; naturalness is not permission to remove important time information.
For a correct sentence already natural, say so and omit a forced alternative. Single-word quiz
answers, requests for Russian clarification and gap-task instructions do not need a rewrite.


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

1. For the selected response mode, create a UTF-8 JSON file in a private temporary directory.
   For lesson mode use the shape below; for other modes use the shapes in Choose the response mode. Use a stable request_id for this ONE lesson (incoming Telegram
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
   For a useful spoken-style alternative to a learner sentence, add both fields:
   "natural_en":"I'll meet you at seven.",
   "natural_note_ru":"Исходный вариант правильный. Этот короче и подходит для непринуждённого разговора."
   They require original_en/corrected_en. Keep the full first text within its 4000-unit limit.
   For a fill-in-the-gap exercise, also include:
   "gap_exercise":{"sentence_en":"She usually _____ to work by bus.","options":["go","goes","going"]}
   Copy this object from the learner task when available. sentence_en must contain exactly one
   placeholder made of at least three underscores; the program displays it as _____. It builds
   the second text from the sentence/options, instead of reply_en, so no model paraphrase can
   turn the placeholder into the word "blank". The second voice has a 600 ms pause at that exact
   position and then reads the choices; it does not say "blank" or "underscore". The first Russian
   voice should explain that this pause is where the missing word belongs. Do not include the
   correct answer. The normal short gap between languages is a separate setting.
2. Run the executable `send-lesson` inside THIS skill directory:
   `<skill-directory>/send-lesson --input /absolute/path/to/lesson.json`
   Resolve the skill directory from this skill's actual location. Allow up to 180 seconds.
3. Read its JSON result. Success means all acknowledgements for the selected mode were recorded:
   lesson: four (voice feedback -> visible feedback text -> English voice -> hidden English text);
   english_only: two (English voice -> hidden English text); text_ru: one visible Russian text.
4. On success, do not repeat any text, MEDIA tags or audio in the assistant's final response.
   The tool already delivered the response. Your final response must be exactly NO_REPLY.
5. On error/timeout stop. Never call the same lesson with a new request_id automatically.
   A message may have been sent even if the acknowledgement was lost. Tell the user the
   confirmed count from the result/state if available; do not claim full delivery.
   A duplicate request with the same completed ID is safe and sends nothing again; incomplete
   attempts require inspection and explicit human instructions.
6. Delete only the temporary input file/directory you created. Keep the delivery state journal.

In lesson mode, the program prepares BOTH voice files before sending anything.
English-only prepares one file; text_ru does not invoke speech at all. Each Telegram call waits for a
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
