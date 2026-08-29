"""
Device Hardware Abstraction Layer (HAL)

A vendor-neutral interface for controlling physical devices. Drivers implement
the protocol-specific logic (cloud API, local HTTP, MQTT, serial, BLE, etc.);
agents interact only with the HAL interface.

Design is intentionally spec-agnostic so it can adapt to Anthropic's hardware
spec when released, or integrate with any other standard without refactoring
agent code.
"""
import os
import json
from abc import ABC, abstractmethod
from enum import Enum
from datetime import datetime
from typing import Any, Dict, List, Optional

BASE = os.path.expanduser("~/mycelial")
DEVICES_CONFIG = os.path.join(BASE, "config", "devices.json")


class DeviceType(Enum):
    """Device classification for capability negotiation."""
    OUTLET = "outlet"  # on/off switch
    DIMMER = "dimmer"  # adjustable brightness/power
    SENSOR = "sensor"  # read-only measurement
    CLIMATE = "climate"  # temperature/humidity control
    PUMP = "pump"  # fluid flow control
    LIGHT = "light"  # lighting controller
    VENTILATION = "ventilation"  # fan speed control
    UNKNOWN = "unknown"


class DeviceCapability(Enum):
    """Operations a device can perform."""
    GET_STATE = "get_state"  # read current state
    SET_STATE = "set_state"  # write state
    GET_READING = "get_reading"  # read sensor data
    LIST_CHANNELS = "list_channels"  # enumerate outlets/zones
    GET_CHANNEL = "get_channel"  # read channel state
    SET_CHANNEL = "set_channel"  # write channel state
    SCHEDULE = "schedule"  # set timed tasks
    QUERY_HISTORY = "query_history"  # retrieve historical data


class DeviceState:
    """Normalized device state, independent of driver."""

    def __init__(
        self,
        device_id: str,
        device_type: DeviceType,
        online: bool = False,
        capabilities: List[DeviceCapability] = None,
        channels: Dict[str, Dict[str, Any]] = None,
        readings: Dict[str, Any] = None,
        last_seen: Optional[str] = None,
        metadata: Dict[str, Any] = None,
    ):
        self.device_id = device_id
        self.device_type = device_type
        self.online = online
        self.capabilities = capabilities or []
        self.channels = channels or {}  # {channel_id: {state, label, ...}}
        self.readings = readings or {}  # {sensor_name: value}
        self.last_seen = last_seen or datetime.now().isoformat()
        self.metadata = metadata or {}

    def to_dict(self):
        return {
            "device_id": self.device_id,
            "device_type": self.device_type.value,
            "online": self.online,
            "capabilities": [c.value for c in self.capabilities],
            "channels": self.channels,
            "readings": self.readings,
            "last_seen": self.last_seen,
            "metadata": self.metadata,
        }


