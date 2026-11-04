"""Self-healing manager for the N-Box Matter extension.

What it does
------------
* Finds the N-Box among the Matter nodes by what it *is* (vendor "BRS-L", product
  "N-Box"), not by a saved node number or light entity names.
* Subscribes to the Matter server: the message text arrives the moment the N-Box
  reports it (no waiting for a light to change, no timing race).
* Heals itself when the N-Box is paired again (new node number): switches to the
  new node, moves lights that an older version had pulled into its devices back to
  the Matter device, and removes the leftover entities of the N-Box nodes that no
  longer exist (only nodes it has itself recognised as an N-Box).
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store

from .const import (
    ATTR_PRODUCT_ID,
    ATTR_PRODUCT_NAME,
    ATTR_VENDOR_ID,
    ATTR_VENDOR_NAME,
    BASIC_INFORMATION_CLUSTER_ID,
    CONF_AUTO_CLEANUP,
    CONF_NODE_ID,
    CONF_TRIGGER_LED1,
    CONF_TRIGGER_LED2,
    CONF_TRIGGER_LED3,
    CONF_TRIGGER_LED4,
    DEFAULT_AUTO_CLEANUP,
    DOMAIN,
    LED_ENDPOINTS,
    MESSAGES_ATTRIBUTE_ID,
    MESSAGES_CLUSTER_ID,
    NBOX_PRODUCT_ID,
    NBOX_PRODUCT_NAME,
    NBOX_VENDOR_ID,
    NBOX_VENDOR_NAME,
    WAITING,
)

_LOGGER = logging.getLogger(__name__)

_RETRY_SECONDS = 30
_DEBOUNCE_SECONDS = 2
_REFRESH_SECONDS = 60  # safety net: re-read everything now and then
_REFRESH_SECONDS_NO_EVENTS = 15  # when the event subscription is not available


# ---------------------------------------------------------------------------
# Pure helpers (no Home Assistant needed - easy to test)
# ---------------------------------------------------------------------------
def parse_message_text(text: Any) -> tuple[str, str, str]:
    """Split "sender|app|content" (only the first two "|" separate)."""
    parts = str(text).split("|", 2)
    if len(parts) == 3:
        return parts[0].strip(), parts[1].strip(), parts[2].strip()
    # Not in the expected format: keep the text as the content
    return "Unknown", "Unknown", str(text).strip()


def parse_messages_value(value: Any) -> tuple[str, str, str] | None:
    """Turn the Matter "Messages" attribute value into (sender, app, content).

    * list with a message  -> the three parts
    * empty list           -> notification cleared: ("None", "None", "None")
    * anything else        -> None (unknown shape, ignore)
    """
    if not isinstance(value, (list, tuple)):
        return None
    if len(value) == 0:
        return ("None", "None", "None")
    struct = value[0]
    text = getattr(struct, "messageText", None)
    if text is None and isinstance(struct, dict):
        text = struct.get("messageText") or struct.get("5") or struct.get(5)
    if text is None:
        return None
    return parse_message_text(text)


def _node_attr(node: Any, endpoint: int, cluster: int, attribute: int) -> Any:
    try:
        return node.get_attribute_value(endpoint, cluster, attribute)
    except Exception:  # noqa: BLE001 - a node can be half-initialised
        return None


def _raw_attribute_paths(node: Any) -> list[str]:
    """Paths of all attributes the node has ("endpoint/cluster/attribute")."""
    for holder in (getattr(node, "node_data", None), node):
        attributes = getattr(holder, "attributes", None)
        if isinstance(attributes, dict):
            return [str(k) for k in attributes]
    return []


def is_nbox_node(node: Any) -> bool:
    """True when this Matter node is an N-Box."""
    vendor = _node_attr(node, 0, BASIC_INFORMATION_CLUSTER_ID, ATTR_VENDOR_NAME)
    product = _node_attr(node, 0, BASIC_INFORMATION_CLUSTER_ID, ATTR_PRODUCT_NAME)
    if vendor == NBOX_VENDOR_NAME or product == NBOX_PRODUCT_NAME:
        return True
    vid = _node_attr(node, 0, BASIC_INFORMATION_CLUSTER_ID, ATTR_VENDOR_ID)
    pid = _node_attr(node, 0, BASIC_INFORMATION_CLUSTER_ID, ATTR_PRODUCT_ID)
    if vid == NBOX_VENDOR_ID and pid == NBOX_PRODUCT_ID:
        return True
    # Fallback: a node that publishes the Messages cluster on several LED endpoints
    paths = _raw_attribute_paths(node)
    with_messages = {
        ep
        for ep in LED_ENDPOINTS
        if any(p.startswith(f"{ep}/{MESSAGES_CLUSTER_ID}/") for p in paths)
    }
    return len(with_messages) >= 2


def get_matter_client(hass: HomeAssistant) -> Any | None:
    """The Matter server client of Home Assistant's Matter integration (or None)."""
    try:
        try:
            from homeassistant.components.matter.helpers import get_matter
        except ImportError:  # older Home Assistant
            from homeassistant.components.matter import get_matter
        adapter = get_matter(hass)
        return getattr(adapter, "matter_client", None) if adapter else None
    except Exception as err:  # noqa: BLE001 - Matter not loaded yet
        _LOGGER.debug("[NBOX] Matter client not available yet: %s", err)
        return None


