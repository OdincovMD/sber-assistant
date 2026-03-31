import random
import httpx
from app.config import get_settings

class VkBotClient:
    """Асинхронный клиент для отправки сообщений в ВКонтакте через Bot API."""

    def __init__(self, token: str, user_id: int, api_version: str = "5.199"):
        self.token = token
        self.user_id = user_id
        self.api_version = api_version
        self.base_url = "https://api.vk.com/method"
        self.session = httpx.AsyncClient(timeout=10)

    async def send_message(self, text: str) -> dict:
        """Отправить сообщение пользователю.

        Параметры:
            text: Текст сообщения.
        Возвращает:
            JSON‑ответ VK API.
        """
        payload = {
            "peer_id": self.user_id,
            "message": text,
            "random_id": random.getrandbits(31),  # 32‑битное случайное число
            "access_token": self.token,
            "v": self.api_version,
        }
        resp = await self.session.post(f"{self.base_url}/messages.send", data=payload)
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        await self.session.aclose()
