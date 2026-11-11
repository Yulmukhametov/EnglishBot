import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("fcntl", reason="Hermes delivery tool targets Linux VPS")

PATH = Path(__file__).parents[1] / 'hermes-skills/english-lesson-delivery/scripts/ordered_delivery.py'
spec = importlib.util.spec_from_file_location('ordered_delivery', PATH)
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


class Sender:
    chat_id = 42

    def __init__(self, failure=None):
        self.events = []
        self.failure = failure

    def send(self, event):
        self.events.append(event)
        if self.failure == len(self.events):
            raise TimeoutError('Secret token MUST NOT LEAK')
        return 100 + len(self.events)

    def send_voice(self, path):
        assert path.exists()
        return self.send(('voice', path.name))

    def send_text(self, text, hidden=False):
        return self.send(('text', text, hidden))


async def synth(payload, directory):
    files = [directory / 'feedback.mp3', directory / 'reply.mp3']
    for file in files:
        file.write_bytes(b'test audio')
    return files


@pytest.fixture
def payload():
    return dict(request_id='test-1', feedback_ru='Правильно: I went.', reply_en='Where did you go?')


def test_four_messages_in_order_with_idempotence(tmp_path, payload):
    sender = Sender()
    state = delivery.deliver(payload, tmp_path, sender, synth)
    assert sender.events == [
        ('voice', 'feedback.mp3'), ('text', payload['feedback_ru'], False),
        ('voice', 'reply.mp3'), ('text', payload['reply_en'], True)]
    assert state['message_ids'] == [101, 102, 103, 104]
    assert state['status'] == 'complete'
    delivery.deliver(payload, tmp_path, sender, synth)
    assert len(sender.events) == 4
    changed = dict(payload, reply_en='Different')
    with pytest.raises(delivery.DeliveryError, match='different text'):
        delivery.deliver(changed, tmp_path, sender, synth)


@pytest.mark.parametrize('failure', [1, 2, 3, 4])
def test_failure_stops_without_retry_or_following_messages(tmp_path, payload, failure):
    sender = Sender(failure)
    with pytest.raises(delivery.DeliveryError) as error:
        delivery.deliver(payload, tmp_path, sender, synth)
    assert 'Secret token' not in str(error.value)
    assert len(sender.events) == failure
    saved = json.loads((tmp_path / 'test-1.json').read_text())
    assert saved['message_ids'] == list(range(101, 100 + failure))
    assert saved['status'] == 'stopped_unconfirmed'
    with pytest.raises(delivery.DeliveryError, match='incomplete'):
        delivery.deliver(payload, tmp_path, sender, synth)
    assert len(sender.events) == failure


def test_generation_failure_sends_nothing(tmp_path, payload):
    async def broken(payload, directory):
        raise RuntimeError('provider error')
    sender = Sender()
    with pytest.raises(delivery.DeliveryError, match='preparation failed'):
        delivery.deliver(payload, tmp_path, sender, broken)
    assert sender.events == []


def test_parallel_delivery_cannot_interleave(tmp_path, payload):
    import fcntl
    with (tmp_path / 'delivery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sender = Sender()
        with pytest.raises(delivery.DeliveryError, match='Another lesson'):
            delivery.deliver(payload, tmp_path, sender, synth)
        assert sender.events == []


def test_telegram_text_escapes_html_and_hides_only_english(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"ok":true,"result":{"message_id":8,"chat":{"id":42}}}'

    def urlopen(request, timeout):
        requests.append(request.data.decode())
        return Response()
    monkeypatch.setattr(delivery.urllib.request, 'urlopen', urlopen)
    sender = delivery.Telegram('123:secret', 42)
    assert sender.send_text('a < b & c', hidden=True) == 8
    assert '<span class="tg-spoiler">a &lt; b &amp; c</span>' in requests[0]
    sender.send_text('**Visible**')
    assert 'tg-spoiler' not in requests[1]
    assert '<b>Visible</b>' in requests[1]
    sender.send_text('**went**', hidden=True)
    assert '<span class="tg-spoiler"><b>went</b></span>' in requests[2]


def test_invalid_ack_is_not_treated_as_delivery(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"ok":true,"result":{"message_id":8,"chat":{"id":99}}}'
    monkeypatch.setattr(delivery.urllib.request, 'urlopen', lambda *a, **k: Response())
    with pytest.raises(delivery.DeliveryError, match='acknowledgement invalid'):
        delivery.Telegram('123:secret', 42).send_text('Hi')


@pytest.mark.parametrize('allowed', ['42,99', '', '@someone'])
def test_credentials_refuse_multiple_or_unnamed_recipients(tmp_path, monkeypatch, allowed):
    for key in ('TELEGRAM_ALLOWED_USERS', 'TELEGRAM_ALLOW_ALL_USERS', 'TELEGRAM_BOT_TOKEN'):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / '.env').write_text(f'TELEGRAM_BOT_TOKEN=123:secret\nTELEGRAM_ALLOWED_USERS={allowed}\n')
    with pytest.raises(delivery.DeliveryError, match='exactly one numeric'):
        delivery.credentials(tmp_path)