def _event_types() -> Any | None:
    """The Matter server's EventType enum (None if it cannot be imported)."""
    try:
        from matter_server.common.models import EventType

        return EventType
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("[NBOX] EventType not importable: %s", err)
        return None


def find_nbox_nodes(client: Any) -> list[Any]:
    """All Matter nodes that are an N-Box."""
    try:
        nodes = list(client.get_nodes())
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("[NBOX] Could not list Matter nodes: %s", err)
        return []
    return [node for node in nodes if is_nbox_node(node)]


def node_marker(node_id: int) -> str:
    """The part of a Matter entity's unique_id that carries the node number."""
    return f"-{int(node_id):016X}-MatterNodeDevice-"


# ---------------------------------------------------------------------------
# The manager
# ---------------------------------------------------------------------------
class NBoxManager:
    """Keeps the N-Box messages up to date and the registry tidy."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.node_id: int | None = None
        self.node_available = False
        self._messages: dict[int, tuple[str, str, str] | None] = {
            ep: None for ep in LED_ENDPOINTS
        }
        self._client: Any | None = None
        self._unsub_node_events: list[Any] = []
        self._unsub_attr_events: list[Any] = []
        self._cancel_retry: Any | None = None
        self._cancel_refresh: Any | None = None
        self._rediscover_pending = False
        self._warned_override: int | None = None
        self._stopped = False
        self._known_nodes: set[int] = set()
        self._store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_known_nodes")

    # -- what the sensors read ---------------------------------------------
    @property
    def signal(self) -> str:
        return f"{DOMAIN}_update_{self.entry.entry_id}"

    @property
    def available(self) -> bool:
        return self.node_id is not None and self.node_available

    def value(self, endpoint: int, index: int) -> str:
        message = self._messages.get(endpoint)
        return message[index] if message else WAITING

    # -- life cycle ----------------------------------------------------------
    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self._known_nodes = {int(n) for n in data.get("known_nodes", [])}
        # Settings of older versions are only used as hints of "this was an N-Box"
        for source in (self.entry.options, self.entry.data):
            hint = source.get(CONF_NODE_ID)
            if isinstance(hint, int) and hint > 0:
                self._known_nodes.add(hint)
        # Older versions stored the node number and the trigger lights as options. They are
        # not needed any more (everything is found automatically), and a stale pinned node
        # would only get in the way of the next re-pairing.
        legacy_keys = (CONF_TRIGGER_LED1, CONF_TRIGGER_LED2, CONF_TRIGGER_LED3, CONF_TRIGGER_LED4)
        if any(key in self.entry.options for key in legacy_keys):
            _LOGGER.info("[NBOX] Migrating settings of an older version: the node and the trigger lights are now automatic")
            kept = {k: v for k, v in self.entry.options.items() if k == CONF_AUTO_CLEANUP}
            self.hass.config_entries.async_update_entry(self.entry, options=kept)
        await self._async_connect()

    async def async_stop(self) -> None:
        self._stopped = True
        for cancel in (self._cancel_retry, self._cancel_refresh):
            if cancel:
                cancel()
        self._cancel_retry = self._cancel_refresh = None
        self._unsubscribe_all()

    def _unsubscribe_all(self) -> None:
        for unsub in self._unsub_node_events + self._unsub_attr_events:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass
        self._unsub_node_events = []
        self._unsub_attr_events = []

    async def _async_connect(self, _now: Any = None) -> None:
        self._cancel_retry = None
        if self._stopped:
            return
        client = get_matter_client(self.hass)
        if client is None:
            _LOGGER.info("[NBOX] Matter is not ready yet, trying again in %s s", _RETRY_SECONDS)
            self._cancel_retry = async_call_later(self.hass, _RETRY_SECONDS, self._async_connect)
            return
        self._client = client

        event_type = _event_types()
        if event_type is not None and not self._unsub_node_events:
            for event in (event_type.NODE_ADDED, event_type.NODE_UPDATED, event_type.NODE_REMOVED):
                self._unsub_node_events.append(
                    client.subscribe_events(self._handle_node_event, event_filter=event)
                )
        await self._async_rediscover("startup")
        self._schedule_refresh()

    # -- events from the Matter server ---------------------------------------
    @callback
    def _handle_node_event(self, event: Any, data: Any) -> None:
        self._schedule_rediscover(str(getattr(event, "value", event)))

    @callback
    def _handle_message_event(self, endpoint: int, event: Any, data: Any) -> None:
        """The Messages attribute of one LED endpoint changed (data = the new value)."""
        self._apply_value(endpoint, data, "update")

    def _subscribe_messages(self, client: Any, node_id: int) -> None:
        """Listen to the Messages attribute of each LED endpoint of this node."""
        for unsub in self._unsub_attr_events:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass
        self._unsub_attr_events = []
        event_type = _event_types()
        if event_type is None:
            return
        for endpoint in LED_ENDPOINTS:
            path = f"{endpoint}/{MESSAGES_CLUSTER_ID}/{MESSAGES_ATTRIBUTE_ID}"
            self._unsub_attr_events.append(
                client.subscribe_events(
                    partial(self._handle_message_event, endpoint),
                    event_filter=event_type.ATTRIBUTE_UPDATED,
                    node_filter=node_id,
                    attr_path_filter=path,
                )
            )

    @callback
    def _schedule_rediscover(self, reason: str) -> None:
        if self._rediscover_pending or self._stopped:
            return
        self._rediscover_pending = True

        async def _run() -> None:
            await asyncio.sleep(_DEBOUNCE_SECONDS)
            self._rediscover_pending = False
            await self._async_rediscover(reason)

        self.hass.async_create_task(_run())

    def _schedule_refresh(self) -> None:
        if self._stopped:
            return
        seconds = _REFRESH_SECONDS if self._unsub_attr_events else _REFRESH_SECONDS_NO_EVENTS
        self._cancel_refresh = async_call_later(self.hass, seconds, self._async_refresh)

    async def _async_refresh(self, _now: Any = None) -> None:
        self._cancel_refresh = None
        if self._stopped:
            return
        await self._async_rediscover("refresh")
        self._schedule_refresh()

    # -- discovery / healing ---------------------------------------------------
    def _override_node_id(self) -> int:
        value = self.entry.options.get(CONF_NODE_ID, 0)
        return value if isinstance(value, int) and value > 0 else 0

    def _choose(self, nodes: list[Any]) -> Any | None:
        if not nodes:
            return None
        override = self._override_node_id()
        if override:
            for node in nodes:
                if node.node_id == override:
                    return node
            if self._warned_override != override:
                self._warned_override = override
                _LOGGER.warning(
                    "[NBOX] The pinned Matter node %s is not an N-Box (or is gone); "
                    "auto-detecting instead",
                    override,
                )
        online = [n for n in nodes if getattr(n, "available", True)]
        pool = online or nodes
        for node in pool:  # keep the current one while it is fine
            if node.node_id == self.node_id:
                return node
        return max(pool, key=lambda n: n.node_id)  # newest pairing

    async def _async_rediscover(self, reason: str) -> None:
        client = self._client
        if client is None or self._stopped:
            return
        node = self._choose(find_nbox_nodes(client))
        previous = self.node_id
        was_available = self.available

        if node is None:
            if previous is not None:
                _LOGGER.info("[NBOX] The N-Box is gone from Matter (%s). Waiting for it to be paired.", reason)
                self._unsubscribe_attr_only()
            self.node_id = None
            self.node_available = False
            if was_available:
                self._dispatch()
            return

        node_changed = node.node_id != previous
        self.node_id = node.node_id
        self.node_available = bool(getattr(node, "available", True))
        if node_changed:
            _LOGGER.info(
                "[NBOX] Using N-Box on Matter node %s (%s)%s",
                node.node_id,
                reason,
                f", was node {previous}" if previous is not None else "",
            )
            self._messages = {ep: None for ep in LED_ENDPOINTS}
            self._subscribe_messages(client, node.node_id)

        values_changed = self._read_all(node)

        if node.node_id not in self._known_nodes:
            self._known_nodes.add(node.node_id)
            await self._store.async_save({"known_nodes": sorted(self._known_nodes)})
        if node_changed or reason == "startup":
            self._heal_registry(node)
            await self._async_cleanup_stale(client)
        if node_changed or values_changed or was_available != self.available:
            self._dispatch()

    def _unsubscribe_attr_only(self) -> None:
        for unsub in self._unsub_attr_events:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass
        self._unsub_attr_events = []

    def _read_all(self, node: Any) -> bool:
        """Read the current messages of all LED endpoints. True if something changed."""
        changed = False
        for endpoint in LED_ENDPOINTS:
            try:
                value = node.get_attribute_value(
                    endpoint, MESSAGES_CLUSTER_ID, MESSAGES_ATTRIBUTE_ID
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("[NBOX] EP%s: could not read the message: %s", endpoint, err)
                continue
            if value is None:
                continue
            changed |= self._apply_value(endpoint, value, "read", dispatch=False)
        return changed

    def _apply_value(self, endpoint: int, value: Any, source: str, dispatch: bool = True) -> bool:
        if endpoint not in self._messages:
            return False
        message = parse_messages_value(value)
        if message is None:
            _LOGGER.debug("[NBOX] EP%s (%s): unexpected value %r", endpoint, source, value)
            return False
        if self._messages[endpoint] == message:
            return False
        self._messages[endpoint] = message
        _LOGGER.debug(
            "[NBOX] EP%s (%s): sender=%r app=%r content=%r", endpoint, source, *message
        )
        if dispatch:
            self._dispatch()
        return True

    @callback
    def _dispatch(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

    # -- registry tidy-up ------------------------------------------------------
    def _own_device_ids(self, dev_reg: Any) -> set[str]:
        return {d.id for d in dr.async_entries_for_config_entry(dev_reg, self.entry.entry_id)}

    def _heal_registry(self, node: Any) -> None:
        """Give lights that an older version pulled into its devices back to Matter."""
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        marker = node_marker(node.node_id)
        matter_entries = [
            e
            for e in ent_reg.entities.values()
            if e.platform == "matter" and marker in (e.unique_id or "")
        ]
        ours = self._own_device_ids(dev_reg)
        counts = Counter(
            e.device_id for e in matter_entries if e.device_id and e.device_id not in ours
        )
        if not counts:
            return
        matter_device = counts.most_common(1)[0][0]
        for entry in matter_entries:
            if entry.device_id in ours:
                _LOGGER.info("[NBOX] Moving %s back to its Matter device", entry.entity_id)
                ent_reg.async_update_entity(entry.entity_id, device_id=matter_device)

    async def _async_cleanup_stale(self, client: Any) -> None:
        """Remove leftovers of N-Box nodes that were removed (opt-out option)."""
        if not self.entry.options.get(CONF_AUTO_CLEANUP, DEFAULT_AUTO_CLEANUP):
            return
        try:
            present = {n.node_id for n in client.get_nodes()}
        except Exception:  # noqa: BLE001
            return
        stale = {n for n in self._known_nodes if n != self.node_id and n not in present}
        if not stale:
            return

        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        ours = self._own_device_ids(dev_reg)
        touched_devices: set[str] = set()
        for node_id in stale:
            marker = node_marker(node_id)
            for entry in list(ent_reg.entities.values()):
                if entry.platform == "matter" and marker in (entry.unique_id or ""):
                    _LOGGER.info(
                        "[NBOX] Removing %s, left over from the removed N-Box node %s",
                        entry.entity_id,
                        node_id,
                    )
                    if entry.device_id and entry.device_id not in ours:
                        touched_devices.add(entry.device_id)
                    ent_reg.async_remove(entry.entity_id)
        for device_id in touched_devices:
            if not er.async_entries_for_device(ent_reg, device_id, include_disabled_entities=True):
                _LOGGER.info("[NBOX] Removing the empty leftover device %s", device_id)
                dev_reg.async_remove_device(device_id)

        self._known_nodes -= stale
        await self._store.async_save({"known_nodes": sorted(self._known_nodes)})
