"""
Mars Hydro iTime Cloud API Driver

Adapter for Mars Hydro iTime smart socket controllers via their cloud API.
Reverse-engineered from community documentation.

Config (devices.json):
    "tent_light": {
        "driver": "marshydro_cloud",
        "account_email": "your-mars-hydro-account@example.com",
        "account_password": "your-password",  # or use env var MARSHYDRO_PASSWORD
        "device_mac": "38:18:2B:71:9C:18",
        "channels": {
            "outlet_1": {"label": "Tent Light", "default_state": "off"},
            "outlet_2": {"label": "CO2 Generator", "default_state": "off"}
        }
    }

Limitations:
- Depends on Mars Hydro's cloud API (not local)
- Requires active internet connection
- API subject to change without notice

This is Path A from DEVICE_INTEGRATION.md.
"""
import os
import asyncio
import aiohttp
import json
from datetime import datetime
from typing import Dict, Any, Optional
from core.device_hal import DeviceDriver, DeviceState, DeviceType, DeviceCapability

MARSHYDRO_API_BASE = "https://api.marshydro.com"  # reverse-engineered endpoint (may vary)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class MarsHydroCloudDriver(DeviceDriver):
    def __init__(self, device_id: str, config: Dict[str, Any]):
        super().__init__(device_id, config)
        self.email = config.get("account_email")
        self.password = config.get("account_password") or os.getenv("MARSHYDRO_PASSWORD", "")
        self.device_mac = config.get("device_mac")
        self.channels = config.get("channels", {})
        self._session: Optional[aiohttp.ClientSession] = None
        self._token: Optional[str] = None
        self._device_id: Optional[str] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazy-load HTTP session."""
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=REQUEST_TIMEOUT)
        return self._session

    async def _authenticate(self) -> bool:
        """Log in and get API token."""
        if self._token:
            return True

        session = await self._get_session()
        try:
            async with session.post(
                f"{MARSHYDRO_API_BASE}/v1/auth/login",
                json={"email": self.email, "password": self.password},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._token = data.get("token")
                    return bool(self._token)
        except Exception as e:
            print(f"Mars Hydro auth failed: {e}")
        return False

    async def _get_device_id(self) -> Optional[str]:
        """Resolve device MAC to device ID via API."""
        if self._device_id:
            return self._device_id

        if not await self._authenticate():
            return None

        session = await self._get_session()
        try:
            async with session.get(
                f"{MARSHYDRO_API_BASE}/v1/devices",
                headers={"Authorization": f"Bearer {self._token}"},
            ) as resp:
                if resp.status == 200:
                    devices = await resp.json()
                    for dev in devices.get("devices", []):
                        if dev.get("mac") == self.device_mac:
                            self._device_id = dev.get("id")
                            return self._device_id
        except Exception as e:
            print(f"Failed to list Mars Hydro devices: {e}")
        return None

    async def get_state(self) -> DeviceState:
        """Fetch device state from cloud API."""
        device_id = await self._get_device_id()
        if not device_id:
            return DeviceState(self.device_id, DeviceType.OUTLET, online=False)

        if not await self._authenticate():
            return DeviceState(self.device_id, DeviceType.OUTLET, online=False)

        session = await self._get_session()
        channels = {}
        online = False

        try:
            async with session.get(
                f"{MARSHYDRO_API_BASE}/v1/devices/{device_id}/state",
                headers={"Authorization": f"Bearer {self._token}"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    online = True

                    # Map API outlets to HAL channels
                    for outlet_idx in [1, 2]:  # Mars Hydro iTime has 2 outlets
                        outlet_key = f"outlet_{outlet_idx}"
                        api_state = data.get(f"outlet_{outlet_idx}", {})
                        channels[outlet_key] = {
                            "on": api_state.get("on", False),
                            "label": self.channels.get(outlet_key, {}).get(
                                "label", f"Outlet {outlet_idx}"
                            ),
                            "power_w": api_state.get("power_w", 0),
                        }
        except Exception as e:
            print(f"Failed to fetch Mars Hydro state: {e}")

        return DeviceState(
            self.device_id,
            DeviceType.OUTLET,
            online=online,
            capabilities=[
                DeviceCapability.GET_STATE,
                DeviceCapability.SET_CHANNEL,
                DeviceCapability.LIST_CHANNELS,
            ],
            channels=channels,
            last_seen=datetime.now().isoformat(),
            metadata={"driver": "marshydro_cloud", "device_mac": self.device_mac},
        )

    async def set_channel(self, channel_id: str, state: Dict[str, Any]) -> bool:
        """Set outlet state (turn on/off)."""
        device_id = await self._get_device_id()
        if not device_id:
            return False

        if not await self._authenticate():
            return False

        # Extract outlet number from channel_id (e.g., "outlet_1" -> 1)
        outlet_num = channel_id.split("_")[-1]

        session = await self._get_session()
        try:
            action = "on" if state.get("on") else "off"
            async with session.post(
                f"{MARSHYDRO_API_BASE}/v1/devices/{device_id}/outlets/{outlet_num}/{action}",
                headers={"Authorization": f"Bearer {self._token}"},
            ) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            print(f"Failed to set Mars Hydro outlet: {e}")
        return False

    async def close(self):
        """Clean up HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
