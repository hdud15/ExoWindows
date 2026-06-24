import os
import platform
import psutil
import subprocess
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
from exowindows.ram_speed import get_ram_speed_mhz, is_qualified_ram

@dataclass
class DeviceCapabilities:
    node_name: str
    os_name: str
    cpu_model: str
    cpu_cores: int
    system_ram_gb: float
    ram_speed_mhz: Optional[int]
    is_ram_qualified: bool
    gpu_model: Optional[str] = None
    gpu_vram_gb: Optional[float] = None
    gpu_shared_vram_gb: Optional[float] = None

def get_cpu_model() -> str:
    """Retrieve cpu model name."""
    sys_type = platform.system()
    if sys_type == "Windows":
        try:
            import wmi
            w = wmi.WMI()
            for cpu in w.Win32_Processor():
                return cpu.Name.strip()
        except Exception:
            pass
        return platform.processor() or "Unknown CPU"
    elif sys_type == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass
    elif sys_type == "Darwin":
        try:
            res = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=2
            )
            return res.stdout.strip()
        except Exception:
            pass
    return platform.machine() or "Unknown CPU"

def get_gpu_info() -> tuple[Optional[str], Optional[float], Optional[float]]:
    """Detect GPU model, VRAM size in GB, and shared VRAM size in GB."""
    gpu_model: Optional[str] = None
    gpu_vram_gb: Optional[float] = None
    gpu_shared_vram_gb: Optional[float] = None

    # First try torch if installed
    try:
        import torch
        if torch.cuda.is_available():
            gpu_model = torch.cuda.get_device_name(0)
            gpu_vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
            # Shared VRAM on Windows with CUDA can be up to 50% of system RAM
            gpu_shared_vram_gb = (psutil.virtual_memory().total * 0.5) / (1024 ** 3)
            return gpu_model, round(gpu_vram_gb, 2), round(gpu_shared_vram_gb, 2)
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            gpu_model = "Apple Silicon GPU"
            gpu_vram_gb = (psutil.virtual_memory().total * 0.75) / (1024 ** 3)
            return gpu_model, round(gpu_vram_gb, 2), None
    except ImportError:
        pass

    # Try nvidia-smi
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
            shell=True if platform.system() == "Windows" else False
        )
        if res.returncode == 0:
            lines = res.stdout.strip().split("\n")
            if lines and "," in lines[0]:
                name, mem_str = lines[0].split(",")
                gpu_model = name.strip()
                gpu_vram_gb = float(mem_str.strip()) / 1024.0
                gpu_shared_vram_gb = (psutil.virtual_memory().total * 0.5) / (1024 ** 3)
                return gpu_model, round(gpu_vram_gb, 2), round(gpu_shared_vram_gb, 2)
    except Exception:
        pass

    # Windows specific WMI check for GPUs (Intel, AMD, Nvidia) if nvidia-smi or torch not present
    if platform.system() == "Windows":
        try:
            import wmi
            w = wmi.WMI()
            for controller in w.Win32_VideoController():
                name = controller.Name
                # Avoid Microsoft Basic Display Adapter
                if "basic display" not in name.lower():
                    gpu_model = name
                    # AdapterRAM might be returned as unsigned int
                    if controller.AdapterRAM:
                        # Sometimes AdapterRAM is represented in unsigned int32, which overflows or underflows
                        ram = int(controller.AdapterRAM)
                        if ram > 0:
                            gpu_vram_gb = ram / (1024 ** 3)
                    gpu_shared_vram_gb = (psutil.virtual_memory().total * 0.5) / (1024 ** 3)
                    break
        except Exception:
            pass

    return gpu_model, gpu_vram_gb, gpu_shared_vram_gb

def get_local_capabilities() -> DeviceCapabilities:
    """Retrieve capabilities of the local host."""
    ram_speed = get_ram_speed_mhz()
    qualified = is_qualified_ram(ram_speed)
    system_ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 2)
    gpu_model, gpu_vram, gpu_shared = get_gpu_info()

    return DeviceCapabilities(
        node_name=platform.node(),
        os_name=f"{platform.system()} {platform.release()}",
        cpu_model=get_cpu_model(),
        cpu_cores=psutil.cpu_count(logical=False) or psutil.cpu_count() or 1,
        system_ram_gb=system_ram_gb,
        ram_speed_mhz=ram_speed,
        is_ram_qualified=qualified,
        gpu_model=gpu_model,
        gpu_vram_gb=gpu_vram,
        gpu_shared_vram_gb=gpu_shared,
    )
