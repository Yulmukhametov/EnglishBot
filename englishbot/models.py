from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PronunciationNote(BaseModel):
    word: str = Field(max_length=100)
    explanation_ru: str = Field(max_length=500)
    confidence: Literal["low", "medium", "high"]


class AudioAnalysis(BaseModel):
    transcript: str = Field(max_length=8000)
    speech_clear: bool
    uncertainty_ru: str = Field(max_length=500)
    pronunciation: list[PronunciationNote] = Field(max_length=5)


class Profile(BaseModel):
    model_config = ConfigDict(extra="ignore")
    goal: str = "Общение на английском; 15 минут в день. Цель уточнить при знакомстве."
    style: Literal["friendly", "strict", "casual"] = "friendly"
    level: str = "Не определён"
    learning_notes: str = ""
    plan: str = ""
    homework: str = ""
    diagnostic_stage: str = "Знакомство: спросить цель, опыт и предложить короткий рассказ."


class Lesson(BaseModel):
    # No optional properties: this model is also an OpenAI strict output schema.
    feedback_ru: str = Field(max_length=2000)
    reply_en: str = Field(min_length=1, max_length=1800)
    level: str = Field(max_length=150)
    learning_notes: str = Field(max_length=3000)
    plan: str = Field(max_length=2500)
    homework: str = Field(max_length=1500)
    diagnostic_stage: str = Field(max_length=600)


class Speech(BaseModel):
    audio: bytes
    transcript: str
