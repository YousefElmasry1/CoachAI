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

class TaskOutput(BaseModel):

    title: str = Field(
        ...,
        description="A short, clear task title extracted from the user's input.",
    )
    description: str = Field(
        default="",
        description=(
            "Optional extra context for the task, taken from the user's "
            "input. Leave empty if the title says everything."
        ),
    )
    category_name: str = Field(
        ...,
        description=(
            "The category this task belongs to. Reuse one of the user's "
            "EXISTING categories exactly (same spelling/casing) whenever "
            "one is a reasonable fit. Only propose a new category name "
            "when none of the existing ones fit."
        ),
    )
    is_new_category: bool = Field(
        default=False,
        description=(
            "True only if category_name does not match any existing "
            "category and is being proposed as a brand-new one."
        ),
    )
    suggested_category_color: Optional[str] = Field(
        default=None,
        description=(
            "A hex color code (e.g. '#3B82F6') for the new category. "
            "Only set this when is_new_category is true, otherwise leave "
            "it null."
        ),
    )
    priority: int = Field(
        default=3,
        ge=1,
        le=5,
        description=(
            "Priority from 1 (highest) to 5 (lowest), inferred from "
            "urgency/importance language in the input (deadlines, exams, "
            "'important' imply higher priority; casual or optional items "
            "imply lower priority)."
        ),
    )
    estimated_minutes: int = Field(
        ...,
        gt=0,
        description=(
            "A realistic duration estimate in minutes, based on the "
            "nature and implied difficulty of the task. Do not default "
            "every task to the same number — a full study session should "
            "be longer than a quick errand."
        ),
    )
    is_fixed_time: bool = Field(
        default=False,
        description=(
            "True only if the user gave a concrete clock time for this "
            "task (e.g. 'at 10am', 'at 14:00'). Vague timing language "
            "('after lunch', 'later today') does NOT count as fixed."
        ),
    )
    fixed_start: Optional[str] = Field(
        default=None,
        description=(
            "The task's fixed start time in 24-hour 'HH:MM' format. Only "
            "set this when is_fixed_time is true, otherwise leave it null. "
            "Do not compute an end time or scheduled order yourself."
        ),
    )
    needs_review: bool = Field(
        default=False,
        description=(
            "True only if you are genuinely unsure about this task's "
            "category, duration, or priority. Do not set this by default."
        ),
    )
    review_reason: Optional[str] = Field(
        default=None,
        description=(
            "A one-sentence explanation of what's uncertain, required "
            "when needs_review is true, otherwise null."
        ),
    )


class DayPlanOutput(BaseModel):

    planning_notes: str = Field(
        ...,
        description=(
            "A brief 1-2 sentence note on how the input was interpreted "
            "and split into tasks."
        ),
    )
    tasks: list[TaskOutput] = Field(
        default_factory=list,
        description="The structured tasks extracted from the user's input.",
    )


# ---------------------------------------------------------------------------
# Prompt Template
# ---------------------------------------------------------------------------

_PLANNER_SYSTEM_PROMPT: str = (
    "You are CoachAI's Planner — you convert a user's free-form description "
    "of their day into a structured list of individual tasks. You do NOT "
    "give coaching advice and you do NOT analyze history; that is handled "
    "by other components. Your only job is turning raw text into clean, "
    "well-categorized tasks. Follow these rules exactly:\n\n"
    "1. Split the input into distinct, individually actionable tasks. Do "
    "not merge unrelated tasks into one, and do not invent tasks that "
    "were not mentioned or clearly implied by the input.\n"
    "2. You are given the user's EXISTING categories. For each task, reuse "
    "an existing category name exactly (same spelling and casing) whenever "
    "it is a reasonable fit. Only propose a new category when none of the "
    "existing ones fit, and never propose a new category that is a "
    "near-duplicate of an existing one (e.g. do not propose 'Studying' if "
    "'Study' already exists).\n"
    "3. Estimate each task's duration realistically based on its nature "
    "and implied difficulty — do not default every task to the same "
    "number of minutes.\n"
    "4. Only mark a task as fixed-time if the user gave a concrete clock "
    "time for it. Vague timing language does not count. Never invent a "
    "scheduled end time or ordering yourself — that is the Scheduler's "
    "job, not yours.\n"
    "5. Assign priority (1 highest to 5 lowest) based on urgency and "
    "importance language actually present in the input.\n"
    "6. Only set needs_review=true for genuine ambiguity (unclear "
    "category, unclear duration, or unclear priority), with a short "
    "review_reason. Do not set it by default.\n"
    "7. Return ONLY valid JSON matching the required schema.\n"
    "8. Do NOT wrap the output in markdown code blocks.\n"
    "9. If the user mentions a break, rest, lunch, or meal period with a "
    "concrete clock time (e.g. 'lunch from 1 to 2', 'break at 4pm for 30 "
    "minutes'), treat it as a regular fixed-time task with "
    "category_name 'Break' — do not skip it or treat it differently from "
    "any other task.\n"
    "10. If the user asks for a break WITHOUT a concrete time (e.g. "
    "'add a break', 'I need some rest in there'), do NOT invent a clock "
    "time or mark it fixed-time — instead, place it intelligently in the "
    "task ORDER you return: right after a long or demanding task, or "
    "roughly midway through the day if the load is heavy. Use "
    "category_name 'Break' and a realistic duration (10-20 minutes for a "
    "short break, 30-60 for a meal)."
)

_PLANNER_USER_PROMPT: str = (
    "The user's existing categories are:\n\n"
    "{existing_categories}\n\n"
    "The user's free-form description of their day:\n\n"
    "{raw_input}\n\n"
    "{format_instructions}"
)


# ---------------------------------------------------------------------------
# Planner Engine
# ---------------------------------------------------------------------------

class PlannerEngine:

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

        self._parser = PydanticOutputParser(pydantic_object=DayPlanOutput)

        prompt = ChatPromptTemplate.from_messages([
            ("system", _PLANNER_SYSTEM_PROMPT),
            ("human", _PLANNER_USER_PROMPT),
        ])

        llm = ChatGoogleGenerativeAI(
            model=self._MODEL_NAME,
            temperature=self._TEMPERATURE,
            google_api_key=self._api_key,
            max_retries=self._MAX_RETRIES,
        )

        self._chain = prompt | llm | self._parser
        return self._chain

    @staticmethod
    def _format_existing_categories(existing_categories: list[str]) -> str:
        if not existing_categories:
            return "None yet — this is the user's first plan."
        return ", ".join(existing_categories)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan_day(
        self,
        raw_input: str,
        existing_categories: Optional[list[str]] = None,
    ) -> DayPlanOutput:
        cleaned_input = raw_input.strip()
        if not cleaned_input:
            raise ValueError("raw_input cannot be empty.")

        chain = self._build_chain()

        try:
            result: DayPlanOutput = chain.invoke({
                "raw_input": cleaned_input,
                "existing_categories": self._format_existing_categories(
                    existing_categories or []
                ),
                "format_instructions": self._parser.get_format_instructions(),
            })
        except Exception as exc:
            raise RuntimeError(
                f"Failed to generate a plan from Gemini. "
                f"Underlying error: {type(exc).__name__}: {exc}"
            ) from exc

        # Defensive: ensure tasks is never None
        if result.tasks is None:
            result.tasks = []

        return result