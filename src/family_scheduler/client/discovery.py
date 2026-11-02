"""Finds the hub on the home network (PC-4)."""

from __future__ import annotations

import socket
import threading
from typing import Any

from family_scheduler.client.hub_client import HubClient, HubError, HubUnreachable
from family_scheduler.config import DEFAULT_PORT, SERVICE_TYPE


def find_hub(timeout: float = 3.0) -> str | None:
    """Browse mDNS for a hub; returns "address:port" or None."""
    try:
        from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
    except ImportError:
        return None
    found: list[str] = []
    done = threading.Event()
    zc = Zeroconf()

    def on_change(zeroconf: Any, service_type: str, name: str, state_change: Any) -> None:
        if state_change is not ServiceStateChange.Added:
            return
        info = zeroconf.get_service_info(service_type, name, timeout=1500)
        if info and info.addresses:
            found.append(f"{socket.inet_ntoa(info.addresses[0])}:{info.port}")
            done.set()

    try:
        ServiceBrowser(zc, SERVICE_TYPE, handlers=[on_change])
        done.wait(timeout)
    finally:
        zc.close()
    return found[0] if found else None


def normalise_address(text: str) -> str:
    """Accept "DESKTOP-ABC123", "192.168.1.20" or "192.168.1.20:8765"."""
    text = text.strip().removeprefix("http://").rstrip("/")
    if ":" not in text:
        text = f"{text}:{DEFAULT_PORT}"
    return text


def is_hub(address: str) -> bool:
    client = HubClient(address, timeout=2.0)
    try:
        return client.health().get("app") == "family-scheduler-hub"
    except (HubUnreachable, HubError, ValueError):
        return False
    finally:
        client.close()
