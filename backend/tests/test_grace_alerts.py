import unittest
from datetime import date, timedelta
from decimal import Decimal

from app.services.grace_alerts import build_grace_deadline_alert


class GraceDeadlineAlertTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 14)
        self.base_args = {
            "period_id": 10,
            "month": date(2026, 8, 1),
            "outstanding": Decimal("66856.00"),
            "target_date": self.today,
        }

    def build(self, days_left: int, **overrides):
        args = {
            **self.base_args,
            "grace_deadline": self.today + timedelta(days=days_left),
            **overrides,
        }
        return build_grace_deadline_alert(**args)

    def test_no_alert_more_than_seven_days_before_deadline(self):
        self.assertIsNone(self.build(8))

    def test_paid_period_has_no_alert(self):
        self.assertIsNone(self.build(3, outstanding=Decimal("0")))

    def test_seven_day_reminder_is_sent(self):
        alert = self.build(7)

        self.assertEqual(alert.severity, "warning")
        self.assertTrue(alert.should_notify)
        self.assertIn("66 856 руб.", alert.message)

    def test_intermediate_day_is_visible_but_not_sent(self):
        alert = self.build(6)

        self.assertEqual(alert.severity, "warning")
        self.assertFalse(alert.should_notify)

    def test_three_day_reminder_is_urgent(self):
        alert = self.build(3)

        self.assertEqual(alert.severity, "urgent")
        self.assertTrue(alert.should_notify)
        self.assertIn("3 дня", alert.message)

    def test_one_day_reminder_is_critical(self):
        alert = self.build(1)

        self.assertEqual(alert.severity, "critical")
        self.assertTrue(alert.should_notify)
        self.assertIn("1 день", alert.message)

    def test_deadline_day_is_critical(self):
        alert = self.build(0)

        self.assertEqual(alert.severity, "critical")
        self.assertTrue(alert.should_notify)
        self.assertIn("срок погашения сегодня", alert.message)

    def test_overdue_debt_is_reported_daily(self):
        alert = self.build(-2)

        self.assertEqual(alert.severity, "overdue")
        self.assertTrue(alert.should_notify)
        self.assertIn("просрочено на 2 дня", alert.message)

    def test_api_dict_contains_serializable_values(self):
        data = self.build(3).to_dict()

        self.assertEqual(data["outstanding"], 66856.0)
        self.assertEqual(data["grace_deadline"], "2026-11-17")
        self.assertEqual(data["month"], "2026-08")


if __name__ == "__main__":
    unittest.main()
