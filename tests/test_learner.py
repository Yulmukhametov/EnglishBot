import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

PATH = Path(__file__).parents[1] / 'hermes-skills/english-mentor/scripts/learner.py'
spec = importlib.util.spec_from_file_location('learner', PATH)
learning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learning)


@pytest.fixture
def store(tmp_path):
    return learning.LearningStore(tmp_path / 'learner.sqlite3', 42)


def ready(store, minutes=15):
    store.set_profile(dict(goal='Travel', minutes_per_day=minutes, interests=['music'], style='casual'))


def answer(task, response, **extra):
    return dict(event_id='answer-' + task, phase='diagnostic', task_id=task, response=response,
                source='text', notes='', errors=[], **extra)


def complete(store, *, assisted=False, decline=False):
    ready(store)
    examples = [('introduction', 'I work in sales.'), ('present_simple', 'goes'),
                ('past_story', 'Yesterday I go to work.'), ('past_simple', 'learned'),
                ('listening_short', 'Tuesday'), ('opinion', 'I prefer friends because it is fun.')]
    for task, response in examples:
        item = answer(task, response)
        if task == 'past_story':
            item['source'] = 'voice_transcript'
            item['errors'] = [dict(topic='past_simple', original='I go', corrected='I went',
                                   explanation='Yesterday calls for a past event in this sentence.')]
        if task == 'listening_short':
            item['used_transcript'] = assisted
        if decline:
            item.update(response='Не знаю', errors=[], declined=True)
        store.record(item)


def assessment(**extra):
    return dict(level='A2', confidence='low', summary='Preliminary estimate from actual short tasks.',
                strengths=['Can express basic ideas'], priorities=['Past tense'],
                evidence_ids=['answer-introduction', 'answer-past_story', 'answer-opinion'], **extra)


def test_onboarding_and_preferences_survive_restart(store):
    assert store.next_task()['stage'] == 'onboarding'
    store.set_profile(dict(goal='Work'))
    assert store.next_task()['stage'] == 'onboarding'  # Daily time must be confirmed.
    store.set_profile(dict(minutes_per_day=20, style='strict'))
    assert store.next_task()['task']['id'] == 'introduction'
    reopened = learning.LearningStore(store.path, 42)
    state = reopened.state()
    assert state['profile']['goal'] == 'Work'
    assert state['profile']['minutes_per_day'] == 20
    assert state['profile']['style'] == 'strict'
    assert state['profile']['assessment'] is None


def test_task_has_no_answer_keys_and_requires_order(store):
    ready(store)
    task = store.next_task()['task']
    assert 'answers' not in task
    with pytest.raises(learning.LearningError, match='current diagnostic'):
        store.record(answer('present_simple', 'goes'))
    assert store.state()['answers_recorded'] == 0


def test_answer_is_idempotent_but_conflicting_retry_is_rejected(store):
    ready(store)
    payload = answer('introduction', 'I am a student.')
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: store.record(payload), range(4)))
    assert store.state()['answers_recorded'] == 1
    assert store.next_task()['task']['id'] == 'present_simple'
    with pytest.raises(learning.LearningError, match='another answer'):
        store.record(dict(payload, response='Different'))


def test_objective_grading_uses_verbatim_extracted_answer(store):
    ready(store)
    store.record(answer('introduction', 'Hello.'))
    payload = answer('present_simple', 'I think the answer is goes.', answer='goes')
    assert store.record(payload)['result'] == 'correct'
    assert store.next_task()['task']['id'] == 'past_story'
    with pytest.raises(learning.LearningError, match='verbatim'):
        store.record(dict(payload, event_id='fabrication', answer='going'))


def test_error_evidence_cannot_be_invented_or_called_pronunciation(store):
    ready(store)
    payload = answer('introduction', 'I go to work.')
    error = dict(topic='past_simple', original='Yesterday', corrected='Today', explanation='Example')
    with pytest.raises(learning.LearningError, match='exact substring'):
        store.record(dict(payload, errors=[error]))
    error.update(topic='pronunciation', original='go', corrected='go')
    with pytest.raises(learning.LearningError, match='Pronunciation'):
        store.record(dict(payload, errors=[error]))
    assert store.state()['answers_recorded'] == 0


def test_listening_requires_self_report_and_assisted_is_not_independent(store):
    complete(store, assisted=True)
    state = store.state()
    listening = next(a for a in state['diagnostic_answers'] if a['task_id'] == 'listening_short')
    assert listening['result'] == 'assisted'
    result = store.finalize(assessment())
    assert result['assessment']['listening_evidence'] == 'assisted'
    assert result['assessment']['pronunciation'] == 'not_assessed'
    assert result['assessment']['spoken_audio'] == 'not_assessed'


