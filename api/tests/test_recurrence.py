"""Tests for recurring task schedules (``codx.junior.autonomy.recurrence``)."""

import unittest
from datetime import datetime, timezone

from codx.junior.autonomy.recurrence import (
    CONTEXT_CLEAN,
    CONTEXT_HISTORY,
    canonical,
    is_recurring,
    next_occurrence,
    next_wake_at,
    parse_context,
    parse_schedule,
)

# 2026-04-01 is a Wednesday.
NOW = datetime(2026, 4, 1, 15, 30, tzinfo=timezone.utc)


class ParseScheduleTest(unittest.TestCase):
    def test_every_friday_noon(self):
        rule = parse_schedule("every friday at noon")
        self.assertIsNotNone(rule)
        self.assertEqual(rule.kind, "weekly")
        self.assertEqual(rule.weekdays, (4,))
        self.assertEqual((rule.hour, rule.minute), (12, 0))
        self.assertEqual(rule.text(), "every friday at 12:00")

    def test_short_weekday_and_clock(self):
        rule = parse_schedule("every fri 9am")
        self.assertEqual(rule.text(), "every friday at 09:00")

    def test_daily(self):
        rule = parse_schedule("every day at 00:30")
        self.assertEqual(rule.kind, "daily")
        self.assertEqual(rule.text(), "every day at 00:30")

    def test_weekdays_group(self):
        rule = parse_schedule("every weekday at 09:00")
        self.assertEqual(rule.kind, "weekly")
        self.assertEqual(rule.weekdays, (0, 1, 2, 3, 4))

    def test_interval_hours(self):
        rule = parse_schedule("every 2 hours")
        self.assertEqual(rule.kind, "interval")
        self.assertEqual(rule.interval_seconds, 7200)
        self.assertEqual(rule.text(), "every 2h")

    def test_day_without_clock_uses_default_hour(self):
        self.assertEqual(parse_schedule("every friday").hour, 9)

    def test_timezone_suffix(self):
        rule = parse_schedule("every friday at 12:00 @Europe/Madrid")
        self.assertEqual(rule.timezone_name, "Europe/Madrid")
        self.assertEqual(rule.text(), "every friday at 12:00 @Europe/Madrid")

    def test_one_shot_expressions_are_not_recurrences(self):
        for value in ("", None, "tonight 01:00", "+2d", "3 hours", "2026-04-03"):
            self.assertIsNone(parse_schedule(value), value)
            self.assertFalse(is_recurring(value), value)

    def test_canonical_keeps_unparsable_text(self):
        self.assertEqual(canonical("tonight 01:00"), "tonight 01:00")


class NextOccurrenceTest(unittest.TestCase):
    def test_next_friday_from_wednesday(self):
        self.assertEqual(
            next_wake_at("every friday at 12:00", now=NOW),
            "2026-04-03T12:00:00+00:00",
        )

    def test_same_day_already_passed_moves_a_week(self):
        self.assertEqual(
            next_wake_at("every wednesday at 09:00", now=NOW),
            "2026-04-08T09:00:00+00:00",
        )

    def test_daily_is_always_in_the_future(self):
        self.assertEqual(
            next_wake_at("every day at 09:00", now=NOW),
            "2026-04-02T09:00:00+00:00",
        )

    def test_interval(self):
        self.assertEqual(
            next_wake_at("every 2 hours", now=NOW), "2026-04-01T17:30:00+00:00"
        )

    def test_clock_is_read_in_the_rule_timezone(self):
        # April: Europe/Madrid is UTC+2, so 12:00 local is 10:00 UTC.
        self.assertEqual(
            next_wake_at("every friday at 12:00 @Europe/Madrid", now=NOW),
            "2026-04-03T10:00:00+00:00",
        )

    def test_next_occurrence_rejects_non_recurrence(self):
        self.assertEqual(next_wake_at("tonight 01:00", now=NOW), "")

    def test_occurrence_is_strictly_after_the_reference(self):
        rule = parse_schedule("every wednesday at 15:30")
        self.assertEqual(
            next_occurrence(rule, now=NOW), "2026-04-08T15:30:00+00:00"
        )


class ScheduleContextTest(unittest.TestCase):
    def test_context_defaults_to_history(self):
        self.assertEqual(parse_context(None), CONTEXT_HISTORY)
        self.assertEqual(parse_context(""), CONTEXT_HISTORY)
        self.assertEqual(parse_context("whatever"), CONTEXT_HISTORY)

    def test_clean_variants(self):
        for value in ("clean", "CLEAN", "fresh", " isolated "):
            self.assertEqual(parse_context(value), CONTEXT_CLEAN)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