def test_credentials_read_quoted_single_owner_without_exposing_token(tmp_path, monkeypatch):
    for key in ('TELEGRAM_ALLOWED_USERS', 'TELEGRAM_ALLOW_ALL_USERS', 'TELEGRAM_BOT_TOKEN'):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / '.env').write_text('TELEGRAM_BOT_TOKEN="123:secret"\nTELEGRAM_ALLOWED_USERS="42"\n')
    assert delivery.credentials(tmp_path) == ('123:secret', 42)


def test_bold_is_rendered_safely_and_not_read_aloud():
    text = 'Вместо **go** скажи **went**. <не тег> & **верно**'
    assert delivery.formatted_text(text) == 'Вместо <b>go</b> скажи <b>went</b>. &lt;не тег&gt; &amp; <b>верно</b>'
    assert delivery.spoken_text(text) == 'Вместо go скажи went. <не тег> & верно'
    assert delivery.formatted_text('**<script>**') == '<b>&lt;script&gt;</b>'
    assert delivery.formatted_text('Unclosed **bold') == 'Unclosed **bold'


def test_whole_english_phrases_use_english_voice():
    text = '**Ошибка:** Yesterday I go. **Правильно:** Yesterday I went. Объяснение: прошедшее время.'
    assert delivery.speech_segments(text) == [
        ('ru', 'Ошибка:'), ('en', 'Yesterday I go.'), ('ru', 'Правильно:'),
        ('en', 'Yesterday I went.'), ('ru', 'Объяснение: прошедшее время.')]
    assert delivery.speech_segments("Ты сказал I didn't go, это правильно.") == [
        ('ru', 'Ты сказал'), ('en', "I didn't go,"), ('ru', 'это правильно.')]


def test_pathological_switching_is_rejected():
    with pytest.raises(delivery.DeliveryError, match='Too many language'):
        delivery.speech_segments('да yes ' * 20)


async def test_synthesis_routes_languages_and_creates_valid_audio(tmp_path, monkeypatch):
    import shutil
    import subprocess
    import sys
    from types import SimpleNamespace
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('FFmpeg is required for audio assembly integration test')
    fixture = tmp_path / 'source.mp3'
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:sample_rate=24000:duration=0.16',
                    '-af', 'adelay=250,apad=pad_dur=0.25', '-c:a', 'libmp3lame', str(fixture)],
                   check=True)
    audio_bytes = fixture.read_bytes()
    calls = []

    class Communicate:
        def __init__(self, text, voice, rate, pitch):
            calls.append((text, voice, rate, pitch))

        async def save(self, path):
            Path(path).write_bytes(audio_bytes)
    monkeypatch.setitem(sys.modules, 'edge_tts', SimpleNamespace(Communicate=Communicate))
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', SimpleNamespace(get_ffmpeg_exe=lambda: ffmpeg))
    paths = await delivery.synthesize(dict(feedback_ru='Вместо **go** скажи **went**.',
                                           reply_en='**What happened?**'), tmp_path)
    assert calls == [
        ('Вместо', 'ru-RU-SvetlanaNeural', '+8%', '+12Hz'),
        ('go', 'en-US-JennyNeural', '+8%', '+12Hz'),
        ('скажи', 'ru-RU-SvetlanaNeural', '+8%', '+12Hz'),
        ('went.', 'en-US-JennyNeural', '+8%', '+12Hz'),
        ('What happened?', 'en-US-JennyNeural', '+8%', '+12Hz')]
    assert all(path.stat().st_size > 0 for path in paths)
    decoded = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(paths[0]),
                              '-f', 's16le', '-ac', '1', '-ar', '24000', '-'],
                             check=True, capture_output=True).stdout
    # Four 160 ms tones plus three 100 ms language gaps, allowing retained edge padding.
    assert 0.90 <= len(decoded) / (24000 * 2) <= 1.8


