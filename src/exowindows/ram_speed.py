import platform
import subprocess
from typing import Optional

def get_ram_speed_mhz() -> Optional[int]:
    """Detect RAM speed in MHz across Windows, Linux, and macOS."""
    sys_type = platform.system()
    if sys_type == "Windows":
        try:
            import wmi  # type: ignore[import-untyped]
            w = wmi.WMI()
            speeds = []
            for mem in w.Win32_PhysicalMemory():
                if getattr(mem, "Speed", None):
                    try:
                        speeds.append(int(mem.Speed))
                    except (ValueError, TypeError):
                        pass
            if speeds:
                return max(speeds)
        except Exception:
            pass
        # Fallback command if WMI import fails or returns nothing
        try:
            res = subprocess.run(
                ["wmic", "memorychip", "get", "speed"],
                capture_output=True,
                text=True,
                timeout=3,
                shell=True
            )
            speeds = []
            for line in res.stdout.splitlines():
                line = line.strip()
                if line and line.isdigit():
                    speeds.append(int(line))
            if speeds:
                return max(speeds)
        except Exception:
            pass

    elif sys_type == "Linux":
        try:
            res = subprocess.run(["dmidecode", "-t", "memory"], capture_output=True, text=True, timeout=2)
            speeds = []
            for line in res.stdout.splitlines():
                if "Speed:" in line and "MHz" in line:
                    parts = line.split("Speed:")[-1].split()
                    if parts and parts[0].isdigit():
                        speeds.append(int(parts[0]))
            if speeds:
                return max(speeds)
        except Exception:
            pass
        # Alternative via lshw if dmidecode fails
        try:
            res = subprocess.run(["lshw", "-class", "memory"], capture_output=True, text=True, timeout=2)
            for line in res.stdout.splitlines():
                if "clock" in line or "clock:" in line:
                    parts = line.split("clock:")[-1].split()
                    if not parts:
                        parts = line.split("clock")[-1].split()
                    if parts and "MHz" in parts[0] or (len(parts) > 1 and parts[1].startswith("MHz")):
                        speed_str = "".join(c for c in parts[0] if c.isdigit())
                        if speed_str:
                            return int(speed_str)
        except Exception:
            pass

    elif sys_type == "Darwin":
        try:
            res = subprocess.run(["system_profiler", "SPMemoryDataType"], capture_output=True, text=True, timeout=2)
            speeds = []
            for line in res.stdout.splitlines():
                if "Speed:" in line:
                    parts = line.split("Speed:")[-1].split()
                    if parts:
                        val = "".join(c for c in parts[0] if c.isdigit())
                        if val:
                            speeds.append(int(val))
            if speeds:
                return max(speeds)
        except Exception:
            pass

    return None

def is_qualified_ram(speed: Optional[int]) -> bool:
    """Return True if RAM speed is at least 3200 MHz or undetectable (safe fallback)."""
    if speed is None:
        return True
    return speed >= 3200
