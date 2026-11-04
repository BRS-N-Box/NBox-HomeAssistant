import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN, CONF_NODE_ID, 
    CONF_TRIGGER_LED1, CONF_TRIGGER_LED2, CONF_TRIGGER_LED3, CONF_TRIGGER_LED4,
    DEFAULT_TRIGGER_LED1, DEFAULT_TRIGGER_LED2, DEFAULT_TRIGGER_LED3, DEFAULT_TRIGGER_LED4,
    MESSAGES_CLUSTER_ID, MESSAGES_ATTRIBUTE_ID
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    """Set up sensors from a Config Entry (UI)."""
    
    # Force the title fix
    if entry.title != "N-Box Notification Sensors":
        hass.config_entries.async_update_entry(entry, title="N-Box Notification Sensors")
    
    config = entry.options if entry.options else entry.data
    node_id = config.get(CONF_NODE_ID, 121)
    
    triggers = {
        1: config.get(CONF_TRIGGER_LED1, DEFAULT_TRIGGER_LED1),
        2: config.get(CONF_TRIGGER_LED2, DEFAULT_TRIGGER_LED2),
        3: config.get(CONF_TRIGGER_LED3, DEFAULT_TRIGGER_LED3),
        4: config.get(CONF_TRIGGER_LED4, DEFAULT_TRIGGER_LED4),
    }
    
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    
    entities = []
    
    # Loop through Endpoints 1 to 4
    for ep in range(1, 5):
        trigger_entity = triggers[ep]
        
        # Explicitly create the 4 subdevices
        device = dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{entry.entry_id}_led{ep}")},
            name=f"N-Box LED {ep}",
            manufacturer="BRS N-Sync",
            model="LED Notification Group"
        )
        
        # ENTITY KIDNAPPING
        # Steal the Matter light switch and force it into our new custom subdevice
        entity_entry = ent_reg.async_get(trigger_entity)
        if entity_entry and entity_entry.device_id != device.id:
            # FIX: Downgraded from 'warning' to 'info' so it stops showing up as an error!
            _LOGGER.info(f"[NBOX] Successfully moved {trigger_entity} into N-Box LED {ep} Device Group")
            ent_reg.async_update_entity(trigger_entity, device_id=device.id)

        # Create the 3 text sensors
        entities.append(NBoxMessagePartSensor(hass, entry, trigger_entity, node_id, ep, "Sender", 0))
        entities.append(NBoxMessagePartSensor(hass, entry, trigger_entity, node_id, ep, "App", 1))
        entities.append(NBoxMessagePartSensor(hass, entry, trigger_entity, node_id, ep, "Content", 2))
        
    async_add_entities(entities)

class NBoxMessagePartSensor(SensorEntity):
    """Representation of an N-Box Matter split data sensor."""

    def __init__(self, hass, entry, trigger_entity, node_id, endpoint_id, part_name, part_index):
        self.hass = hass
        self._trigger_entity = trigger_entity
        self._node_id = node_id
        self._endpoint_id = endpoint_id
        self._part_index = part_index
        
        self.entity_id = f"sensor.n_box_led{endpoint_id}_{part_name.lower()}"
        self._attr_name = f"N-Box LED{endpoint_id} {part_name}"
        self._attr_native_value = "Waiting for data..."
        self._attr_unique_id = f"{entry.entry_id}_led{endpoint_id}_{part_name.lower()}"
        
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_led{endpoint_id}")}
        )

    async def async_added_to_hass(self):
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._trigger_entity], self._handle_light_change
            )
        )
        self.hass.async_create_task(self._fetch_matter_data())

    @callback
    def _handle_light_change(self, event):
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        
        if new_state and new_state.state == "on":
            if old_state is None or old_state.state != "on":
                self.hass.loop.call_later(1.0, lambda: self.hass.async_create_task(self._fetch_matter_data()))

    async def _fetch_matter_data(self):
        try:
            from homeassistant.components.matter import get_matter
            matter_adapter = get_matter(self.hass)
            
            if not matter_adapter or not matter_adapter.matter_client:
                return

            node = matter_adapter.matter_client.get_node(self._node_id)
            if not node:
                return
                
            new_value = None
            try:
                new_value = node.get_attribute_value(self._endpoint_id, MESSAGES_CLUSTER_ID, MESSAGES_ATTRIBUTE_ID)
            except Exception:
                pass

            if not new_value or not isinstance(new_value, list) or len(new_value) == 0:
                return

            message_struct = new_value[0]
            message_text = getattr(message_struct, "messageText", None)

            if message_text is None and isinstance(message_struct, dict):
                message_text = message_struct.get("messageText") or message_struct.get("5") or message_struct.get(5)

            if message_text is not None:
                parts = str(message_text).split("|", 2)
                
                if len(parts) == 3:
                    new_val = str(parts[self._part_index]).strip()
                else:
                    new_val = str(message_text) if self._part_index == 2 else "Unknown"
                    
                if self._attr_native_value != new_val:
                    self._attr_native_value = new_val
                    self.async_write_ha_state() 

        except Exception as e:
            # Ensure any boot-up errors stay hidden in the background as well
            _LOGGER.debug("[NBOX] Could not fetch Matter data (Node missing/asleep): %s", e)