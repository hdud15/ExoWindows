"""
Network interface detection for exowindows.
Self-contained so it has no dependency on exo-node or exo-main.
"""
from __future__ import annotations

import platform
import socket
import subprocess
import os
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class InterfaceType(Enum):
    THUNDERBOLT = auto()
    USB_C_HIGH_SPEED = auto()
    USB_ETHERNET = auto()
    ETHERNET = auto()
    WIFI = auto()
    LOOPBACK = auto()
    UNKNOWN = auto()

    def display_name(self) -> str:
        return {
            InterfaceType.THUNDERBOLT: "Thunderbolt",
            InterfaceType.USB_C_HIGH_SPEED: "USB-C (≥5 Gbps)",
            InterfaceType.USB_ETHERNET: "USB Ethernet Adapter",
            InterfaceType.ETHERNET: "Ethernet",
            InterfaceType.WIFI: "Wi-Fi",
            InterfaceType.LOOPBACK: "Loopback",
            InterfaceType.UNKNOWN: "Unknown",
        }[self]

    def priority(self) -> int:
        order = os.getenv("EXO_CONNECTION_PRIORITY", "wifi,thunderbolt,usb_c,ethernet,usb_ethernet,unknown").lower()
        aliases = {
            "thunderbolt": InterfaceType.THUNDERBOLT,
            "tb": InterfaceType.THUNDERBOLT,
            "usb_c": InterfaceType.USB_C_HIGH_SPEED,
            "usbc": InterfaceType.USB_C_HIGH_SPEED,
            "usb": InterfaceType.USB_C_HIGH_SPEED,
            "usb_ethernet": InterfaceType.USB_ETHERNET,
            "usb-ethernet": InterfaceType.USB_ETHERNET,
            "ethernet": InterfaceType.ETHERNET,
            "wifi": InterfaceType.WIFI,
            "wi-fi": InterfaceType.WIFI,
            "wireless": InterfaceType.WIFI,
            "unknown": InterfaceType.UNKNOWN,
        }
        preferred: dict[InterfaceType, int] = {}
        for index, item in enumerate(part.strip() for part in order.split(",")):
            if item in aliases and aliases[item] not in preferred:
                preferred[aliases[item]] = index
        if self in preferred:
            return preferred[self]
        return {
            InterfaceType.THUNDERBOLT: 0,
            InterfaceType.USB_C_HIGH_SPEED: 1,
            InterfaceType.ETHERNET: 2,
            InterfaceType.USB_ETHERNET: 3,
            InterfaceType.WIFI: 4,
            InterfaceType.LOOPBACK: 99,
            InterfaceType.UNKNOWN: 10,
        }[self]

    def icon(self) -> str:
        return {
            InterfaceType.THUNDERBOLT: "[TB]",
            InterfaceType.USB_C_HIGH_SPEED: "[UC]",
            InterfaceType.USB_ETHERNET: "[UE]",
            InterfaceType.ETHERNET: "[ET]",
            InterfaceType.WIFI: "[WF]",
            InterfaceType.LOOPBACK: "[LO]",
            InterfaceType.UNKNOWN: "[??]",
        }[self]


@dataclass
class NetworkInterface:
    name: str
    ip_address: str
    interface_type: InterfaceType
    speed_mbps: Optional[int] = None
    mac_address: Optional[str] = None
    is_up: bool = True
    extra_info: dict[str, str] = field(default_factory=dict)

    @property
    def speed_display(self) -> str:
        if self.speed_mbps is None:
            return "Unknown speed"
        if self.speed_mbps >= 1_000_000:
            return f"{self.speed_mbps // 1_000_000} Tbps"
        if self.speed_mbps >= 1_000:
            return f"{self.speed_mbps // 1_000} Gbps"
        return f"{self.speed_mbps} Mbps"


def _get_windows() -> list[NetworkInterface]:
    interfaces: list[NetworkInterface] = []
    try:
        import wmi  # type: ignore[import-untyped]
        w = wmi.WMI()
        adapters = {a.Description: a for a in w.Win32_NetworkAdapter() if a.NetEnabled}
        for cfg in w.Win32_NetworkAdapterConfiguration(IPEnabled=True):
            if not cfg.IPAddress:
                continue
            ip = next((ip for ip in cfg.IPAddress if ":" not in ip), None)
            if not ip:
                continue
            desc = cfg.Description or ""
            speed_mbps: Optional[int] = None
            adapter = adapters.get(desc)
            if adapter and adapter.Speed:
                try:
                    speed_mbps = int(adapter.Speed) // 1_000_000
                except (ValueError, TypeError):
                    pass
            itype = _classify_windows(desc, speed_mbps)
            interfaces.append(NetworkInterface(
                name=desc,
                ip_address=ip,
                interface_type=itype,
                speed_mbps=speed_mbps,
                mac_address=cfg.MACAddress,
            ))
    except ImportError:
        interfaces = _fallback()
    return interfaces