def test_missing_listening_self_report_does_not_advance(store):
    ready(store)
    for task, response in [('introduction', 'Hello.'), ('present_simple', 'goes'),
                           ('past_story', 'I worked yesterday.'), ('past_simple', 'learned')]:
        store.record(answer(task, response))
    with pytest.raises(learning.LearningError, match='opened the transcript'):
        store.record(answer('listening_short', 'Tuesday'))
    assert store.next_task()['task']['id'] == 'listening_short'


def test_assessment_needs_completed_tasks_and_real_evidence(store):
    ready(store)
    with pytest.raises(learning.LearningError, match='Complete all'):
        store.finalize(assessment())
    complete(store)
    data = assessment()
    data['evidence_ids'] = ['fake1', 'fake2', 'fake3']
    with pytest.raises(learning.LearningError, match='existing diagnostic'):
        store.finalize(data)
    assert store.state()['profile']['assessment'] is None


def test_level_cannot_be_upgraded_from_same_answers(store):
    complete(store)
    result = store.finalize(assessment())
    assert result['assessment']['provisional'] is True
    assert store.finalize(assessment()) == result
    data = assessment()
    data['level'] = 'B2'
    with pytest.raises(learning.LearningError, match='already saved'):
        store.finalize(data)
    assert store.state()['profile']['assessment']['level'] == 'A2'


def test_declined_tasks_do_not_establish_level(store):
    complete(store, decline=True)
    with pytest.raises(learning.LearningError, match='Insufficient'):
        store.finalize(assessment())
    data = assessment()
    data['level'] = 'undetermined'
    assert store.finalize(data)['assessment']['level'] == 'undetermined'


def test_plan_matches_time_and_changes_without_losing_answers(store):
    complete(store)
    first = store.finalize(assessment())
    assert len(first['plan']) == 7
    assert all(sum(a['minutes'] for a in day['activities']) == 15 for day in first['plan'])
    assert all('past_simple' in day['priority_topics'] for day in first['plan'])
    store.set_profile(dict(minutes_per_day=5, goal='Job interviews'))
    state = store.state()
    assert state['answers_recorded'] == 6
    assert state['profile']['assessment']['level'] == 'A2'
    for day in state['profile']['plan']:
        assert day['goal'] == 'Job interviews'
        assert sum(a['minutes'] for a in day['activities']) == 5
        assert all(a['minutes'] > 0 for a in day['activities'])


def test_practice_updates_recurring_errors_once_per_answer(store):
    complete(store)
    store.finalize(assessment())
    error = dict(topic='word_choice', original='make homework', corrected='do homework', explanation='Collocation')
    payload = dict(event_id='practice-1', phase='practice', response='I make homework.',
                   source='text', notes='Collocation practice', errors=[error, error])
    store.record(payload)
    store.record(payload)
    state = store.state()
    assert next(item for item in state['errors'] if item['topic'] == 'word_choice')['answers'] == 1
    assert 'word_choice' in state['profile']['plan'][0]['priority_topics']
    # Diagnostics remain available even when ordinary recent history is pushed out.
    for i in range(9):
        store.record(dict(payload, event_id=f'practice-{i + 2}'))
    assert len(store.state()['recent_answers']) == 8
    assert len(store.state()['diagnostic_answers']) == 6


def test_owner_data_is_isolated_in_same_db(store):
    ready(store)
    store.record(answer('introduction', 'My name is Private.'))
    other = learning.LearningStore(store.path, 99)
    assert other.state()['answers_recorded'] == 0
    assert other.state()['profile']['goal'] == ''


@pytest.mark.parametrize('data', [dict(minutes_per_day=0), dict(minutes_per_day=True),
                                 dict(minutes_per_day=121), dict(style='mean'),
                                 dict(goal=''), dict(level='B2')])
def test_invalid_profile_does_not_mutate(store, data):
    ready(store)
    before = store.state()['profile']
    with pytest.raises(learning.LearningError):
        store.set_profile(data)
    assert store.state()['profile'] == before


