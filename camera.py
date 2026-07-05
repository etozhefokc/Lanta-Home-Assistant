import logging
from datetime import timedelta
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from .const import DOMAIN
from .api import LantaAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    data = config_entry.data
    session = async_get_clientsession(hass)
    api = LantaAPI(data["phone"])
    api.token = data["token"]

    # Адреса и flat_id из платежей
    address_map = {}
    flat_by_house = {}
    try:
        payments_data = await api.get_payments(session)
        for payment in payments_data:
            house_id = str(payment["houseId"])
            if "address" in payment:
                address_map[house_id] = payment["address"]
            if "flatId" in payment:
                flat_by_house[house_id] = payment["flatId"]
    except Exception:
        pass

    try:
        cameras_data = await api.get_cameras(session)
    except Exception as e:
        _LOGGER.error("Ошибка при получении списка камер: %s", e)
        return False

    cameras = []
    for cam in cameras_data:
        house_id = str(cam["houseId"])
        address = address_map.get(house_id, f"House {house_id}")

        cameras.append(LantaCamera(
            name=cam["name"],
            url=cam["url"],
            token=cam["token"],
            house_id=house_id,
            flat_id=flat_by_house.get(house_id),
            address=address,
        ))

    updater = CameraTokenUpdater(hass, api, cameras)

    cancel = async_track_time_interval(
        hass, updater.async_update_tokens, timedelta(minutes=30),
    )
    updater._unsub_listener = cancel

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(config_entry.entry_id, {"cleanup_callbacks": []})
    hass.data[DOMAIN][config_entry.entry_id]["cleanup_callbacks"].append(
        updater.async_unload,
    )

    async_add_entities(cameras)
    _LOGGER.info("Успешно инициализировано %d камер", len(cameras))
    return True


class CameraTokenUpdater:
    """Обновление токенов камер."""

    def __init__(self, hass, api, cameras):
        self.hass = hass
        self.api = api
        self.cameras = cameras
        self._unsub_listener = None

    def async_unload(self):
        if self._unsub_listener:
            self._unsub_listener()
            self._unsub_listener = None

    async def async_update_tokens(self, _):
        try:
            session = async_get_clientsession(self.hass)
            new_data = await self.api.get_cameras(session)

            if not new_data:
                _LOGGER.warning("Не получены данные для обновления токенов")
                return

            token_map = {cam["url"]: cam["token"] for cam in new_data}
            updated = 0

            for camera in self.cameras:
                if camera._url in token_map:
                    new_token = token_map[camera._url]
                    camera._token = new_token
                    camera._stream_url = f"{camera._url}/index.m3u8?token={new_token}"
                    updated += 1

            _LOGGER.debug("Обновлено %d/%d токенов", updated, len(self.cameras))

        except Exception as e:
            _LOGGER.error("Ошибка обновления токенов: %s", e)


class LantaCamera(Camera):
    """Представление камеры Lanta в Home Assistant."""

    def __init__(self, name, url, token, house_id, flat_id, address):
        super().__init__()
        self._name = name
        self._url = url.rstrip("/")
        self._token = token
        self._house_id = str(house_id)
        self._flat_id = str(flat_id) if flat_id else None
        self._address = address
        self._attr_icon = "mdi:cctv"

        self._stream_url = f"{self._url}/index.m3u8?token={self._token}"
        self._preview_url = f"{self._url}/preview.mp4?token={self._token}"

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self._house_id}_{self._url.split('//')[-1].replace('/', '_')}"

    @property
    def name(self):
        return self._name

    @property
    def available(self):
        return bool(self._url and self._token)

    @property
    def supported_features(self):
        return CameraEntityFeature.STREAM

    @property
    def device_info(self):
        identifier = f"intercom_{self._flat_id}" if self._flat_id else f"house_{self._house_id}"
        return {
            "identifiers": {(DOMAIN, identifier)},
            "manufacturer": "Lanta",
            "entry_type": "service",
        }

    async def async_camera_image(self, width=None, height=None):
        if not self._preview_url:
            return None

        try:
            websession = async_get_clientsession(self.hass)
            async with websession.get(self._preview_url) as response:
                if response.status == 200:
                    return await response.read()
                _LOGGER.warning(
                    "Ошибка при получении изображения: статус %s", response.status,
                )
        except Exception as e:
            _LOGGER.error("Исключение при получении изображения с %s: %s", self._name, e)

        return None

    async def stream_source(self):
        if not self.available:
            _LOGGER.warning("Камера %s недоступна", self._name)
            return None
        return self._stream_url

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
