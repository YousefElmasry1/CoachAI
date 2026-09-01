import os
from typing import Any, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.output_parsers import PydanticOutputParser


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


# ---------------------------------------------------------------------------
# Pydantic Models - Structured Output
# ---------------------------------------------------------------------------

class RecommendationOutput(BaseModel):

    summary: str = Field(
        ...,
        description=(
            "A concise 2-3 sentence overview of the day's schedule, "
            "highlighting overall balance and workload."
        ),
    )
    strengths: list[str] = Field(
        default_factory=list,
        description=(
            "List of 2-4 things the user is doing well. "
            "Examples: good break distribution, strong focus blocks, "
            "balanced categories, sustained streaks, improving trends."
        ),
    )
    weaknesses: list[str] = Field(
        default_factory=list,
        description=(
            "List of 2-4 areas for improvement, grounded in recurring "
            "patterns from the historical context when available. "
            "Examples: back-to-back heavy tasks, insufficient breaks, "
            "late-day overload, a category the user repeatedly fails, "
            "a recurring failure reason at a specific time of day."
        ),
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description=(
            "List of 3-5 specific, actionable coaching recommendations, "
            "each tied to a concrete step the user can take today or "
            "going forward."
        ),
    )


# ---------------------------------------------------------------------------
# Prompt Template
# ---------------------------------------------------------------------------

_RECOMMENDATION_SYSTEM_PROMPT: str = (
    "You are CoachAI, a long-term AI productivity coach with 10+ years of "
    "experience helping students and professionals build better habits.\n\n"
    "You are given two things: (1) the user's schedule for today, and "
    "(2) historical context about this specific user — their analytics "
    "profile and a record of their recent plans and tasks, including "
    "which ones they completed, failed, and why.\n\n"
    "Your job is to combine both to produce personalized coaching, not a "
    "generic schedule review. Specifically:\n\n"
    "1. Analyze today's schedule on its own merits (workload, balance, "
    "timing of high-priority tasks, break placement).\n"
    "2. Analyze the historical context for recurring patterns: categories "
    "the user repeatedly fails, times of day their failures cluster "
    "around, tasks they consistently postpone, trends in their "
    "completion rate or streaks, and habits visible across multiple "
    "days rather than a single one.\n"
    "3. When you identify a pattern, say so explicitly and explain WHY it "
    "matters, then suggest HOW to address it. A good pattern observation "
    "names the recurring behavior and ties it to what's in the "
    "historical context — not a vague generality.\n"
    "4. Ground every claim in the schedule or historical context you were "
    "given. If the historical context says there is no data yet, do not "
    "invent a history — comment only on today's schedule instead.\n"
    "5. Do NOT invent new tasks or modify existing ones.\n"
    "6. Do NOT change task order or durations.\n"
    "7. Only provide coaching commentary and recommendations — you are "
    "not the Scheduler or the Analytics engine, so do not recompute "
    "statistics; use the ones already provided.\n"
    "8. Avoid generic, one-size-fits-all advice — every recommendation "
    "should read as if it were written for this specific user.\n"
    "9. Return ONLY valid JSON matching the required schema.\n"
    "10. Do NOT wrap the output in markdown code blocks.\n"
    "11. 'Ran out of time' is a SYSTEM-assigned failure reason, not one "
    "the user chose — it means the task was still pending or in-progress "
    "when its day ended, so the user never actively worked on it or "
    "never finished it. Treat it as a signal of over-scheduling or poor "
    "prioritization (planning more than the day could hold), not as "
    "active effort that failed. When it appears often, call this out "
    "explicitly as an over-scheduling pattern. Always distinguish it from "
    "user-reported reasons like 'Distracted', 'Tired', or 'Harder than "
    "expected', which reflect genuine attempts that didn't go as planned.\n"
    "12. Language: write all output text in the SAME language as the "
    "user's schedule/historical context (Arabic if they are in Arabic). "
    "When referring to metrics, use these exact Arabic terms and do NOT "
    "leave them in English or add English in parentheses:\n"
    "   - burnout risk -> خطر الإرهاق\n"
    "   - streak / current streak / longest streak -> سلسلة الإنجاز\n"
    "   - completion rate -> نسبة الإنجاز\n"
    "   - productivity score -> مؤشر الإنتاجية\n"
    "   - consistency score -> مؤشر الانتظام\n"
    "   - planning accuracy -> دقة التخطيط\n"
    "   - trend direction -> اتجاه الأداء"
)

_RECOMMENDATION_USER_PROMPT: str = (
    "Today's schedule:\n\n"
    "{schedule_text}\n\n"
    "Historical context for this user (profile stats, recent plans, and "
    "recent tasks):\n\n"
    "{historical_context}\n\n"
    "{format_instructions}"
)


# ---------------------------------------------------------------------------
# Recommendation Engine
# ---------------------------------------------------------------------------

class RecommendationEngine:


    _MODEL_NAME: str = "gemini-3.6-flash"
    _TEMPERATURE: float = 0.2
    _MAX_RETRIES: int = 2

    def __init__(self) -> None:
        self._api_key: str = _GOOGLE_API_KEY
        self._chain: Optional[Any] = None
        self._parser: Optional[PydanticOutputParser] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_chain(self) -> Any:
        if self._chain is not None:
            return self._chain

        self._parser = PydanticOutputParser(
            pydantic_object=RecommendationOutput
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", _RECOMMENDATION_SYSTEM_PROMPT),
            ("human", _RECOMMENDATION_USER_PROMPT),
        ])

        llm = ChatGoogleGenerativeAI(
            model=self._MODEL_NAME,
            temperature=self._TEMPERATURE,
            google_api_key=self._api_key,
            max_retries=self._MAX_RETRIES,
        )

        self._chain = prompt | llm | self._parser
        return self._chain

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_schedule(
        self,
        schedule_text: str,
        historical_context: str = "",
    ) -> RecommendationOutput:
        cleaned_schedule = schedule_text.strip()
        if not cleaned_schedule:
            raise ValueError("schedule_text cannot be empty.")

        cleaned_history = historical_context.strip() or (
            "No historical data is available yet for this user."
        )

        chain = self._build_chain()

        try:
            result: RecommendationOutput = chain.invoke({
                "schedule_text": cleaned_schedule,
                "historical_context": cleaned_history,
                "format_instructions": self._parser.get_format_instructions(),
            })
        except Exception as exc:
            raise RuntimeError(
                f"Failed to generate recommendations from Gemini. "
                f"Underlying error: {type(exc).__name__}: {exc}"
            ) from exc

        # Defensive: ensure lists are never None
        if result.strengths is None:
            result.strengths = []
        if result.weaknesses is None:
            result.weaknesses = []
        if result.recommendations is None:
            result.recommendations = []

        return result