"""Constants for the N-Box Matter extension."""

DOMAIN = "nbox_matter"

# --- options -----------------------------------------------------------------
# Optional: pin the integration to one Matter node. 0 / missing = auto-detect (default).
CONF_NODE_ID = "node_id"
# Remove leftover Matter entities of N-Box nodes that were removed and paired again.
CONF_AUTO_CLEANUP = "auto_cleanup"
DEFAULT_AUTO_CLEANUP = True

# Legacy options (v2.x). They are no longer used: the lights that signal a new
# message are found from the Matter node itself. Kept so old entries still load.
CONF_TRIGGER_LED1 = "trigger_led1"
CONF_TRIGGER_LED2 = "trigger_led2"
CONF_TRIGGER_LED3 = "trigger_led3"
CONF_TRIGGER_LED4 = "trigger_led4"

# --- the N-Box on the Matter side --------------------------------------------
# The firmware publishes the standard Matter Messages cluster (0x0097) on
# endpoints 1-4; the "Messages" attribute (id 0) holds one MessageStruct whose
# messageText is "sender|app|content" ("None|None|None" when cleared).
MESSAGES_CLUSTER_ID = 151
MESSAGES_ATTRIBUTE_ID = 0
LED_ENDPOINTS = (1, 2, 3, 4)

# Basic Information cluster on the root endpoint: used to recognise an N-Box.
BASIC_INFORMATION_CLUSTER_ID = 40
ATTR_VENDOR_NAME = 1
ATTR_VENDOR_ID = 2
ATTR_PRODUCT_NAME = 3
ATTR_PRODUCT_ID = 4

NBOX_VENDOR_NAME = "BRS-L"
NBOX_PRODUCT_NAME = "N-Box"
NBOX_VENDOR_ID = 0xFFF2
NBOX_PRODUCT_ID = 0x8001

# --- sensors -----------------------------------------------------------------
PART_NAMES = ("Sender", "App", "Content")
WAITING = "Waiting for data..."

# --- notification ID / events ---------------------------------------------------
# Fired once per new notification (new ID). Data: endpoint, id, sender, app, content, is_new.
EVENT_MESSAGE = "nbox_matter_message"
# Option per LED channel: "Process LED n messages" (default on). Off = no events from it.
PROCESS_LED_KEYS = {ep: f"process_led{ep}" for ep in LED_ENDPOINTS}
