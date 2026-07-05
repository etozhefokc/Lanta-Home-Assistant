import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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

    buttons = []
    for addr in data["doors"]:
        house_id = str(addr.get("houseId", ""))
        address = address_map.get(house_id, addr.get("address", ""))
        for door in addr.get("doors", []):
            buttons.append(LantaDoorButton(
                api=api,
                session=session,
                name=door.get("name"),
                domophone_id=int(door["domophoneId"]),
                door_id=int(door["doorId"]),
                address=address,
                house_id=house_id,
                flat_id=flat_by_house.get(house_id),
                icon_type=door.get("icon"),
            ))

    # Кнопка сброса кода для каждой квартиры
    seen_flats = set()
    for addr in data["doors"]:
        house_id = str(addr.get("houseId", ""))
        flat_id = flat_by_house.get(house_id)
        if flat_id and flat_id not in seen_flats:
            seen_flats.add(flat_id)
            address = address_map.get(house_id, addr.get("address", ""))
            buttons.append(LantaResetCodeButton(
                api=api,
                session=session,
                flat_id=flat_id,
                address=address,
            ))

    async_add_entities(buttons)


class LantaDoorButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "open_door"

    def __init__(self, api, session, name, domophone_id, door_id, address, house_id, flat_id, icon_type=None):
        if name:
            self._attr_name = name
        self._attr_unique_id = f"{house_id}_{domophone_id}_{door_id}"
        self.api = api
        self.session = session
        self.domophone_id = domophone_id
        self.door_id = door_id
        self.house_id = house_id
        self.address = address
        self._flat_id = flat_id
        self.icon_type = icon_type

    @property
    def icon(self):
        return self._get_icon_by_type(self.icon_type)

    def _get_icon_by_type(self, icon_type):
        return {
            "entrance": "mdi:door",
            "gate": "mdi:gate",
            "wicket": "mdi:walk",
            "garage": "mdi:garage",
        }.get(icon_type, "mdi:door")

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"intercom_{self._flat_id}") if self._flat_id else (DOMAIN, str(self.house_id))},
            "manufacturer": "Lanta",
            "entry_type": "service",
        }

    async def async_press(self):
        await self.api.open_door(self.session, self.domophone_id, self.door_id)


class LantaResetCodeButton(ButtonEntity):
    """Кнопка сброса кода домофона."""

    _attr_has_entity_name = True
    _attr_translation_key = "reset_code"
    _attr_icon = "mdi:form-textbox"

    def __init__(self, api, session, flat_id, address):
        self._api = api
        self._session = session
        self._flat_id = flat_id
        self._address = address
        self._attr_unique_id = f"lanta_{flat_id}_reset_code"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"intercom_{self._flat_id}")},
            "manufacturer": "Lanta",
            "entry_type": "service",
        }

    async def async_press(self):
        _LOGGER.debug("Сброс кода домофона для кв. %s", self._flat_id)
        await self._api.reset_code(self._session, self._flat_id)
        # Немедленное обновление кода на сенсоре
        refresh = self.hass.data.get(DOMAIN, {}).get("refresh_intercom")
        if refresh:
            await refresh()
