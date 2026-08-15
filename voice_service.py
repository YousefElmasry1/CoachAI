"""
CoachAI – Voice Service (Speech-to-Text via Gemini)
======================================================

Transcribes a voice recording (bytes from Streamlit's st.audio_input())
into plain text using Gemini's native audio understanding.

The transcribed text is handed off as-is to the existing PlannerEngine
(planner.py) exactly like typed free-form text — this module never
splits tasks, assigns categories, or talks to the database. It has
exactly one job: audio in, text out.

Usage:
    from voice_service import transcribe_audio

    text = transcribe_audio(audio_bytes, mime_type="audio/wav")
    result = generate_today_plan(raw_input=text, user_id=user_id)
"""

from __future__ import annotations

import os
from typing import Any, Optional

from dotenv import load_dotenv
import google.generativeai as genai


# ---------------------------------------------------------------------------
# Environment Setup
# ---------------------------------------------------------------------------

load_dotenv()

_GOOGLE_API_KEY: Optional[str] = os.getenv("GOOGLE_API_KEY")
if not _GOOGLE_API_KEY:
    raise EnvironmentError(
        "GOOGLE_API_KEY not found. Please create a .env file with:\n"
        "GOOGLE_API_KEY=your_key_here"
    )

genai.configure(api_key=_GOOGLE_API_KEY)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Same model family used by planner.py / recommendation.py, for one
# consistent Gemini version across the whole app.
_MODEL_NAME: str = "gemini-3.6-flash"

_TRANSCRIBE_PROMPT: str = (
    "You are an automatic speech recognition (ASR) engine, not an AI "
    "assistant. Your only task is to transcribe the audio exactly as "
    "spoken. The speaker may use English, Arabic, or Egyptian Arabic "
    "mixed with English within the same sentence. Preserve the "
    "original language exactly as spoken. Never translate, "
    "paraphrase, summarize, or rewrite anything. Do not add "
    "punctuation the speaker didn't clearly imply through pauses or "
    "intonation — this transcript is used downstream to split the "
    "speech into individual tasks, so invented punctuation can "
    "silently merge or split tasks incorrectly. "
    "Preserve English technical terms exactly as they are spoken, "
    "including but not limited to: Machine Learning, Deep Learning, "
    "Artificial Intelligence, AI, LLM, Python, SQL, Database, GitHub, "
    "Git, VS Code, Streamlit, Docker, API, JSON, CoachAI, LangChain, "
    "Gemini, FastAPI, Linux, C++, Java, JavaScript, React, TensorFlow, "
    "PyTorch. Do not convert English technical words into Arabic "
    "phonetic spellings. For example, transcribe 'Machine Learning' "
    "as 'Machine Learning', not 'ماشين ليرنينج'. "
    "If any portion of the recording is unclear, noisy, or silent, do "
    "not guess or invent missing words. Transcribe only what is "
    "confidently audible. Never infer, normalize, or complete dates, "
    "times, or durations beyond exactly what was spoken — e.g. if the "
    "speaker says a vague time, transcribe that vague phrasing "
    "verbatim rather than converting it to a precise clock time. "
    "Do not add explanations, labels, comments, markdown, quotation "
    "marks, or formatting. Return only the raw transcript."
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class VoiceTranscriptionError(Exception):
    """Raised when Gemini fails to transcribe the given audio."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def transcribe_audio(audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
    """
    Transcribe raw audio bytes into plain text using Gemini.

    Sends the audio as an inline blob on the same generate_content
    endpoint already used successfully by planner.py / recommendation.py
    — the alternative genai.upload_file() (File API) path was tried and
    rejected the same API key ("API key not valid"), since it validates
    keys differently than plain generateContent calls. Inline data needs
    no extra key permissions and no async processing wait.

    Args:
        audio_bytes: Raw audio bytes, e.g. the value returned by
            Streamlit's st.audio_input() widget.
        mime_type: MIME type of the audio, ideally read from the
            recorder itself (e.g. audio_value.type) rather than
            assumed — a mismatched mime_type is a common reason Gemini
            silently returns an empty (or, worse, hallucinated)
            response for otherwise-valid audio.

    Returns:
        The transcribed text, stripped of leading/trailing whitespace.

    Raises:
        ValueError: If audio_bytes is empty.
        VoiceTranscriptionError: If Gemini fails, or returns no text
            (e.g. silence, a blocked prompt, or a mismatched format).
    """
    if not audio_bytes:
        raise ValueError("audio_bytes cannot be empty.")

    try:
        model = genai.GenerativeModel(_MODEL_NAME)
        response = model.generate_content(
            [
                _TRANSCRIBE_PROMPT,
                {"mime_type": mime_type, "data": audio_bytes},
            ],
            generation_config=genai.types.GenerationConfig(
                # Deterministic, literal transcription rather than the
                # model's default "creative" temperature — this is what
                # stops it from confidently inventing plausible-sounding
                # speech when it isn't sure what it heard.
                temperature=0.0,
                candidate_count=1,
            ),
        )
    except Exception as exc:
        raise VoiceTranscriptionError(
            f"Failed to transcribe audio via Gemini. "
            f"Underlying error: {type(exc).__name__}: {exc}"
        ) from exc

    text = _extract_text(response)
    if not text:
        raise VoiceTranscriptionError(_diagnose_empty_response(response))

    return text


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_text(response: Any) -> str:
    """
    Safely pull transcribed text out of a Gemini response.

    response.text is a convenience accessor that raises ValueError when
    the first candidate has no parts (empty response) instead of
    returning ""; this walks the raw candidates/parts structure instead
    so a truly-empty response can be diagnosed rather than crashing.
    """
    try:
        return (response.text or "").strip()
    except Exception:
        pass

    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) if content else None
        if not parts:
            continue
        joined = "".join(getattr(part, "text", "") or "" for part in parts).strip()
        if joined:
            return joined
    return ""


def _diagnose_empty_response(response: Any) -> str:
    """Turn an empty Gemini response into a specific, actionable message."""
    feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(feedback, "block_reason", None)
    if block_reason:
        return (
            f"Gemini blocked this recording (reason: {block_reason}). "
            "Try recording again."
        )

    candidates = getattr(response, "candidates", None) or []
    if candidates:
        finish_reason = getattr(candidates[0], "finish_reason", None)
        # 1 / "STOP" is a normal completion; anything else explains the gap.
        if finish_reason not in (None, 1, "STOP"):
            return (
                f"Gemini stopped without producing a transcript "
                f"(finish_reason: {finish_reason}). This can mean the audio "
                "format wasn't recognised — try recording again."
            )

    return (
        "Gemini couldn't make out any speech in that recording — it may be "
        "silent, too quiet, or too short. Please try recording again, a bit "
        "closer to the mic."
    )