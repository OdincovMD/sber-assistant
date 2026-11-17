import asyncio
import os
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

RUN_INTEGRATION_TESTS = os.getenv("RUN_INTEGRATION_TESTS") == "1"

if RUN_INTEGRATION_TESTS:
    import asyncpg
    from fastapi.testclient import TestClient

    from app.main import app
    from app.schemas import OllamaParseResult
    from app.services.ollama_client import ollama_client
    from app.tasks.celery_worker import process_sms_task


@unittest.skipUnless(
    RUN_INTEGRATION_TESTS,
    "Set RUN_INTEGRATION_TESTS=1 and provide PostgreSQL to run integration tests",
)
class WebhookPipelineIntegrationTests(unittest.TestCase):
    sms_text = "31.10.2026 23:58 Покупка 1500 руб. TEST SHOP"
    idempotency_key = "integration-purchase-2026-10-31"

    @classmethod
    def setUpClass(cls):
        asyncio.run(cls._truncate_application_tables())

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls._truncate_application_tables())

    @staticmethod
    async def _truncate_application_tables():
        database_url = os.environ["DATABASE_URL"].replace(
            "postgresql+asyncpg://",
            "postgresql://",
        )
        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(
                """
                TRUNCATE TABLE
                    credit_payments,
                    webhook_events,
                    transactions,
                    billing_periods,
                    daily_yields,
                    budget_limits,
                    investment_prices,
                    investment_lots
                RESTART IDENTITY CASCADE
                """
            )
        finally:
            await connection.close()

    @staticmethod
    def _parsed_purchase():
        return OllamaParseResult(
            card_tail="0000",
            account_type="credit",
            amount=Decimal("1500.00"),
            merchant="TEST SHOP",
            category="Другое",
            is_expense=True,
            balance_after=Decimal("148500.00"),
            type="purchase",
            is_grace_safe=True,
            card="TEST0000",
        )

    def test_webhook_worker_financial_logic_and_duplicate_delivery(self):
        with patch.object(process_sms_task, "delay") as delay:
            with TestClient(app) as client:
                response = client.post(
                    "/api/sber-webhook",
                    json={"sms_text": self.sms_text},
                    headers={"Idempotency-Key": self.idempotency_key},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "queued")
        transaction_id = response.json()["transaction_id"]
        delay.assert_called_once_with(transaction_id)

        parse_sms = AsyncMock(
            return_value=(self._parsed_purchase(), '{"type":"purchase"}')
        )
        with patch.object(ollama_client, "parse_sms", new=parse_sms):
            process_sms_task.run(transaction_id)
        parse_sms.assert_awaited_once_with(self.sms_text)

        with TestClient(app) as client:
            periods_response = client.get("/api/finance/periods")
            limit_response = client.get("/api/finance/limit")

        self.assertEqual(periods_response.status_code, 200)
        periods = periods_response.json()
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0]["month"], "2026-10")
        self.assertEqual(periods[0]["total_spent"], 1500.0)
        self.assertEqual(periods[0]["outstanding"], 1500.0)
        self.assertEqual(periods[0]["grace_deadline"], "2027-02-28")
        self.assertFalse(periods[0]["is_closed"])

        self.assertEqual(limit_response.status_code, 200)
        self.assertEqual(limit_response.json()["total_unpaid"], 1500.0)
        self.assertEqual(limit_response.json()["available_limit"], 148500.0)

        with patch.object(process_sms_task, "delay") as duplicate_delay:
            with TestClient(app) as client:
                duplicate_response = client.post(
                    "/api/sber-webhook",
                    json={"sms_text": self.sms_text},
                    headers={"Idempotency-Key": self.idempotency_key},
                )

        self.assertEqual(duplicate_response.status_code, 200)
        self.assertEqual(duplicate_response.json()["status"], "duplicate")
        self.assertEqual(
            duplicate_response.json()["transaction_id"],
            transaction_id,
        )
        duplicate_delay.assert_called_once_with(transaction_id)

        must_not_parse = AsyncMock(
            side_effect=AssertionError("duplicate task reached Ollama")
        )
        with patch.object(ollama_client, "parse_sms", new=must_not_parse):
            process_sms_task.run(transaction_id)
        must_not_parse.assert_not_awaited()

        with TestClient(app) as client:
            final_periods = client.get("/api/finance/periods").json()

        self.assertEqual(len(final_periods), 1)
        self.assertEqual(final_periods[0]["total_spent"], 1500.0)
        self.assertEqual(final_periods[0]["outstanding"], 1500.0)


if __name__ == "__main__":
    unittest.main()
