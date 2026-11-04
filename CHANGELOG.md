# Changelog

## 3.0.0 – self-healing

The integration now finds the N-Box by itself and keeps working when the N-Box is removed and paired again.

### Changed
- **Automatic detection.** The N-Box is recognised on Matter by what it is (vendor `BRS-L`, product `N-Box`;
  fallback: a node that publishes the Messages cluster on several LED endpoints). The Matter node number and
  the four trigger lights no longer need to be entered or kept in sync. Setup is a single confirmation.
- **Push instead of polling.** The integration subscribes to the Matter server for the Messages attribute of each
  LED endpoint, so a message appears the moment the N-Box reports it. The old "read 1 s after the light turns on"
  race is gone, and it no longer depends on any light entity.
- **Re-pairing is picked up automatically.** When the N-Box node is removed or a new one appears, the integration
  switches to it, resubscribes and re-reads all four channels.
- A periodic re-read (every 60 s) acts as a safety net.
- Sensors show `unavailable` while there is no N-Box on Matter (or the node is offline), and `Waiting for data...`
  until a node has been read. A cleared notification shows `None`. Sensor entity ids and unique ids are unchanged.

### Added
- **Self-healing of the registry.**
  - Matter lights that v2 had pulled into its own "N-Box LED n" devices (or that were left without any device when
    v2 was removed) are moved back to the N-Box's Matter device.
  - Leftover Matter entities (and empty devices) of N-Box nodes that no longer exist are removed. Only nodes the
    integration itself recognised as an N-Box are ever touched; other Matter devices are never affected.
    Switch it off with the **auto cleanup** option.
- Options: optional **pin to a Matter node** (0 = automatic) and **auto cleanup**.
- Migration: the old `node_id` / `trigger_led1…4` options are dropped on first start (the old node number is
  still used once, to clean up its leftovers).
- Debug logging of every received message (`custom_components.nbox_matter: debug`), sensor attribute `matter_node_id`.

### Fixed
- **Missing integration icon.** Home Assistant 2026.3+ reads icons from `custom_components/nbox_matter/brand/`
  (`icon.png` 256x256 and `icon@2x.png` 512x512, square). The old install had its images in the wrong place and shape.

### Removed
- Moving the Matter light entities into the integration's own devices ("entity kidnapping"), the source of the
  orphaned `unavailable` lights after re-pairing.

## 2.0.6
- Initial public release.