class DeviceDriver(ABC):
    """
    Base driver interface. Each driver translates HAL calls into protocol-specific
    operations (cloud API, local HTTP, MQTT, etc.).

    Drivers are instantiated by the DeviceManager based on config/devices.json.
    They should be stateless (state lives in DeviceState) and handle their own
    connection pooling, retry logic, and timeouts.
    """

    def __init__(self, device_id: str, config: Dict[str, Any]):
        """
        Initialize driver with device config from devices.json.

        Args:
            device_id: unique identifier (e.g., "tent_light")
            config: driver config from devices.json (host, port, credentials, etc.)
        """
        self.device_id = device_id
        self.config = config
        self.driver_type = config.get("driver", "unknown")

    @abstractmethod
    async def get_state(self) -> DeviceState:
        """Read current device state. Called frequently, so keep it fast."""
        pass

    @abstractmethod
    async def set_channel(
        self, channel_id: str, state: Dict[str, Any]
    ) -> bool:
        """Set channel state (e.g., {"on": True}). Return True if successful."""
        pass

    async def get_channel(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Get channel state. Default: fetch full state and extract channel."""
        state = await self.get_state()
        return state.channels.get(channel_id)

    async def list_channels(self) -> Dict[str, Dict[str, Any]]:
        """List all channels. Default: fetch full state."""
        state = await self.get_state()
        return state.channels

    async def get_reading(self, sensor_name: str) -> Optional[Any]:
        """Get sensor reading. Default: fetch full state and extract reading."""
        state = await self.get_state()
        return state.readings.get(sensor_name)

    # Optional: advanced drivers may implement these
    async def schedule(
        self, channel_id: str, schedule: Dict[str, Any]
    ) -> bool:
        """Set a timed task (e.g., turn on at 6am). Return True if supported."""
        return False

    async def query_history(
        self, channel_id: str, start_time: str, end_time: str
    ) -> List[Dict[str, Any]]:
        """Retrieve historical state changes. Return empty list if unsupported."""
        return []


class DeviceManager:
    """
    Singleton manager that loads drivers from config/devices.json, maintains
    normalized device state, and routes HAL calls to the right driver.

    Usage:
        manager = DeviceManager()
        state = await manager.get_device_state("tent_light")
        await manager.set_channel("tent_light", "outlet_1", {"on": True})
    """

    def __init__(self):
        self.devices = {}  # {device_id: driver instance}
        self.state_cache = {}  # {device_id: DeviceState}
        self._load_devices()

    def _load_devices(self):
        """Load device config from devices.json and instantiate drivers."""
        if not os.path.exists(DEVICES_CONFIG):
            return

        with open(DEVICES_CONFIG, "r") as f:
            config = json.load(f)

        for device_id, device_config in config.items():
            driver_type = device_config.get("driver", "unknown")
            try:
                driver_class = self._get_driver_class(driver_type)
                self.devices[device_id] = driver_class(device_id, device_config)
            except Exception as e:
                print(f"Failed to load driver for {device_id} ({driver_type}): {e}")

    def _get_driver_class(self, driver_type: str):
        """Dynamically import and return driver class."""
        # Built-in drivers
        if driver_type == "marshydro_cloud":
            from services.devices.drivers.marshydro_cloud import MarsHydroCloudDriver

            return MarsHydroCloudDriver
        elif driver_type == "esphome":
            from services.devices.drivers.esphome import ESPHomeDriver

            return ESPHomeDriver
        elif driver_type == "mqtt":
            from services.devices.drivers.mqtt import MQTTDriver

            return MQTTDriver
        else:
            raise ValueError(f"Unknown driver: {driver_type}")

    async def get_device_state(self, device_id: str) -> Optional[DeviceState]:
        """Fetch and cache device state."""
        if device_id not in self.devices:
            return None
        try:
            state = await self.devices[device_id].get_state()
            self.state_cache[device_id] = state
            return state
        except Exception as e:
            print(f"Error fetching state for {device_id}: {e}")
            return None

    async def set_channel(
        self, device_id: str, channel_id: str, state: Dict[str, Any]
    ) -> bool:
        """Set channel state on device."""
        if device_id not in self.devices:
            return False
        try:
            result = await self.devices[device_id].set_channel(channel_id, state)
            if result:
                # Invalidate cache so next get_device_state fetches fresh state
                self.state_cache.pop(device_id, None)
            return result
        except Exception as e:
            print(f"Error setting channel {device_id}/{channel_id}: {e}")
            return False

    async def get_channel(
        self, device_id: str, channel_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get channel state."""
        if device_id not in self.devices:
            return None
        try:
            return await self.devices[device_id].get_channel(channel_id)
        except Exception as e:
            print(f"Error getting channel {device_id}/{channel_id}: {e}")
            return None

    async def list_devices(self) -> List[Dict[str, Any]]:
        """List all configured devices and their state."""
        result = []
        for device_id in self.devices:
            state = await self.get_device_state(device_id)
            if state:
                result.append(state.to_dict())
        return result


# Singleton instance
_manager: Optional[DeviceManager] = None


async def get_device_manager() -> DeviceManager:
    """Get or create the device manager singleton."""
    global _manager
    if _manager is None:
        _manager = DeviceManager()
    return _manager
