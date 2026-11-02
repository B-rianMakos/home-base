"""Announces the hub on the home network so other PCs find it (PC-4)."""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any

from family_scheduler import __version__
from family_scheduler.config import SERVICE_TYPE

log = logging.getLogger(__name__)


def private_ipv4_addresses() -> list[str]:
    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
    except OSError:
        return []
    return [a for a in addresses if ipaddress.ip_address(a).is_private and not a.startswith("127.")]


def announce(port: int) -> Any:
    """Returns the Zeroconf instance (close it on shutdown), or None if announcing failed."""
    try:
        from zeroconf import ServiceInfo, Zeroconf

        addresses = private_ipv4_addresses()
        info = ServiceInfo(
            SERVICE_TYPE,
            f"Family Scheduler Hub on {socket.gethostname()}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(a) for a in addresses],
            port=port,
            properties={"version": __version__},
            server=f"{socket.gethostname()}.local.",
        )
        zc = Zeroconf()
        zc.register_service(info)
        log.info("Announced hub on %s port %s", addresses, port)
        return zc
    except Exception:
        log.exception("Couldn't announce the hub; PCs can still connect by address")
        return None
