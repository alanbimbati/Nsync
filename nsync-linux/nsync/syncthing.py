"""Syncthing REST API client."""

import re
from pathlib import Path

import requests

CONFIG_XML_CANDIDATES = [
    Path("~/.local/state/syncthing/config.xml"),
    Path("~/.config/syncthing/config.xml"),
    Path("~/Library/Application Support/Syncthing/config.xml"),
]


def find_api_key() -> str | None:
    """Read the key from Syncthing's own config so the user need not copy it."""
    for candidate in CONFIG_XML_CANDIDATES:
        path = candidate.expanduser()
        if path.exists():
            m = re.search(r"<apikey>([^<]+)</apikey>", path.read_text())
            if m:
                return m.group(1)
    return None


class Syncthing:
    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/")
        self._http = requests.Session()
        self._http.headers["X-API-Key"] = api_key

    def _request(self, method: str, path: str, **kw):
        r = self._http.request(method, self.url + path, timeout=10, **kw)
        r.raise_for_status()
        return r.json() if r.content else None

    def device_id(self) -> str:
        return self._request("GET", "/rest/system/status")["myID"]

    def listen_port(self) -> int:
        """First TCP port Syncthing listens on; 22000 is its default."""
        opts = self._request("GET", "/rest/config/options")
        for addr in opts.get("listenAddresses", []):
            m = re.match(r"^(?:tcp|quic)?(?:4|6)?://.*:(\d+)$", addr)
            if m:
                return int(m.group(1))
        return 22000

    def healthy(self) -> bool:
        try:
            self._request("GET", "/rest/noauth/health")
            return True
        except (requests.RequestException, ValueError):
            return False

    def patch_options(self, options: dict) -> None:
        self._request("PATCH", "/rest/config/options", json=options)

    def device(self, device_id: str) -> dict | None:
        for dev in self._request("GET", "/rest/config/devices"):
            if dev["deviceID"] == device_id:
                return dev
        return None

    def add_device(self, device_id: str, name: str) -> None:
        self._request("POST", "/rest/config/devices",
                      json={"deviceID": device_id, "name": name, "addresses": ["dynamic"]})

    def set_addresses(self, device_id: str, addresses: list[str]) -> bool:
        """Point Syncthing at *addresses*; returns whether anything changed.

        'dynamic' stays last so Syncthing's own discovery remains a fallback.
        """
        wanted = sorted(addresses) + ["dynamic"]
        dev = self.device(device_id)
        if dev is None or dev.get("addresses") == wanted:
            return False
        self._request("PATCH", f"/rest/config/devices/{device_id}", json={"addresses": wanted})
        return True
