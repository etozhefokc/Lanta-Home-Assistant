import logging
from datetime import timedelta
from homeassistant.components.sensor import SensorEntity
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

    sensors = []
    flat_id = None
    house_id = None
    address = None

    try:
        payments_data = await api.get_payments(session)

        for payment in payments_data:
            addr = payment.get("address", "")
            for account in payment["accounts"]:
                flat_id = payment["flatId"]
                house_id = payment["houseId"]
                address = address or addr

                sensors.extend([
                    LantaBalanceSensor(
                        api=api,
                        initial_data=account,
                        unique_id=f"{payment['houseId']}_balance",
                        address=addr,
                        house_id=payment["houseId"],
                        flat_id=payment["flatId"],
                    ),
                    LantaBonusSensor(
                        api=api,
                        initial_data=account,
                        unique_id=f"{payment['houseId']}_bonus",
                        address=addr,
                        house_id=payment["houseId"],
                        flat_id=payment["flatId"],
                    ),
                    LantaAccountLink(
                        api=api,
                        initial_data=account,
                        unique_id=f"{payment['houseId']}_lcab",
                        address=addr,
                        house_id=payment["houseId"],
                        flat_id=payment["flatId"],
                    ),
                ])

        if flat_id:
            sensors.append(
                LantaDoorCodeSensor(
                    api=api,
                    session=session,
                    unique_id=f"{house_id}_doorcode",
                    address=address,
                    house_id=house_id,
                    flat_id=flat_id,
                )
            )

    except Exception as e:
        _LOGGER.error("Ошибка при создании сенсоров: %s", e)
        return False

    async def update_payments(now=None):
        try:
            data = await api.get_payments(session)
            for sensor in sensors:
                if hasattr(sensor, "async_update_payments_data"):
                    await sensor.async_update_payments_data(data)
        except Exception as e:
            _LOGGER.error("Ошибка обновления платежей: %s", e)

    async def update_intercom(now=None):
        try:
            for sensor in sensors:
                if hasattr(sensor, "async_update_intercom_data"):
                    await sensor.async_update_intercom_data()
        except Exception as e:
            _LOGGER.error("Ошибка обновления домофона: %s", e)

    cancel_payments = async_track_time_interval(hass, update_payments, timedelta(minutes=5))
    cancel_intercom = async_track_time_interval(hass, update_intercom, timedelta(minutes=5))

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(config_entry.entry_id, {"cleanup_callbacks": []})
    hass.data[DOMAIN][config_entry.entry_id]["cleanup_callbacks"].extend([
        cancel_payments,
        cancel_intercom,
    ])
    # Для немедленного обновления после сброса кода
    hass.data[DOMAIN]["refresh_intercom"] = update_intercom

    async_add_entities(sensors)

    # Немедленная загрузка кода домофона
    for sensor in sensors:
        if hasattr(sensor, "async_update_intercom_data"):
            await sensor.async_update_intercom_data()

    return True


class LantaPaymentSensor(SensorEntity):
    """Базовый сенсор для данных из платежей."""

    _attr_has_entity_name = True

    def __init__(self, api, initial_data, unique_id, address, house_id, flat_id, value_key):
        self._api = api
        self._attr_unique_id = unique_id
        self._attr_native_value = initial_data[value_key]
        self._address = address
        self._house_id = house_id
        self._flat_id = flat_id
        self._lcab_url = initial_data["lcab"]
        self._value_key = value_key

    async def async_update_payments_data(self, payments_data):
        for payment in payments_data:
            if str(payment["houseId"]) == self._house_id:
                for account in payment["accounts"]:
                    self._attr_native_value = account[self._value_key]
                    self._lcab_url = account["lcab"]
                    self.async_write_ha_state()
                    return

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"intercom_{self._flat_id}")},
            "manufacturer": "Lanta",
        }


class LantaBalanceSensor(LantaPaymentSensor):
    _attr_translation_key = "balance"
    _attr_native_unit_of_measurement = "₽"
    _attr_icon = "mdi:cash"

    def __init__(self, api, initial_data, unique_id, address, house_id, flat_id):
        super().__init__(api, initial_data, unique_id, address, house_id, flat_id, "balance")


class LantaBonusSensor(LantaPaymentSensor):
    _attr_translation_key = "bonus"
    _attr_native_unit_of_measurement = "₽"
    _attr_icon = "mdi:gift"

    def __init__(self, api, initial_data, unique_id, address, house_id, flat_id):
        super().__init__(api, initial_data, unique_id, address, house_id, flat_id, "bonus")


class LantaAccountLink(LantaPaymentSensor):
    _attr_translation_key = "account_link"
    _attr_icon = "mdi:account-box"

    def __init__(self, api, initial_data, unique_id, address, house_id, flat_id):
        super().__init__(api, initial_data, unique_id, address, house_id, flat_id, "contractPayName")

    @property
    def extra_state_attributes(self):
        return {
            "lcab_url": self._lcab_url,
            "house_id": self._house_id,
            "flat_id": self._flat_id,
        }


class LantaDoorCodeSensor(SensorEntity):
    """Сенсор для отображения кода домофона."""

    _attr_has_entity_name = True
    _attr_translation_key = "door_code"
    _attr_icon = "mdi:form-textbox-password"
    _attr_native_unit_of_measurement = None

    def __init__(self, api, session, unique_id, address, house_id, flat_id):
        self._api = api
        self._session = session
        self._attr_unique_id = unique_id
        self._attr_native_value = None
        self._address = address
        self._house_id = house_id
        self._flat_id = flat_id
        self._additional_data = {}

    async def async_update_intercom_data(self):
        try:
            response = await self._api.get_intercom_settings(self._session, int(self._flat_id))
            data = response.get("data", {})

            door_code = data.get("doorCode")
            if door_code:
                self._attr_native_value = door_code

            self._additional_data = {
                "allow_door_code": data.get("allowDoorCode"),
                "cms_enabled": data.get("CMS"),
                "voip_enabled": data.get("VoIP"),
                "auto_open": data.get("autoOpen"),
                "white_rabbit": data.get("whiteRabbit"),
                "disable_plog": data.get("disablePlog"),
                "hidden_plog": data.get("hiddenPlog"),
                "frs_disabled": data.get("FRSDisabled"),
            }

            self.async_write_ha_state()
            _LOGGER.debug("Обновлен код домофона для кв. %s: %s", self._flat_id, door_code)

        except Exception as e:
            _LOGGER.error("Ошибка обновления кода домофона: %s", e)

    @property
    def extra_state_attributes(self):
        attrs = {
            "flat_id": self._flat_id,
            "house_id": self._house_id,
        }
        attrs.update(self._additional_data)
        return attrs

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"intercom_{self._flat_id}")},
            "manufacturer": "Lanta",
        }
