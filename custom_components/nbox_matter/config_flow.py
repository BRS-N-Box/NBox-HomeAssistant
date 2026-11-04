import logging
import traceback
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN, CONF_NODE_ID, 
    CONF_TRIGGER_LED1, CONF_TRIGGER_LED2, CONF_TRIGGER_LED3, CONF_TRIGGER_LED4,
    DEFAULT_TRIGGER_LED1, DEFAULT_TRIGGER_LED2, DEFAULT_TRIGGER_LED3, DEFAULT_TRIGGER_LED4
)

_LOGGER = logging.getLogger(__name__)

class NBoxConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for N-Box Matter."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial setup step."""
        errors = {}
        guessed_node_id = None
        
        # --- FORM SUBMISSION ---
        # If user already clicked submit, process it immediately
        if user_input is not None:
            return self.async_create_entry(
                title="N-Box Notification Sensors",
                data=user_input
            )

        _LOGGER.error("[NBOX DISCOVERY] ---> STARTING AUTO-DISCOVERY SCAN <---")
        
        # --- SAFE DISCOVERY BLOCK ---
        try:
            ent_reg = er.async_get(self.hass)
            dev_reg = dr.async_get(self.hass)
            
            # 1. Ask the Entity Registry for the light
            _LOGGER.error(f"[NBOX DISCOVERY] Looking for entity: {DEFAULT_TRIGGER_LED1}")
            entity_entry = ent_reg.async_get(DEFAULT_TRIGGER_LED1)
            
            # Fallback: Find any n_box light if LED1 was renamed
            if not entity_entry:
                _LOGGER.error(f"[NBOX DISCOVERY] {DEFAULT_TRIGGER_LED1} not found. Scanning all entities...")
                for ent_id, entity in ent_reg.entities.items():
                    if entity.platform == "matter" and "n_box" in ent_id:
                        _LOGGER.error(f"[NBOX DISCOVERY] Found fallback entity: {ent_id}")
                        entity_entry = entity
                        break

            # 2. Ask the Light what physical Device it belongs to
            if entity_entry:
                _LOGGER.error(f"[NBOX DISCOVERY] Entity mapped to Device ID: {entity_entry.device_id}")
                if entity_entry.device_id:
                    device_entry = dev_reg.async_get(entity_entry.device_id)
                    
                    if device_entry:
                        _LOGGER.error(f"[NBOX DISCOVERY] Found Device! Name: '{device_entry.name}', Identifiers: {device_entry.identifiers}")
                        
                        # 3. Look at the Matter identifiers stamped on the hardware
                        for identifier in device_entry.identifiers:
                            _LOGGER.error(f"[NBOX DISCOVERY] Inspecting Identifier: {identifier}")
                            
                            if identifier[0] == "matter":
                                matter_str = str(identifier[1])
                                _LOGGER.error(f"[NBOX DISCOVERY] Found Matter String: {matter_str}")
                                
                                try:
                                    # Split by hyphen. The Node ID is in hex format at parts[-2]
                                    parts = matter_str.split("-")
                                    _LOGGER.error(f"[NBOX DISCOVERY] Split parts array: {parts}")
                                    if len(parts) >= 2:
                                        hex_node_id = parts[-2] # Grabs '000000000000007A'
                                        guessed_node_id = int(hex_node_id, 16) # Safely converts hex to decimal integer (122)
                                        _LOGGER.error(f"[NBOX DISCOVERY] SUCCESS! Extracted Node ID (Decimal): {guessed_node_id}")
                                        break
                                except Exception as parse_e:
                                    _LOGGER.error(f"[NBOX DISCOVERY] Failed to parse Node ID from string: {parse_e}")
                    else:
                        _LOGGER.error("[NBOX DISCOVERY] Device ID not found in device registry!")
                else:
                    _LOGGER.error("[NBOX DISCOVERY] Entity exists but has no linked device_id.")
            else:
                _LOGGER.error("[NBOX DISCOVERY] No N-Box entity found in registry.")

        except Exception as e:
            # If ANYTHING crashes, catch it here so the UI doesn't blow up
            _LOGGER.error(f"[NBOX DISCOVERY] CRITICAL CRASH DURING DISCOVERY: {e}")
            _LOGGER.error(traceback.format_exc())

        if not guessed_node_id:
            _LOGGER.error("[NBOX DISCOVERY] Auto-discovery completely failed. Rendering manual fallback form.")
            errors["base"] = "not_paired"

        # --- RENDER THE UI FORM SAFELY ---
        schema_dict = {}
        if guessed_node_id is not None:
            schema_dict[vol.Required(CONF_NODE_ID, default=guessed_node_id)] = int
        else:
            schema_dict[vol.Required(CONF_NODE_ID)] = int

        schema_dict.update({
            vol.Optional(CONF_TRIGGER_LED1, default=DEFAULT_TRIGGER_LED1): str,
            vol.Optional(CONF_TRIGGER_LED2, default=DEFAULT_TRIGGER_LED2): str,
            vol.Optional(CONF_TRIGGER_LED3, default=DEFAULT_TRIGGER_LED3): str,
            vol.Optional(CONF_TRIGGER_LED4, default=DEFAULT_TRIGGER_LED4): str,
        })

        data_schema = vol.Schema(schema_dict)

        return self.async_show_form(
            step_id="user", 
            data_schema=data_schema,
            errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Tell HA we have an Options Menu."""
        return NBoxOptionsFlowHandler(config_entry)


class NBoxOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle the Options (Configure) menu after installation."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        errors = {}
        opts = self._entry.options
        data = self._entry.data

        # Load the currently active values
        current_node_id = opts.get(CONF_NODE_ID, data.get(CONF_NODE_ID, 121))
        current_led1 = opts.get(CONF_TRIGGER_LED1, data.get(CONF_TRIGGER_LED1, DEFAULT_TRIGGER_LED1))
        current_led2 = opts.get(CONF_TRIGGER_LED2, data.get(CONF_TRIGGER_LED2, DEFAULT_TRIGGER_LED2))
        current_led3 = opts.get(CONF_TRIGGER_LED3, data.get(CONF_TRIGGER_LED3, DEFAULT_TRIGGER_LED3))
        current_led4 = opts.get(CONF_TRIGGER_LED4, data.get(CONF_TRIGGER_LED4, DEFAULT_TRIGGER_LED4))

        if user_input is not None:
            # If the user checked the "Auto-Discover" box, perform the scan
            if user_input.get("auto_discover"):
                ent_reg = er.async_get(self.hass)
                dev_reg = dr.async_get(self.hass)
                guessed_node_id = None
                
                trigger_entity = user_input.get(CONF_TRIGGER_LED1, current_led1)
                entity_entry = ent_reg.async_get(trigger_entity)
                
                # Fallback: Find any n_box light if the trigger entity doesn't match
                if not entity_entry:
                    for ent_id, entity in ent_reg.entities.items():
                        if entity.platform == "matter" and "n_box" in ent_id:
                            entity_entry = entity
                            break

                # Hop to the Device Registry to extract the true Node ID
                if entity_entry and entity_entry.device_id:
                    device_entry = dev_reg.async_get(entity_entry.device_id)
                    if device_entry:
                        for identifier in device_entry.identifiers:
                            if identifier[0] == "matter":
                                try:
                                    matter_str = str(identifier[1])
                                    parts = matter_str.split("-")
                                    if len(parts) >= 2:
                                        hex_node_id = parts[-2]
                                        guessed_node_id = int(hex_node_id, 16) # Convert hex string to decimal
                                        break
                                except Exception:
                                    pass
                
                if guessed_node_id is not None:
                    current_node_id = guessed_node_id
                else:
                    errors["base"] = "not_paired"

                # Keep the other text boxes matching what the user just submitted
                current_led1 = user_input.get(CONF_TRIGGER_LED1, current_led1)
                current_led2 = user_input.get(CONF_TRIGGER_LED2, current_led2)
                current_led3 = user_input.get(CONF_TRIGGER_LED3, current_led3)
                current_led4 = user_input.get(CONF_TRIGGER_LED4, current_led4)

            # If Auto-Discover is UNCHECKED, process the final Save normally
            else:
                user_input.pop("auto_discover", None) # Remove our fake button from the saved database
                return self.async_create_entry(title="", data=user_input)

        # Build the form
        data_schema = vol.Schema({
            vol.Required(CONF_NODE_ID, default=current_node_id): int,
            vol.Optional("auto_discover", default=False): bool, # Acts as our "Scan" button
            vol.Optional(CONF_TRIGGER_LED1, default=current_led1): str,
            vol.Optional(CONF_TRIGGER_LED2, default=current_led2): str,
            vol.Optional(CONF_TRIGGER_LED3, default=current_led3): str,
            vol.Optional(CONF_TRIGGER_LED4, default=current_led4): str,
        })

        return self.async_show_form(step_id="init", data_schema=data_schema, errors=errors)