def test_inline_corrections_and_complete_sentence(tmp_path, payload):
    payload.update(original_en='Yesterday I go to work and buyed coffee.',
                   corrected_en='Yesterday I went to work and bought coffee.')
    text = delivery.feedback_display(payload)
    assert 'Yesterday I ~~go~~ **went** to work and ~~buyed~~ **bought** coffee.' in text
    assert '**Правильный вариант:**\nYesterday I went to work and bought coffee.' in text
    sender = Sender()
    delivery.deliver(payload, tmp_path, sender, synth)
    assert sender.events[1] == ('text', text, False)
    rendered = delivery.formatted_text(text)
    assert '<s>go</s> <b>went</b>' in rendered
    assert '<s>buyed</s> <b>bought</b>' in rendered


@pytest.mark.parametrize(('before', 'after', 'expected'), [
    ('I go school.', 'I go to school.', 'I go **to** school.'),
    ('I discuss about work.', 'I discuss work.', 'I discuss ~~about~~ work.'),
    ('I work.', 'I work.', 'I work.'),
])
def test_insertions_deletions_and_no_changes(payload, before, after, expected):
    payload.update(original_en=before, corrected_en=after)
    assert expected in delivery.feedback_display(payload)


def test_formatted_user_html_stays_escaped_and_strikes_are_not_spoken():
    text = '~~<script>~~ **went**'
    assert delivery.formatted_text(text) == '<s>&lt;script&gt;</s> <b>went</b>'
    assert delivery.spoken_text(text) == '<script> went'


def test_missing_sentence_pair_and_oversized_feedback_rejected(payload):
    with pytest.raises(delivery.DeliveryError, match='provided together'):
        delivery.validate(dict(payload, original_en='I go.'))
    with pytest.raises(delivery.DeliveryError, match='too long'):
        delivery.validate(dict(payload, feedback_ru='😀' * 2000, original_en='I go.', corrected_en='I went.'))


def test_quote_explanation_only_and_escape_html(payload):
    payload.update(original_en='I go yesterday.', corrected_en='I went yesterday.',
                   feedback_ru='**went** — прошедшее время.\nНе тег: <script>.')
    rendered = delivery.formatted_text(delivery.feedback_display(payload))
    assert '<s>go</s> <b>went</b>' in rendered
    assert '<blockquote><b>went</b> — прошедшее время.\nНе тег: &lt;script&gt;.</blockquote>' in rendered
    assert rendered.index('I went yesterday.') < rendered.index('<blockquote>')
    assert '<blockquote><blockquote>' not in delivery.formatted_text('> > nested')


def test_voice_settings_persist_and_reject_bad_values(tmp_path):
    assert delivery.speech_settings(tmp_path) == dict(rate_percent=8, pitch_hz=12, pause_ms=100)
    delivery.speech_settings(tmp_path, dict(pause_ms=80, rate_percent=10))
    assert delivery.speech_settings(tmp_path) == dict(rate_percent=10, pitch_hz=12, pause_ms=80)
    for change in [dict(pause_ms=-1), dict(pause_ms=401), dict(rate_percent=100), dict(pitch_hz='bad')]:
        with pytest.raises(delivery.DeliveryError, match='must be an integer'):
            delivery.speech_settings(tmp_path, change)
    assert delivery.speech_settings(tmp_path)['pause_ms'] == 80


def test_gap_display_is_literal_placeholder_and_not_word_blank(tmp_path, payload):
    payload['gap_exercise'] = dict(sentence_en='She usually ___ to work by bus.', options=['go', 'goes', 'going'])
    delivery.validate(payload)
    assert delivery.reply_display(payload) == '**Fill in the gap:**\nShe usually _____ to work by bus.\n\n**Options:** go / goes / going'
    sender = Sender()
    delivery.deliver(payload, tmp_path, sender, synth)
    assert sender.events[3] == ('text', delivery.reply_display(payload), True)
    assert 'blank' not in sender.events[3][1]


