import logging
from datetime import timedelta
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval, async_track_point_in_time
from homeassistant.util import dt as dt_util
from .const import DOMAIN
from .api import LantaAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    data = config_entry.data
    session = async_get_clientsession(hass)
    api = LantaAPI(data["phone"])
    api.token = data["token"]

    payments_data = await api.get_payments(session)
    flat_id = None
    address = None
    for payment in payments_data:
        if "flatId" in payment:
            flat_id = payment["flatId"]
            address = payment.get("address", "")
            break

    if not flat_id:
        _LOGGER.error("Не удалось найти flatId для управления настройками")
        return False

    switches = [
        LantaIntercomSwitch(
            api=api,
            session=session,
            flat_id=flat_id,
            address=address,
            setting_key="disablePlog",
            translation_key="disable_plog",
            icon="mdi:book-alert",
            on_value="f",
            off_value="t",
        ),
        LantaIntercomSwitch(
            api=api,
            session=session,
            flat_id=flat_id,
            address=address,
            setting_key="hiddenPlog",
            translation_key="hidden_plog",
            icon="mdi:book-cancel",
        ),
        LantaIntercomSwitch(
            api=api,
            session=session,
            flat_id=flat_id,
            address=address,
            setting_key="FRSDisabled",
            translation_key="frs_disabled",
            icon="mdi:face-recognition",
            on_value="f",
            off_value="t",
        ),
        LantaIntercomSwitch(
            api=api,
            session=session,
            flat_id=flat_id,
            address=address,
            setting_key="VoIP",
            translation_key="voip",
            icon="mdi:phone-forward",
        ),
        LantaIntercomSwitch(
            api=api,
            session=session,
            flat_id=flat_id,
            address=address,
            setting_key="CMS",
            translation_key="cms",
            icon="mdi:phone",
        ),
        LantaIntercomSwitch(
            api=api,
            session=session,
            flat_id=flat_id,
            address=address,
            setting_key="whiteRabbit",
            translation_key="white_rabbit",
            icon="mdi:rabbit",
            on_value="5",
            off_value="0",
        ),
        LantaAutoOpenSwitch(
            api=api,
            session=session,
            flat_id=flat_id,
            address=address,
        ),
    ]

    async_add_entities(switches)

    # Немедленная загрузка начального состояния
    try:
        data = await api.get_intercom_settings(session, flat_id)
        for switch in switches:
            switch._update_from_data(data)
    except Exception as e:
        _LOGGER.error("Ошибка начальной загрузки переключателей: %s", e)

    # Один таймер на все переключатели
    async def update_all_switches(now=None):
        try:
            data = await api.get_intercom_settings(session, flat_id)
            for switch in switches:
                switch._update_from_data(data)
        except Exception as e:
            _LOGGER.error("Ошибка обновления переключателей: %s", e)

    cancel = async_track_time_interval(hass, update_all_switches, timedelta(minutes=1))

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(config_entry.entry_id, {"cleanup_callbacks": []})
    hass.data[DOMAIN][config_entry.entry_id]["cleanup_callbacks"].append(cancel)

    return True


class LantaIntercomSwitch(SwitchEntity):
    """Переключатель настроек домофона."""

    _attr_has_entity_name = True

    def __init__(self, api, session, flat_id, address, setting_key, translation_key, icon,
                 on_value="t", off_value="f"):
        self._api = api
        self._session = session
        self._flat_id = flat_id
        self._address = address
        self._setting_key = setting_key
        self._on_value = on_value
        self._off_value = off_value
        self._attr_translation_key = translation_key
        self._attr_icon = icon
        self._attr_unique_id = f"lanta_intercom_{flat_id}_{setting_key}"
        self._attr_is_on = False

    def _update_from_data(self, data):
        new_state = data["data"].get(self._setting_key) == self._on_value
        if new_state != self._attr_is_on:
            self._attr_is_on = new_state
            self.async_write_ha_state()
            _LOGGER.debug("Состояние %s обновлено: %s", self._setting_key, new_state)

    async def async_turn_on(self, **kwargs):
        await self._api.update_intercom_settings(
            self._session,
            self._flat_id,
            {self._setting_key: self._on_value},
        )
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        await self._api.update_intercom_settings(
            self._session,
            self._flat_id,
            {self._setting_key: self._off_value},
        )
        self._attr_is_on = False
        self.async_write_ha_state()

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"intercom_{self._flat_id}")},
            "manufacturer": "Lanta",
        }


class LantaAutoOpenSwitch(SwitchEntity):
    """Автооткрытие — включает автоматическое открытие двери на 1 час."""

    _attr_has_entity_name = True
    _attr_translation_key = "auto_open"
    _attr_icon = "mdi:account-arrow-right"

    def __init__(self, api, session, flat_id, address):
        self._api = api
        self._session = session
        self._flat_id = flat_id
        self._address = address
        self._attr_unique_id = f"lanta_{flat_id}_auto_open"
        self._attr_is_on = False
        self._unsub_auto_off = None

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"intercom_{self._flat_id}")},
            "manufacturer": "Lanta",
            "entry_type": "service",
        }

    def _update_from_data(self, data):
        now = dt_util.now()
        auto_str = data["data"].get("autoOpen")
        is_on = False

        if auto_str:
            try:
                auto_time = dt_util.parse_datetime(auto_str)
                if auto_time is not None:
                    # API возвращает время без timezone — добавляем текущий
                    if auto_time.tzinfo is None:
                        auto_time = auto_time.replace(tzinfo=now.tzinfo)
                    if auto_time > now:
                        is_on = True
            except (ValueError, TypeError):
                pass

        if is_on != self._attr_is_on:
            self._attr_is_on = is_on
            self.async_write_ha_state()
            _LOGGER.debug("Автооткрытие обновлено: %s (autoOpen=%s)", is_on, auto_str)

    async def async_turn_on(self, **kwargs):
        now = dt_util.now()
        auto_time = now + timedelta(hours=1)
        time_str = auto_time.strftime("%Y-%m-%d %H:%M:%S")

        await self._api.update_intercom_settings(
            self._session,
            self._flat_id,
            {"autoOpen": time_str},
        )

        self._attr_is_on = True
        self.async_write_ha_state()
        self._schedule_auto_off()

    async def async_turn_off(self, **kwargs):
        now = dt_util.now()
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")

        await self._api.update_intercom_settings(
            self._session,
            self._flat_id,
            {"autoOpen": time_str},
        )

        self._attr_is_on = False
        self.async_write_ha_state()
        self._cancel_auto_off()

    def _schedule_auto_off(self):
        self._cancel_auto_off()
        self._unsub_auto_off = async_track_point_in_time(
            self.hass,
            self._async_auto_expired,
            dt_util.now() + timedelta(hours=1),
        )
        _LOGGER.debug("Автоотключение автооткрытия запланировано через 1 час")

    def _cancel_auto_off(self):
        if self._unsub_auto_off:
            self._unsub_auto_off()
            self._unsub_auto_off = None

    async def _async_auto_expired(self, now):
        """Час прошёл — выключаем автомат."""
        self._attr_is_on = False
        self.async_write_ha_state()
        self._unsub_auto_off = None
        _LOGGER.debug("Автооткрытие автоматически отключено по таймеру")

    async def async_will_remove_from_hass(self):
        self._cancel_auto_off()