def _classify_windows(desc: str, speed_mbps: Optional[int]) -> InterfaceType:
    d = desc.lower()
    if "loopback" in d:
        return InterfaceType.LOOPBACK
    if "thunderbolt" in d or "tb4" in d or "tb5" in d:
        return InterfaceType.THUNDERBOLT
    if any(k in d for k in ("wi-fi", "wifi", "wireless", "802.11", "wlan")):
        return InterfaceType.WIFI
    if any(k in d for k in ("usb", "rndis", "cdc", "gadget")):
        if speed_mbps and speed_mbps >= 5_000:
            return InterfaceType.USB_C_HIGH_SPEED
        return InterfaceType.USB_ETHERNET
    if speed_mbps and speed_mbps >= 10_000:
        return InterfaceType.THUNDERBOLT
    return InterfaceType.ETHERNET


def _get_linux() -> list[NetworkInterface]:
    import os
    interfaces: list[NetworkInterface] = []
    net_dir = "/sys/class/net"
    if not os.path.exists(net_dir):
        return _fallback()
    for iface_name in os.listdir(net_dir):
        iface_path = os.path.join(net_dir, iface_name)
        ip = _linux_ip(iface_name)
        if not ip:
            continue
        speed_mbps: Optional[int] = None
        speed_path = os.path.join(iface_path, "speed")
        if os.path.exists(speed_path):
            try:
                with open(speed_path) as f:
                    speed_mbps = int(f.read().strip())
            except (ValueError, OSError):
                pass
        itype = _classify_linux(iface_name, iface_path, speed_mbps)
        interfaces.append(NetworkInterface(
            name=iface_name,
            ip_address=ip,
            interface_type=itype,
            speed_mbps=speed_mbps,
        ))
    return interfaces


def _classify_linux(name: str, sys_path: str, speed_mbps: Optional[int]) -> InterfaceType:
    import os
    if name == "lo":
        return InterfaceType.LOOPBACK
    if name.startswith("wl") or "wlan" in name:
        return InterfaceType.WIFI
    sub = os.path.join(sys_path, "device", "subsystem")
    if os.path.exists(sub):
        real = os.path.realpath(sub)
        if "usb" in real.lower():
            return InterfaceType.USB_C_HIGH_SPEED if (speed_mbps and speed_mbps >= 5_000) else InterfaceType.USB_ETHERNET
    if speed_mbps and speed_mbps >= 10_000:
        return InterfaceType.THUNDERBOLT
    return InterfaceType.ETHERNET


def _linux_ip(iface: str) -> Optional[str]:
    try:
        r = subprocess.run(["ip", "-4", "addr", "show", iface], capture_output=True, text=True, timeout=3)
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                return line.split()[1].split("/")[0]
    except Exception:
        pass
    return None


def _fallback() -> list[NetworkInterface]:
    ifaces: list[NetworkInterface] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if ":" in ip:
                continue
            itype = InterfaceType.LOOPBACK if ip.startswith("127.") else InterfaceType.UNKNOWN
            ifaces.append(NetworkInterface(name="auto", ip_address=ip, interface_type=itype))
    except Exception:
        pass
    return ifaces


def get_interfaces(include_loopback: bool = False) -> list[NetworkInterface]:
    system = platform.system()
    if system == "Windows":
        raw = _get_windows()
    elif system == "Linux":
        raw = _get_linux()
    else:
        raw = _fallback()
    if not include_loopback:
        raw = [i for i in raw if i.interface_type != InterfaceType.LOOPBACK]
    return sorted(raw, key=lambda i: i.interface_type.priority())


def print_interfaces() -> None:
    from rich.console import Console
    from rich.table import Table
    console = Console()
    table = Table(title="Network Interfaces", show_header=True, header_style="bold cyan")
    table.add_column("Type", style="cyan")
    table.add_column("Interface")
    table.add_column("IP Address", style="green")
    table.add_column("Speed", justify="right")

    for iface in get_interfaces(include_loopback=True):
        icon = iface.interface_type.icon()
        name = iface.interface_type.display_name()
        table.add_row(f"{icon} {name}", iface.name, iface.ip_address, iface.speed_display)

    console.print(table)
    best = get_interfaces()
    if best:
        b = best[0]
        console.print(f"\n[bold green][OK] Recommended:[/bold green] {b.interface_type.display_name()} - {b.ip_address}")