def test_installed_cli_persists_state_between_processes(tmp_path):
    import json
    import os
    import shutil
    import subprocess
    import sys
    pytest.importorskip('fcntl')
    script_dir = tmp_path / 'skill' / 'scripts'
    script_dir.mkdir(parents=True)
    shutil.copy(PATH, script_dir / 'learner.py')
    delivery = Path(__file__).parents[1] / 'hermes-skills/english-lesson-delivery/scripts/ordered_delivery.py'
    shutil.copy(delivery, script_dir / 'ordered_delivery.py')
    home = tmp_path / 'hermes-home'
    home.mkdir()
    env = dict(os.environ, HERMES_HOME=str(home), TELEGRAM_BOT_TOKEN='123:fake',
               TELEGRAM_ALLOWED_USERS='42', TELEGRAM_ALLOW_ALL_USERS='false')
    profile_input = tmp_path / 'profile.json'
    profile_input.write_text(json.dumps(dict(goal='Travel', minutes_per_day=15)))
    subprocess.run([sys.executable, str(script_dir / 'learner.py'), 'set-profile', '--input', str(profile_input)],
                   env=env, check=True, capture_output=True)
    result = subprocess.run([sys.executable, str(script_dir / 'learner.py'), 'state'], env=env,
                            check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    assert data['success'] is True
    assert data['data']['profile']['goal'] == 'Travel'
    assert data['data']['next']['task']['id'] == 'introduction'
    assert '123:fake' not in result.stdout


def test_mixed_language_hint_is_saved_without_being_a_grammar_error(store):
    ready(store)
    gap = dict(russian='иностранцами', english='foreigners', example='I want to talk to foreigners.')
    payload = dict(event_id='mixed-1', phase='practice', response='I want to talk to иностранцами.',
                   source='voice_transcript', notes='Used a Russian hint for missing vocabulary.',
                   errors=[], vocabulary_gaps=[gap, gap])
    store.record(payload)
    store.record(payload)
    reopened = learning.LearningStore(store.path, 42)
    state = reopened.state()
    assert state['errors'] == []
    assert state['vocabulary_gaps'][0]['english'] == 'foreigners'
    assert state['vocabulary_gaps'][0]['answers'] == 1
    assert state['recent_answers'][0]['response'] == payload['response']
    assert state['next']['task']['id'] == 'introduction'
    store.record(dict(payload, event_id='mixed-2', vocabulary_gaps=[gap]))
    assert store.state()['vocabulary_gaps'][0]['answers'] == 2


def test_asr_nonsense_cannot_create_a_guessed_word_but_clarification_can(store):
    ready(store)
    gap = dict(russian='иностранцы', english='foreigners', example='I talk to foreigners.')
    payload = dict(event_id='asr-1', phase='practice', response='I talk with the in-stands of me.',
                   source='voice_transcript', notes='Unclear ASR; needs clarification.',
                   errors=[], vocabulary_gaps=[gap])
    with pytest.raises(learning.LearningError, match='verbatim'):
        store.record(payload)
    store.record(dict(payload, vocabulary_gaps=[]))
    store.record(dict(payload, event_id='clarification-1', response='Я сказал иностранцы.',
                      source='text', notes='Clarification of asr-1.'))
    state = store.state()
    assert state['recent_answers'][0]['response'] == payload['response']
    assert state['vocabulary_gaps'][0]['russian'] == 'иностранцы'
    assert state['next']['task']['id'] == 'introduction'


def test_bad_vocabulary_input_does_not_write_an_answer(store):
    ready(store)
    payload = dict(event_id='bad-word', phase='practice', response='иностранцы', source='text',
                   notes='', errors=[], vocabulary_gaps=[dict(russian='иностранцы', english='иностранцы', example='Hi')])
    with pytest.raises(learning.LearningError, match='English translation'):
        store.record(payload)
    assert store.state()['answers_recorded'] == 0


def test_new_vocabulary_is_added_to_existing_weekly_plan(store):
    complete(store)
    store.finalize(assessment())
    store.record(dict(event_id='new-word-plan', phase='practice', response='I talk to иностранцами.',
                      source='voice_transcript', notes='', errors=[], vocabulary_gaps=[
                          dict(russian='иностранцами', english='foreigners', example='I talk to foreigners.')]))
    assert store.state()['profile']['plan'][0]['vocabulary'] == ['foreigners']


def test_objective_task_exposes_placeholder_and_options_without_answer_key(store):
    ready(store)
    store.record(answer('introduction', 'Hi.'))
    task = store.next_task()['task']
    assert task['gap_exercise'] == dict(sentence_en='She usually _____ to work by bus.', options=['go', 'goes', 'going'])
    assert 'answers' not in task and 'topic' not in task
