"""Config flow for the N-Box Matter extension.

Nothing needs to be typed in: the N-Box is found on Matter by itself (and found
again after it is paired again). The Configure menu only offers optional extras.
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_AUTO_CLEANUP,
    CONF_NODE_ID,
    DEFAULT_AUTO_CLEANUP,
    DOMAIN,
)
from .manager import find_nbox_nodes, get_matter_client

_LOGGER = logging.getLogger(__name__)


def _detect_text(hass) -> str:
    """A one-line status for the setup screen."""
    client = get_matter_client(hass)
    if client is None:
        return "Matter is not ready yet - the N-Box will be picked up as soon as it is."
    nodes = find_nbox_nodes(client)
    if not nodes:
        return "No N-Box found yet - it will be picked up automatically once it is paired with Matter."
    ids = ", ".join(str(n.node_id) for n in sorted(nodes, key=lambda n: n.node_id))
    return f"N-Box found on Matter node {ids}."


class NBoxConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the setup."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="N-Box Notification Sensors", data={})
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
            description_placeholders={"status": _detect_text(self.hass)},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return NBoxOptionsFlow()


class NBoxOptionsFlow(config_entries.OptionsFlow):
    """The Configure menu."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(CONF_NODE_ID, default=options.get(CONF_NODE_ID, 0)): vol.All(
                    int, vol.Range(min=0)
                ),
                vol.Optional(
                    CONF_AUTO_CLEANUP,
                    default=options.get(CONF_AUTO_CLEANUP, DEFAULT_AUTO_CLEANUP),
                ): bool,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={"status": _detect_text(self.hass)},
        )
