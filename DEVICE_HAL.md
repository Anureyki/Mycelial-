# Device Hardware Abstraction Layer (HAL)

A **vendor-neutral interface** for controlling physical devices. Agents interact with a normalized `DeviceState` abstraction; drivers handle protocol-specific logic. This design is intentionally **spec-agnostic** so it adapts to Anthropic's hardware spec when released, or integrates with any other standard without refactoring agent code.

## Why a HAL?

Without abstraction, agent code gets tangled with device protocol details:
- Grow agent knows about Mars Hydro cloud API, ESPHome HTTP, MQTT payloads, serial commands — mixed together.
- Adding a new device type requires editing agent code.
- Switching from cloud API to local control requires refactoring.

With a HAL:
- Agents ask the DeviceManager for normalized state.
- Drivers translate protocol specifics → HAL interface.
- Adding a device is a **config change** (devices.json), not code.
- Swapping Mars Hydro cloud for ESPHome local is a **one-line config edit**.

## Architecture

```
┌─────────────────────────────────┐
│  Agent Code (grow_agent, etc.)  │
│  "get tent_light state"         │
└─────────┬───────────────────────┘
          │ (uses normalized interface)
┌─────────▼───────────────────────┐
│  DeviceManager (core/device_hal) │
│  routes to driver, caches state │
└─────────┬───────────────────────┘
          │ (driver dispatch)
    ┌─────┴──────┬──────────┬──────────┐
    │            │          │          │
┌───▼──┐   ┌────▼─┐   ┌───▼──┐   ┌──▼──┐
│Cloud │   │Local │   │MQTT  │   │Stub │
│API   │   │HTTP/ │   │Broker│   │Test │
│Driver│   │gRPC  │   │Driver│   │Driver
└──────┘   └──────┘   └──────┘   └─────┘
(Mars Hydro)(ESPHome)(Tasmota,  (for
            HAL)     MQTT-any)   testing)
```

## Core Types

### `DeviceState`
Normalized state returned by all drivers. Independent of protocol.

```python
state = DeviceState(
    device_id="tent_light",
    device_type=DeviceType.OUTLET,
    online=True,
    capabilities=[DeviceCapability.GET_STATE, DeviceCapability.SET_CHANNEL],
    channels={
        "outlet_1": {"on": True, "label": "Tent Light", "power_w": 540},
        "outlet_2": {"on": False, "label": "CO2 Generator", "power_w": 0},
    },
    readings={},
    last_seen="2026-08-29T12:34:56.000000",
)
```

### `DeviceDriver`
Base class that each driver implements. Routes protocol specifics → HAL.

```python
class MyDriver(DeviceDriver):
    async def get_state(self) -> DeviceState:
        """Fetch current state."""
        # Protocol-specific call here
        # Return normalized DeviceState
    
    async def set_channel(self, channel_id: str, state: Dict) -> bool:
        """Set channel state."""
        # Protocol-specific call here
        # Return True if successful
```

### `DeviceManager`
Singleton that loads drivers from config, maintains state, routes calls.

```python
manager = await get_device_manager()
state = await manager.get_device_state("tent_light")
success = await manager.set_channel("tent_light", "outlet_1", {"on": True})
```

## Configuration

### Static: `config/devices.json`

```json
{
  "tent_light": {
    "driver": "marshydro_cloud",
    "account_email": "user@example.com",
    "account_password": "$MARSHYDRO_PASSWORD",
    "device_mac": "38:18:2B:71:9C:18",
    "channels": {
      "outlet_1": {
        "label": "Tent Light",
        "power_limit_w": 600
      }
    }
  }
}
```

Each driver reads its own subset of keys (driver-specific, not shared):
- `marshydro_cloud`: reads `account_email`, `account_password`, `device_mac`
- `esphome`: reads `host`, `port`, `api_password`
- `mqtt`: reads `broker`, `port`, `username`, `password`, channel topics
- All: read `channels` dict to label/describe outlets

### Environment Variables

Secrets can be referenced as `$VAR_NAME`:
```json
"account_password": "$MARSHYDRO_PASSWORD"
```

Drivers resolve these at runtime:
```python
password = os.getenv("MARSHYDRO_PASSWORD", "")
```

## Usage in Agents

```python
from core.device_hal import get_device_manager

async def handle_task(self, task, args, sender):
    if task == "set_outlet":
        manager = await get_device_manager()
        device_id = args.get("device_id")  # "tent_light"
        channel_id = args.get("channel_id")  # "outlet_1"
        success = await manager.set_channel(
            device_id, 
            channel_id, 
            {"on": args.get("on", False)}
        )
        return {"result": "Channel set" if success else "Failed"}
    
    elif task == "status":
        manager = await get_device_manager()
        devices = await manager.list_devices()
        return {"devices": devices}
```

