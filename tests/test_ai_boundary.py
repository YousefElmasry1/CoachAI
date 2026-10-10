from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from pydantic import ValidationError as PydanticValidationError

from application_services import AICoachingApplicationService, AIPlanningApplicationService
from backend_errors import (
    ExternalProviderFailure,
    InvalidStateTransition,
    RateLimited,
    ResourceNotFound,
)
from chatbot_service import ChatbotService
from database import Database
from domain_validation import (
    MAX_AI_TASKS,
    MAX_AI_INPUT_CHARS,
    MAX_AI_OUTPUT_TOKENS,
    MAX_AUDIO_BYTES,
    MAX_TASK_TITLE_CHARS,
    MAX_TRANSCRIPT_CHARS,
)
from planner import DayPlanOutput, TaskOutput
from planner_service import PlannerService
from recommendation import RecommendationEngine, RecommendationOutput
from rate_limiter import DatabaseRateLimiter, RateLimitExceeded
from identity import AuthenticatedPrincipal
from operational_metrics import BackendMetrics, InMemoryMetricSink
from voice_service import VoiceTranscriptionError, transcribe_audio


def _task(title: str) -> TaskOutput:
    return TaskOutput(
        title=title,
        category_name="Work",
        estimated_minutes=30,
    )


class SafeAiBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "ai-boundary.db"
        self.db = Database(f"sqlite:///{self.path.as_posix()}")
        self.db.create_user("u1", "u1@example.test", "U1", "UTC")
        self.service = PlannerService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.tempdir.cleanup()

    def test_structured_output_limits_are_enforced(self) -> None:
        with self.assertRaises(PydanticValidationError):
            _task("x" * (MAX_TASK_TITLE_CHARS + 1))
        with self.assertRaises(PydanticValidationError):
            DayPlanOutput(
                planning_notes="bounded",
                tasks=[_task(f"Task {index}") for index in range(MAX_AI_TASKS + 1)],
            )

    def test_apply_is_atomic_when_a_later_task_fails(self) -> None:
        draft = DayPlanOutput(
            planning_notes="Two tasks",
            tasks=[_task("First"), _task("Second")],
        )
        original = self.db.add_task
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected failure")
            return original(*args, **kwargs)

        with patch.object(self.db, "add_task", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.service.save_plan(draft, "first and second", "u1")

        self.assertIsNone(self.db.get_today_plan("u1"))
        self.assertEqual([], self.db.get_categories("u1"))

    def test_direct_model_to_persistence_path_is_quarantined(self) -> None:
        with self.assertRaises(InvalidStateTransition):
            self.service.generate_and_save_plan("Do work", "u1")

    def test_explicit_apply_is_durable_and_idempotent(self) -> None:
        application = AIPlanningApplicationService(
            self.db,
            AuthenticatedPrincipal(
                "u1", "u1@example.test", "U1", {"sub": "u1"}
            ),
        )
        draft = DayPlanOutput(
            planning_notes="One task",
            tasks=[_task("Apply once")],
        )
        first = application.apply_draft(
            draft, raw_input="apply once", idempotency_key="apply-1"
        )
        replay = application.apply_draft(
            draft, raw_input="apply once", idempotency_key="apply-1"
        )
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(first["plan_id"], replay["plan_id"])
        self.assertEqual(
            1, len(self.db.get_tasks_by_plan(int(first["plan_id"]), "u1"))
        )

    def test_voice_rejects_oversize_and_unapproved_mime_before_provider_call(self) -> None:
        with self.assertRaises(ValueError):
            transcribe_audio(b"audio", "application/octet-stream")
        with self.assertRaises(ValueError):
            transcribe_audio(b"x" * (MAX_AUDIO_BYTES + 1), "audio/wav")

    def test_voice_uses_supported_client_with_bounded_transport(self) -> None:
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(
            text="  buy groceries  "
        )
        sink = InMemoryMetricSink()

        with (
            patch.dict(
                os.environ,
                {
                    "GOOGLE_API_KEY": "test-key",
                    "COACHAI_AI_TIMEOUT_SECONDS": "1.25",
                    "COACHAI_VOICE_MODEL": "gemini-3.8-flash",
                },
            ),
            patch("voice_service.genai.Client", return_value=client) as client_type,
        ):
            result = transcribe_audio(
                b"audio-bytes",
                "audio/wav; codecs=pcm",
                metrics=BackendMetrics(sink),
            )

        self.assertEqual("buy groceries", result)
        client.close.assert_called_once_with()
        options = client_type.call_args.kwargs["http_options"]
        self.assertEqual("v1", options.api_version)
        self.assertEqual(1250, options.timeout)
        self.assertEqual(3, options.retry_options.attempts)
        self.assertEqual(2.0, options.retry_options.max_delay)
        request = client.models.generate_content.call_args.kwargs
        self.assertEqual("gemini-3.8-flash", request["model"])
        self.assertEqual("audio/wav", request["contents"][1].inline_data.mime_type)
        self.assertEqual(b"audio-bytes", request["contents"][1].inline_data.data)
        counters = sink.snapshot()["counters"]["coachai_ai_calls_total"]
        self.assertEqual(1.0, next(iter(counters.values())))

    def test_voice_errors_are_safe_and_failed_outputs_are_measured(self) -> None:
        oversized = MagicMock()
        oversized.models.generate_content.return_value = SimpleNamespace(
            text="x" * (MAX_TRANSCRIPT_CHARS + 1)
        )
        sink = InMemoryMetricSink()

        with (
            patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}),
            patch("voice_service.genai.Client", return_value=oversized),
            self.assertRaisesRegex(VoiceTranscriptionError, "supported size"),
        ):
            transcribe_audio(b"audio", metrics=BackendMetrics(sink))

        counters = sink.snapshot()["counters"]["coachai_ai_calls_total"]
        self.assertTrue(any("result=failure" in labels for labels in counters))

        blocked = MagicMock()
        blocked.models.generate_content.return_value = SimpleNamespace(
            text="",
            prompt_feedback=SimpleNamespace(block_reason="PRIVATE_PROVIDER_DETAIL"),
            candidates=[],
        )
        with (
            patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}),
            patch("voice_service.genai.Client", return_value=blocked),
            self.assertRaises(VoiceTranscriptionError) as caught,
        ):
            transcribe_audio(b"audio")
        self.assertNotIn("PRIVATE_PROVIDER_DETAIL", str(caught.exception))
        self.assertNotIn("Gemini", str(caught.exception))

    def test_database_rate_limit_is_shared_and_rejected_attempt_is_not_counted(self) -> None:
        second = Database(f"sqlite:///{self.path.as_posix()}")
        try:
            clock = lambda: 120.0
            first_limiter = DatabaseRateLimiter(
                self.db, scope="ai.test", max_requests=2,
                window_seconds=60, wall_clock=clock,
            )
            second_limiter = DatabaseRateLimiter(
                second, scope="ai.test", max_requests=2,
                window_seconds=60, wall_clock=clock,
            )
            first_limiter.check("u1")
            second_limiter.check("u1")
            with self.assertRaises(RateLimitExceeded):
                first_limiter.check("u1")
            row = second.fetch_one(
                """SELECT request_count FROM rate_limit_buckets
                   WHERE user_id=? AND scope=?""",
                ("u1", "ai.test"),
            )
            self.assertEqual(2, int(row["request_count"]))
        finally:
            second.close()

    def test_coaching_boundary_derives_identity_and_hides_missing_foreign_plan(self) -> None:
        principal = AuthenticatedPrincipal(
            "u1", "u1@example.test", "U1", {"sub": "u1"}
        )
        service = AICoachingApplicationService(self.db, principal)
        self.db.create_user("u2", "u2@example.test", "U2", "UTC")
        foreign_plan = self.db.create_plan("u2", date(2026, 10, 10), "foreign")

        with self.assertRaises(ResourceNotFound):
            service.recommendations_for_plan(foreign_plan)

        fake_output = DayPlanOutput(planning_notes="safe", tasks=[])
        with patch.object(
            service.recommendations,
            "get_recommendations_for_today",
            return_value=fake_output,
        ) as recommend:
            result = service.recommendations_for_today()
        recommend.assert_called_once_with("u1")
        self.assertEqual("safe", result["planning_notes"])

    def test_coaching_boundary_translates_internal_rate_limit(self) -> None:
        principal = AuthenticatedPrincipal(
            "u1", "u1@example.test", "U1", {"sub": "u1"}
        )
        service = AICoachingApplicationService(self.db, principal)
        with (
            patch.object(
                service._voice_limiter,
                "check",
                side_effect=RateLimitExceeded(12.4),
            ),
            self.assertRaises(RateLimited) as caught,
        ):
            service.transcribe_voice(b"audio")
        self.assertEqual("RATE_LIMITED", caught.exception.code.value)
        self.assertEqual(12, caught.exception.details["retry_after_seconds"])

    def test_chatbot_client_and_payload_are_bounded(self) -> None:
        client = MagicMock()
        client.chats.create.return_value = MagicMock()
        with (
            patch.dict(
                os.environ,
                {"GOOGLE_API_KEY": "test-key", "COACHAI_AI_TIMEOUT_SECONDS": "1.5"},
            ),
            patch("google.genai.Client", return_value=client) as client_type,
        ):
            chatbot = ChatbotService("u1", db=self.db)

        options = client_type.call_args.kwargs["http_options"]
        self.assertEqual("v1", options.api_version)
        self.assertEqual(1500, options.timeout)
        self.assertEqual(3, options.retry_options.attempts)
        config = client.chats.create.call_args.kwargs["config"]
        self.assertEqual(MAX_AI_OUTPUT_TOKENS, config.max_output_tokens)
        self.assertIn(
            "too long",
            chatbot.send_message("x" * (MAX_AI_INPUT_CHARS + 1)).lower(),
        )

    def test_recommendation_provider_error_is_safe_and_output_items_are_bounded(self) -> None:
        engine = RecommendationEngine()
        engine._chain = MagicMock()
        engine._chain.invoke.side_effect = RuntimeError("provider-secret")
        engine._parser = MagicMock()
        with self.assertRaises(ExternalProviderFailure) as caught:
            engine.analyze_schedule("09:00 work", "No history")
        self.assertNotIn("provider-secret", str(caught.exception))

        with self.assertRaises(PydanticValidationError):
            RecommendationOutput(
                summary="safe",
                strengths=["x" * 2_001],
                weaknesses=[],
                recommendations=[],
            )


if __name__ == "__main__":
    unittest.main()
