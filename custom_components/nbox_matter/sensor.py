"""Sensors: sender, app and content of the message on each N-Box LED."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, LED_ENDPOINTS, PART_NAMES
from .manager import NBoxManager


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    """Create 3 text sensors for each of the 4 LED channels."""
    manager: NBoxManager = hass.data[DOMAIN][entry.entry_id]
    dev_reg = dr.async_get(hass)

    entities: list[NBoxMessagePartSensor] = []
    for endpoint in LED_ENDPOINTS:
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{entry.entry_id}_led{endpoint}")},
            name=f"N-Box LED {endpoint}",
            manufacturer="BRS N-Sync",
            model="LED Notification Group",
        )
        for index, part in enumerate(PART_NAMES):
            entities.append(NBoxMessagePartSensor(manager, entry, endpoint, part, index))
    async_add_entities(entities)


class NBoxMessagePartSensor(SensorEntity):
    """One part (sender / app / content) of the message on one N-Box LED."""

    _attr_should_poll = False

    def __init__(self, manager: NBoxManager, entry: ConfigEntry, endpoint: int, part: str, index: int) -> None:
        self._manager = manager
        self._endpoint = endpoint
        self._index = index
        self.entity_id = f"sensor.n_box_led{endpoint}_{part.lower()}"
        self._attr_name = f"N-Box LED{endpoint} {part}"
        self._attr_unique_id = f"{entry.entry_id}_led{endpoint}_{part.lower()}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_led{endpoint}")}
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._manager.signal, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._manager.available

    @property
    def native_value(self) -> str:
        return self._manager.value(self._endpoint, self._index)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "matter_node_id": self._manager.node_id,
            "message_id": self._manager.message_id(self._endpoint),
        }