@pytest.mark.parametrize('sentence', ['She usually blank to work.', '___ goes ___', ''])
def test_gap_exercise_rejects_missing_or_multiple_placeholders(payload, sentence):
    payload['gap_exercise'] = dict(sentence_en=sentence, options=['go', 'goes'])
    with pytest.raises(delivery.DeliveryError, match='exactly one'):
        delivery.validate(payload)


async def test_gap_speech_uses_real_pause_not_spoken_placeholder(tmp_path, monkeypatch, payload):
    import sys
    from types import SimpleNamespace
    calls, joins = [], []

    class Communicate:
        def __init__(self, text, voice, rate, pitch):
            calls.append(text)

        async def save(self, path):
            Path(path).write_bytes(b'fake audio')

    async def combine(paths, destination, pause_ms=100):
        joins.append((destination.name, pause_ms))
        destination.write_bytes(b'joined audio')
    monkeypatch.setitem(sys.modules, 'edge_tts', SimpleNamespace(Communicate=Communicate))
    monkeypatch.setattr(delivery, 'combine_speech', combine)
    payload.update(feedback_ru='Вставь слово на месте паузы.',
                   gap_exercise=dict(sentence_en='She usually _____ to work by bus.', options=['go', 'goes', 'going']))
    await delivery.synthesize(payload, tmp_path)
    assert calls == ['Вставь слово на месте паузы.', 'Choose the word that fits the gap. She usually',
                     'to work by bus. Your options are: go, goes, going. Which word fits?']
    assert joins == [('reply.mp3', 600)]
    assert all('blank' not in part and '_____' not in part for part in calls)


def test_russian_clarification_is_one_visible_text_and_never_calls_speech(tmp_path):
    async def forbidden(*args):
        pytest.fail('Speech must not run for a Russian text clarification')
    payload = dict(request_id='clarify-1', mode='text_ru', text_ru='Расскажи, что ты делал вчера. Можно начать с одного предложения.')
    sender = Sender()
    state = delivery.deliver(payload, tmp_path, sender, forbidden)
    assert sender.events == [('text', payload['text_ru'], False)]
    assert state['message_ids'] == [101] and state['status'] == 'complete'
    delivery.deliver(payload, tmp_path, sender, forbidden)
    assert len(sender.events) == 1


def test_english_only_task_has_two_messages(tmp_path):
    payload = dict(request_id='task-1', mode='english_only', reply_en='What did you do yesterday?')
    sender = Sender()
    state = delivery.deliver(payload, tmp_path, sender, synth)
    assert sender.events == [('voice', 'reply.mp3'), ('text', payload['reply_en'], True)]
    assert state['status'] == 'complete'


async def test_english_only_synthesizes_no_russian_intro(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    calls = []

    class Communicate:
        def __init__(self, text, voice, **kwargs):
            calls.append((text, voice))

        async def save(self, path):
            Path(path).write_bytes(b'audio')
    monkeypatch.setitem(sys.modules, 'edge_tts', SimpleNamespace(Communicate=Communicate))
    payload = dict(request_id='task-2', mode='english_only', reply_en='Tell me about yesterday.')
    await delivery.synthesize(payload, tmp_path)
    assert calls == [('Tell me about yesterday.', 'en-US-JennyNeural')]
    assert not (tmp_path / 'feedback.mp3').exists()


@pytest.mark.parametrize('payload', [
    dict(request_id='x', mode='text_ru', text_ru='Hi', reply_en='Extra'),
    dict(request_id='x', mode='english_only', reply_en='Hi', feedback_ru='Extra'),
    dict(request_id='x', mode='text_ru', text_ru=''),
    dict(request_id='x', mode='invalid'),
])
def test_response_modes_reject_mixed_or_invalid_fields(payload):
    with pytest.raises(delivery.DeliveryError):
        delivery.validate(payload)


def test_text_only_failure_is_journaled_and_not_retried(tmp_path):
    payload = dict(request_id='clarify-error', mode='text_ru', text_ru='Уточнение.')
    sender = Sender(failure=1)
    with pytest.raises(delivery.DeliveryError, match='Stopped at step 1'):
        delivery.deliver(payload, tmp_path, sender)
    with pytest.raises(delivery.DeliveryError, match='incomplete'):
        delivery.deliver(payload, tmp_path, sender)
    assert len(sender.events) == 1
