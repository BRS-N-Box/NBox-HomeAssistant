"""N-Box Matter extension: phone notifications from the N-Box as sensors."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .manager import NBoxManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]
ENTRY_TITLE = "N-Box Notification Sensors"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up N-Box Matter from a config entry."""
    if entry.title != ENTRY_TITLE:
        hass.config_entries.async_update_entry(entry, title=ENTRY_TITLE)

    manager = NBoxManager(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = manager

    # Entities first (they listen to the manager), then start finding the N-Box.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await manager.async_start()

    # Reload when the options (Configure menu) change.
    entry.async_on_unload(entry.add_update_listener(_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    manager: NBoxManager | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if manager is not None:
        await manager.async_stop()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply changed options right away."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing a device from the UI."""
    _LOGGER.info("[NBOX] Removing device %s from the registry", device_entry.id)
    return True
