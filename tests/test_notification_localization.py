from datetime import datetime, timezone
import unittest

from notifications import TemplateCatalog, Tone, format_local_datetime
from notifications.templates import normalize_locale


class NotificationLocalizationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = TemplateCatalog()

    def test_english_and_arabic_are_utf8_and_keep_original_task_title(self):
        context = {"task_title": "قراءة API"}
        english = self.catalog.select(
            "task_reminder", locale="en-US", tone=Tone.NEUTRAL,
            context=context, seed="english",
        )
        arabic = self.catalog.select(
            "task_reminder", locale="ar-EG", tone=Tone.NEUTRAL,
            context=context, seed="arabic",
        )
        self.assertEqual("en", english.locale)
        self.assertEqual("ar", arabic.locale)
        self.assertIn("قراءة API", english.body)
        self.assertIn("قراءة API", arabic.body)
        self.assertTrue(any("\u0600" <= char <= "\u06ff" for char in arabic.title))

    def test_template_id_and_version_are_explicit(self):
        rendered = self.catalog.select(
            "schedule_updated", locale="en", tone="coach",
            context={"adjusted_count": 3}, seed="version",
        )
        self.assertTrue(rendered.template_id.startswith("schedule.updated.en"))
        self.assertEqual(1, rendered.template_version)

    def test_recent_template_is_avoided_when_an_alternative_exists(self):
        first = self.catalog.select(
            "task_reminder", locale="en", tone="neutral",
            context={"task_title": "A"}, seed="fixed",
        )
        second = self.catalog.select(
            "task_reminder", locale="en", tone="neutral",
            context={"task_title": "A"}, recent_template_ids=[first.template_id], seed="fixed",
        )
        self.assertNotEqual(first.template_id, second.template_id)

    def test_locale_fallback_is_english(self):
        self.assertEqual("en", normalize_locale("fr-FR"))

    def test_locale_and_time_format_use_existing_iana_timezone(self):
        instant = datetime(2026, 10, 8, 12, 5, tzinfo=timezone.utc)
        english = format_local_datetime(
            instant, timezone_name="Africa/Cairo", locale="en", time_format="12h",
        )
        arabic = format_local_datetime(
            instant, timezone_name="Africa/Cairo", locale="ar", time_format="24h",
        )
        self.assertEqual("2026-10-08, 03:05 PM", english)
        self.assertIn("١٥:٠٥", arabic)


if __name__ == "__main__":
    unittest.main()
