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

    def setUp(self):
        asyncio.run(self._truncate_application_tables())

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
                    unallocated_credits,
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

    @staticmethod
    def _parsed_payment():
        return OllamaParseResult(
            card_tail="0000",
            account_type="credit",
            amount=Decimal("2000.00"),
            merchant="CARD PAYMENT",
            category="Перевод между счетами",
            is_expense=False,
            balance_after=Decimal("152000.00"),
            type="payment",
            is_grace_safe=True,
            card="TEST0000",
        )

    @staticmethod
    def _parsed_purchase_for(amount: str, merchant: str):
        return OllamaParseResult(
            card_tail="0000",
            account_type="credit",
            amount=Decimal(amount),
            merchant=merchant,
            category="Другое",
            is_expense=True,
            balance_after=None,
            type="purchase",
            is_grace_safe=True,
            card="TEST0000",
        )

    def _deliver_and_process(self, sms_text, idempotency_key, parsed_result):
        with patch.object(process_sms_task, "delay") as delay:
            with TestClient(app) as client:
                response = client.post(
                    "/api/sber-webhook",
                    json={"sms_text": sms_text},
                    headers={"Idempotency-Key": idempotency_key},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "queued")
        transaction_id = response.json()["transaction_id"]
        delay.assert_called_once_with(transaction_id)

        parse_sms = AsyncMock(
            return_value=(parsed_result, f'{{"type":"{parsed_result.type}"}}')
        )
        with patch.object(ollama_client, "parse_sms", new=parse_sms):
            process_sms_task.run(transaction_id)
        parse_sms.assert_awaited_once_with(sms_text)

        with TestClient(app) as client:
            status_response = client.get(
                f"/api/transactions/{transaction_id}"
            )
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()["status"], "completed")
        self.assertIsNotNone(status_response.json()["processed_at"])
        return transaction_id

    def test_unparseable_sms_exposes_failed_status(self):
        sms_text = "19.11.2026 Нераспознанное банковское сообщение"
        with patch.object(process_sms_task, "delay") as delay:
            with TestClient(app) as client:
                response = client.post(
                    "/api/sber-webhook",
                    json={"sms_text": sms_text},
                    headers={
                        "Idempotency-Key": "integration-unparseable-2026-11-19"
                    },
                )

        transaction_id = response.json()["transaction_id"]
        delay.assert_called_once_with(transaction_id)

        parse_sms = AsyncMock(return_value=(None, "not-json"))
        with patch.object(ollama_client, "parse_sms", new=parse_sms):
            process_sms_task.run(transaction_id)

        with TestClient(app) as client:
            status_response = client.get(
                f"/api/transactions/{transaction_id}"
            )
            missing_response = client.get("/api/transactions/999999")

        self.assertEqual(status_response.status_code, 200)
        status = status_response.json()
        self.assertEqual(status["status"], "failed")
        self.assertEqual(
            status["error"],
            "LLM response could not be parsed",
        )
        self.assertIsNotNone(status["processed_at"])
        self.assertEqual(missing_response.status_code, 404)

        with patch.object(
            process_sms_task,
            "delay",
            side_effect=RuntimeError("Redis unavailable"),
        ) as unavailable_delay:
            with TestClient(app) as client:
                unavailable_response = client.post(
                    f"/api/transactions/{transaction_id}/retry"
                )
                still_failed = client.get(
                    f"/api/transactions/{transaction_id}"
                ).json()

        unavailable_delay.assert_called_once_with(transaction_id)
        self.assertEqual(unavailable_response.status_code, 503)
        self.assertEqual(still_failed["status"], "failed")
        self.assertEqual(
            still_failed["error"],
            "Background queue unavailable: RuntimeError",
        )

        with patch.object(process_sms_task, "delay") as retry_delay:
            with TestClient(app) as client:
                retry_response = client.post(
                    f"/api/transactions/{transaction_id}/retry"
                )
                duplicate_retry = client.post(
                    f"/api/transactions/{transaction_id}/retry"
                )
                queued_status = client.get(
                    f"/api/transactions/{transaction_id}"
                ).json()

        retry_delay.assert_called_once_with(transaction_id)
        self.assertEqual(retry_response.status_code, 202)
        self.assertEqual(
            retry_response.json(),
            {"status": "queued", "transaction_id": transaction_id},
        )
        self.assertEqual(duplicate_retry.status_code, 409)
        self.assertEqual(queued_status["status"], "queued")
        self.assertIsNone(queued_status["error"])
        self.assertIsNone(queued_status["processed_at"])

        recovered_parse = AsyncMock(
            return_value=(
                self._parsed_purchase_for("250.00", "RECOVERED SHOP"),
                '{"type":"purchase"}',
            )
        )
        with patch.object(ollama_client, "parse_sms", new=recovered_parse):
            process_sms_task.run(transaction_id)

        with TestClient(app) as client:
            recovered = client.get(
                f"/api/transactions/{transaction_id}"
            ).json()
            completed_retry = client.post(
                f"/api/transactions/{transaction_id}/retry"
            )

        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["merchant"], "RECOVERED SHOP")
        self.assertEqual(completed_retry.status_code, 409)

    def test_financial_validation_exposes_rejected_status(self):
        sms_text = "19.11.2026 14:00 Покупка 200000 руб. LARGE SHOP"
        parsed_result = self._parsed_purchase_for(
            "200000.00",
            "LARGE SHOP",
        )
        with patch.object(process_sms_task, "delay") as delay:
            with TestClient(app) as client:
                response = client.post(
                    "/api/sber-webhook",
                    json={"sms_text": sms_text},
                    headers={
                        "Idempotency-Key": "integration-rejected-2026-11-19"
                    },
                )

        transaction_id = response.json()["transaction_id"]
        delay.assert_called_once_with(transaction_id)

        parse_sms = AsyncMock(
            return_value=(parsed_result, '{"type":"purchase"}')
        )
        with patch.object(ollama_client, "parse_sms", new=parse_sms):
            process_sms_task.run(transaction_id)

        with TestClient(app) as client:
            status_response = client.get(
                f"/api/transactions/{transaction_id}"
            )
            periods = client.get("/api/finance/periods").json()

        self.assertEqual(status_response.status_code, 200)
        status = status_response.json()
        self.assertEqual(status["status"], "rejected")
        self.assertIn("Превышение лимита", status["error"])
        self.assertEqual(status["amount"], 200000.0)
        self.assertEqual(periods, [])

    def test_worker_exception_is_persisted_as_failed_status(self):
        sms_text = "19.11.2026 15:00 Сообщение для проверки сбоя"
        with patch.object(process_sms_task, "delay") as delay:
            with TestClient(app) as client:
                response = client.post(
                    "/api/sber-webhook",
                    json={"sms_text": sms_text},
                    headers={
                        "Idempotency-Key": "integration-worker-error-2026-11-19"
                    },
                )

        transaction_id = response.json()["transaction_id"]
        delay.assert_called_once_with(transaction_id)

        parse_sms = AsyncMock(side_effect=RuntimeError("Ollama unavailable"))
        with patch.object(ollama_client, "parse_sms", new=parse_sms):
            with self.assertRaises(RuntimeError):
                process_sms_task.run(transaction_id)

        with TestClient(app) as client:
            status_response = client.get(
                f"/api/transactions/{transaction_id}"
            )

        self.assertEqual(status_response.status_code, 200)
        status = status_response.json()
        self.assertEqual(status["status"], "failed")
        self.assertEqual(
            status["error"],
            "Background processing failed: RuntimeError",
        )
        self.assertIsNotNone(status["processed_at"])

    def test_payment_before_purchase_is_carried_forward(self):
        self._deliver_and_process(
            "01.11.2026 09:00 Пополнение кредитной карты 2000 руб.",
            "integration-advance-payment-2026-11-01",
            self._parsed_payment(),
        )

        with TestClient(app) as client:
            advance_limit = client.get("/api/finance/limit").json()

        self.assertEqual(advance_limit["total_unpaid"], 0.0)
        self.assertEqual(advance_limit["unallocated_credit"], 2000.0)
        self.assertEqual(advance_limit["available_limit"], 152000.0)

        self._deliver_and_process(
            "02.11.2026 12:00 Покупка 1500 руб. FIRST SHOP",
            "integration-advance-purchase-1-2026-11-02",
            self._parsed_purchase_for("1500.00", "FIRST SHOP"),
        )

        with TestClient(app) as client:
            partly_used_limit = client.get("/api/finance/limit").json()

        self.assertEqual(partly_used_limit["total_unpaid"], 0.0)
        self.assertEqual(partly_used_limit["unallocated_credit"], 500.0)
        self.assertEqual(partly_used_limit["available_limit"], 150500.0)

        self._deliver_and_process(
            "03.11.2026 18:00 Покупка 1000 руб. SECOND SHOP",
            "integration-advance-purchase-2-2026-11-03",
            self._parsed_purchase_for("1000.00", "SECOND SHOP"),
        )

        with TestClient(app) as client:
            final_limit = client.get("/api/finance/limit").json()
            periods = client.get("/api/finance/periods").json()
            history = client.get("/api/transactions").json()
            expenses = client.get(
                "/api/transactions",
                params={
                    "status": "completed",
                    "account_type": "credit",
                    "is_expense": "true",
                },
            ).json()
            dated = client.get(
                "/api/transactions",
                params={
                    "date_from": "2026-11-02",
                    "date_to": "2026-11-02",
                },
            ).json()
            payment = client.get(
                "/api/transactions",
                params={"transaction_type": "payment"},
            ).json()
            second_page = client.get(
                "/api/transactions",
                params={"limit": 1, "offset": 1},
            ).json()
            invalid_range = client.get(
                "/api/transactions",
                params={
                    "date_from": "2026-11-03",
                    "date_to": "2026-11-02",
                },
            )

        self.assertEqual(final_limit["total_unpaid"], 500.0)
        self.assertEqual(final_limit["unallocated_credit"], 0.0)
        self.assertEqual(final_limit["available_limit"], 149500.0)
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0]["month"], "2026-11")
        self.assertEqual(periods[0]["total_spent"], 2500.0)
        self.assertEqual(periods[0]["total_paid"], 2000.0)
        self.assertEqual(periods[0]["outstanding"], 500.0)

        self.assertEqual(history["total"], 3)
        self.assertEqual(history["limit"], 20)
        self.assertEqual(history["offset"], 0)
        self.assertEqual(
            [item["merchant"] for item in history["items"]],
            ["SECOND SHOP", "FIRST SHOP", "CARD PAYMENT"],
        )

        self.assertEqual(expenses["total"], 2)
        self.assertTrue(
            all(item["is_expense"] for item in expenses["items"])
        )
        self.assertEqual(dated["total"], 1)
        self.assertEqual(dated["items"][0]["merchant"], "FIRST SHOP")
        self.assertEqual(payment["total"], 1)
        self.assertEqual(payment["items"][0]["merchant"], "CARD PAYMENT")
        self.assertEqual(second_page["total"], 3)
        self.assertEqual(len(second_page["items"]), 1)
        self.assertEqual(second_page["items"][0]["merchant"], "FIRST SHOP")
        self.assertEqual(invalid_range.status_code, 422)

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

        with TestClient(app) as client:
            queued_response = client.get(
                f"/api/transactions/{transaction_id}"
            )
        self.assertEqual(queued_response.status_code, 200)
        self.assertEqual(queued_response.json()["status"], "queued")
        self.assertIsNone(queued_response.json()["processed_at"])

        parse_sms = AsyncMock(
            return_value=(self._parsed_purchase(), '{"type":"purchase"}')
        )
        with patch.object(ollama_client, "parse_sms", new=parse_sms):
            process_sms_task.run(transaction_id)
        parse_sms.assert_awaited_once_with(self.sms_text)

        with TestClient(app) as client:
            completed_response = client.get(
                f"/api/transactions/{transaction_id}"
            )
        self.assertEqual(completed_response.status_code, 200)
        completed = completed_response.json()
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["amount"], 1500.0)
        self.assertEqual(completed["transaction_type"], "purchase")
        self.assertIsNotNone(completed["processed_at"])

        with TestClient(app) as client:
            periods_response = client.get("/api/finance/periods")
            limit_response = client.get("/api/finance/limit")

        self.assertEqual(periods_response.status_code, 200)
        periods = periods_response.json()
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0]["month"], "2026-10")
        self.assertEqual(periods[0]["total_spent"], 1500.0)
        self.assertEqual(periods[0]["outstanding"], 1500.0)
        self.assertEqual(periods[0]["grace_deadline"], "2027-01-31")
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
