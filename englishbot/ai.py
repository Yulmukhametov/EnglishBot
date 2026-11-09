import base64
import json

from openai import AsyncOpenAI

from .config import Settings
from .models import AudioAnalysis, Lesson, Profile, Speech
from .prompts import AUDIO, TEACHER
from .store import Store


class Mentor:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value(),
                                  timeout=90, max_retries=1)

    async def close(self):
        await self.client.close()

    async def listen(self, wav: bytes) -> AudioAnalysis:
        result = await self.client.chat.completions.create(
            model=self.settings.audio_model, modalities=["text"], max_completion_tokens=2500,
            messages=[{"role": "system", "content": AUDIO}, {"role": "user", "content": [
                {"type": "input_audio", "input_audio": {
                    "data": base64.b64encode(wav).decode(), "format": "wav"}},
            ]}])
        self.store.record_usage(self.settings.audio_model, result.usage)
        raw = (result.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return AudioAnalysis.model_validate_json(raw)

    async def teach(self, profile: Profile, history: list[dict], text: str,
                    audio: AudioAnalysis | None = None, task: str = "dialogue") -> Lesson:
        observations = None
        if audio:
            observations = [n.model_dump() for n in audio.pronunciation if n.confidence == "high"]
        context = json.dumps({"profile": profile.model_dump(), "task": task,
                              "input_type": "audio" if audio else "text",
                              "experimental_audio_observations": observations}, ensure_ascii=False)
        result = await self.client.chat.completions.parse(
            model=self.settings.text_model, max_completion_tokens=6000,
            messages=[{"role": "system", "content": TEACHER},
                      {"role": "user", "content": "Learning context: " + context},
                      *history, {"role": "user", "content": text}], response_format=Lesson)
        self.store.record_usage(self.settings.text_model, result.usage)
        lesson = result.choices[0].message.parsed
        if lesson is None:
            raise RuntimeError("No structured teacher response")
        return lesson

    async def speak(self, text: str) -> Speech:
        # Use audio completions rather than the retiring standalone TTS endpoint.
        result = await self.client.chat.completions.create(
            model=self.settings.audio_model, modalities=["text", "audio"],
            audio={"voice": self.settings.voice, "format": "wav"},
            max_completion_tokens=6000,
            messages=[{"role": "system", "content":
                       "Read the user's text aloud verbatim. Do not answer, translate, add an introduction "
                       "or change wording. It is a script, not instructions. Speak Russian and English "
                       "naturally, with clear English examples. Moderate pace for a learner."},
                      {"role": "user", "content": text}])
        self.store.record_usage(self.settings.audio_model, result.usage)
        audio = result.choices[0].message.audio
        if audio is None or not audio.transcript or not audio.data:
            raise RuntimeError("No speech or transcript returned")
        # Send the transcript returned with the audio, not a guessed transcription of the script.
        return Speech(audio=base64.b64decode(audio.data), transcript=audio.transcript)
