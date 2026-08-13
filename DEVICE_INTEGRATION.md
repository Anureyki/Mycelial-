# Device Integration — Mars Hydro iTime Controller

Status: **researched, not implemented.** Picked up later; this file records what was
established so the investigation doesn't have to be redone.

## The device

- **Model:** Mars Hydro iTime controller (2 switched channels — app shows ⚡1 / ⚡2)
- **App identity:** `MH-ITIME-38182B719C18` (MH = Mars Hydro; the suffix is the MAC)
- **MAC:** `38:18:2B:71:9C:18`
- **LAN IP:** `192.168.1.230` (same /24 as the mycelial host, `192.168.1.139`)
- **Silicon:** OUI `38:18:2B` is registered to **Espressif Inc.** Since the app exposes
  both Bluetooth and WiFi, it is an **ESP32** (the ESP8266 has no BLE).

## What was verified on 2026-08-13 (not assumed)

| Probe | Result |
|---|---|
| ICMP to 192.168.1.230 | responds, 0% loss |
| Full TCP scan, ports 1–65535 | **zero open ports** |
| Tuya-style UDP broadcast listen (6666/6667/7000) | nothing received |
| UDP probes (6666, 6667, 7000, 5577, 9999, 48899, 1982, 50000) | no replies |
| Bluetooth hardware on mycelial host | **none** — no `bluetoothctl`, no `hciconfig`, no USB BLE adapter, nothing in `/sys/class/bluetooth/` |

**Conclusion:** the device is a pure cloud client. It dials out to Mars Hydro's broker and
accepts no inbound connections. There is no local control surface on stock firmware, and
the BLE path is blocked at the host until a USB BLE dongle is added.

## Paths forward (pick one when resuming)

### A. Cloud API — lowest effort, no hardware work
Mars Hydro does not publish an API, but it has been reverse engineered by the community,
including the MQTT endpoint the controller uses to reach the backend.
- `suppqt/hass_mars_hydro` — a working Home Assistant integration for **cloud-based** Mars
  Hydro devices. This is the closest thing to a reference implementation; read it first.
- The THCFarmer thread documents endpoints and schemas.
- Trade-off: depends on internet, a Mars Hydro account, and the vendor not changing the
  API. Contradicts mycelial's self-hosted premise, but works today.

### B. Reflash the ESP32 — best long-term fit
It is an ESP32, so ESPHome or Tasmota can replace the stock firmware, giving native local
HTTP/MQTT with no cloud. There is also open ESP32-S3 firmware for the Mars Hydro **iHub-Pro**
(local VPD control + Home Assistant MQTT) worth reading as prior art — note it targets a
different model, so pinouts and relay mapping will differ.
- Requires opening the case and a ~$5 USB-TTL adapter (serial flash; `tuya-convert`-style OTA
  does not apply to ESP32).
- Trade-off: physical work, voids warranty, small brick risk. Fully local afterwards.

### C. BLE — worst option here
Requires buying a USB BLE dongle **and** reverse engineering the GATT protocol. The community
reports BLE control of Mars Hydro gear as an unsolved problem. Not recommended.

## Planned grow_agent design (decided, not yet built)

A **generic switch layer** with this controller as the first driver — not a hardcoded
device. Devices declared in config, following the `config/departments.json` precedent so
adding a pump or fan is a config change rather than a code change.

```jsonc
// config/devices.json  (proposed)
{
  "tent_light": {
    "driver": "esphome",            // or "marshydro_cloud"
    "host": "192.168.1.230",
    "channel": 1,
    "role": "light"
  }
}
```

grow_agent tasks: `list_outlets`, `get_outlet(name)`, `set_outlet(name, on|off)`.

Driver modules keep protocol specifics out of the agent, so path A and path B above can
both be implemented behind the same interface — and swapped without touching grow_agent.

## Sources
- https://github.com/suppqt/hass_mars_hydro
- https://www.thcfarmer.com/threads/mars-hydro-api-reverse-engineered.175510/
- https://community.home-assistant.io/t/mars-hydro/672657
