import asyncio

_API_URL = "https://dm.lanta-io.ru:543/api"


class LantaAPI:
    def __init__(self, phone):
        self.phone = phone
        self.token = None

    async def _post(self, session, path, json_data=None):
        url = f"{_API_URL}{path}"
        headers = {"Authorization": f"Bearer {self.token}"}
        async with session.post(url, headers=headers, json=json_data) as resp:
            if resp.status == 204:
                return None
            if resp.status != 200:
                raise Exception(f"API error {resp.status} on {path}")
            return await resp.json()

    async def request_code(self, session):
        return await self._post(session, "/user/requestCode", {"userPhone": self.phone})

    async def poll_for_token(self, session, timeout=180):
        for _ in range(timeout // 5):
            async with session.post(
                f"{_API_URL}/user/checkPhone",
                json={"userPhone": self.phone},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.token = data["data"]["accessToken"]
                    return self.token
                elif resp.status != 401:
                    raise Exception(f"Unexpected error: {resp}")
            await asyncio.sleep(5)
        raise TimeoutError("Authorization timed out")

    async def get_doors(self, session):
        return (await self._post(session, "/address/getAddressList"))["data"]

    async def open_door(self, session, domophone_id: int, door_id: int):
        await self._post(
            session,
            "/address/openDoor",
            {"domophoneId": domophone_id, "doorId": door_id},
        )

    async def get_cameras(self, session):
        return (await self._post(session, "/cctv/all"))["data"]

    async def get_payments(self, session):
        return (await self._post(session, "/user/getPaymentsList"))["data"]

    async def get_intercom_settings(self, session, flat_id: int):
        """Получает текущие настройки домофона."""
        return await self._post(session, "/address/intercom", {"flatId": flat_id})

    async def update_intercom_settings(self, session, flat_id: int, settings: dict):
        """Обновляет настройки домофона."""
        return await self._post(
            session,
            "/address/intercom",
            {"flatId": flat_id, "settings": settings},
        )

    async def reset_code(self, session, flat_id: int):
        """Сбрасывает код домофона, возвращает новый код."""
        return await self._post(session, "/address/resetCode", {"flatId": flat_id})