No agent code changes when switching drivers. Configuration-driven.

## Drivers

### 1. **Mars Hydro Cloud** (`marshydro_cloud`)

**Path:** `services/devices/drivers/marshydro_cloud.py`  
**Status:** Implemented  
**Pros:** Works today, no hardware changes  
**Cons:** Depends on internet, Mars Hydro API, vendor control

```json
{
  "tent_light": {
    "driver": "marshydro_cloud",
    "account_email": "...",
    "account_password": "$MARSHYDRO_PASSWORD",
    "device_mac": "38:18:2B:71:9C:18"
  }
}
```

### 2. **ESPHome Local** (`esphome`)

**Path:** `services/devices/drivers/esphome.py`  
**Status:** Stub (implementation pending)  
**Pros:** Fully local, open firmware, no vendor lock-in  
**Cons:** Requires reflashing (case-opening, USB-TTL adapter, ~$5)

```json
{
  "tent_light": {
    "driver": "esphome",
    "host": "192.168.1.230",
    "port": 8266,
    "api_password": "$ESPHOME_API_PASSWORD"
  }
}
```

### 3. **Generic MQTT** (`mqtt`)

**Path:** `services/devices/drivers/mqtt.py`  
**Status:** Stub (implementation pending)  
**Pros:** Protocol-agnostic, works with Tasmota, Home Assistant, custom firmware  
**Cons:** Requires MQTT broker

```json
{
  "tent_light": {
    "driver": "mqtt",
    "broker": "192.168.1.100",
    "port": 1883,
    "username": "mycelial",
    "password": "$MQTT_PASSWORD",
    "channels": {
      "outlet_1": {
        "command_topic": "home/tent/outlet1/cmd",
        "state_topic": "home/tent/outlet1/state",
        "payload_on": "ON",
        "payload_off": "OFF"
      }
    }
  }
}
```

## Adding a New Driver

1. Create `services/devices/drivers/my_protocol.py`:
   ```python
   from core.device_hal import DeviceDriver, DeviceState, DeviceType, DeviceCapability

   class MyDriver(DeviceDriver):
       async def get_state(self) -> DeviceState:
           # Call your protocol, return DeviceState
           pass
       
       async def set_channel(self, channel_id: str, state: Dict) -> bool:
           # Call your protocol, return success bool
           pass
   ```

2. Register in `DeviceManager._get_driver_class()`:
   ```python
   elif driver_type == "my_protocol":
       from services.devices.drivers.my_protocol import MyDriver
       return MyDriver
   ```

3. Add config example to `config/devices.json.example`

4. No agent changes needed — just update config and restart.

## Roadmap

- [ ] Implement ESPHome driver (local HTTP/gRPC)
- [ ] Implement MQTT driver (Tasmota, HA, custom)
- [ ] Implement Home Assistant integration
- [ ] Add scheduling support (`schedule` task)
- [ ] Add history queries (`query_history` task)
- [ ] Wrap agent tasks in `grow_agent` (set_outlet, get_outlet, list_outlets)
- [ ] Support for Anthropic hardware spec when released
- [ ] Open source as standalone library

## Specification Readiness

This HAL is designed to be **spec-agnostic** — when Anthropic (or any standards body) releases a hardware specification:

1. **If the spec is a strict superset:** Extend `DeviceState` / `DeviceCapability` to cover new fields.
2. **If the spec differs:** Create an adapter driver that translates spec ↔ HAL.
3. **If the spec is simpler:** HAL can emit a restricted subset.

The key insight: **agent code never changes** — only drivers and config.

## Testing

Use the `test_driver` stub to mock devices without hardware:

```json
{
  "mock_light": {
    "driver": "test_driver",
    "channels": {
      "outlet_1": {"label": "Mock Light", "default_state": "off"}
    }
  }
}
```

Write agent logic against real devices once the driver is proven.

## Open Source

The HAL (core/device_hal.py) and all drivers are designed to be self-contained and open-sourceable. No agent-specific coupling. Deploy this independently:

```bash
git clone https://github.com/yourusername/device-hal.git
pip install device-hal
from device_hal import DeviceManager
```

---

**The goal:** agents control devices through a spec-neutral abstraction, drivers handle the complexity, and switching protocols is a config file edit.
