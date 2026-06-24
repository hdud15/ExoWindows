from dataclasses import dataclass
from typing import List, Dict, Any, Optional

@dataclass
class AllocationPool:
    name: str
    role_type: str  # "LOCAL_GPU", "REMOTE_GPU", "LOCAL_RAM", "REMOTE_RAM", "LOCAL_CPU"
    capacity_gb: float
    priority: int
    details: str

def allocate_layers(
    num_layers: int,
    local_caps: Dict[str, Any],
    remote_nodes: List[Dict[str, Any]],
    mem_per_layer_gb: float = 0.8
) -> List[Dict[str, Any]]:
    """
    Allocate model layers to GPU VRAM, System RAM, and CPU according to resource priorities.
    RAM speed must be >= 3200 MHz to qualify for RAM offloading.
    """
    pools: List[AllocationPool] = []

    # 1. Local GPU VRAM
    if local_caps.get("gpu_model") and local_caps.get("gpu_vram_gb"):
        pools.append(AllocationPool(
            name="Local GPU",
            role_type="LOCAL_GPU",
            capacity_gb=local_caps["gpu_vram_gb"],
            priority=1,
            details=f"{local_caps['gpu_model']} ({local_caps['gpu_vram_gb']}GB VRAM)"
        ))

    # 2. Remote GPU VRAM
    for idx, r in enumerate(remote_nodes):
        if r.get("gpu_model") and r.get("gpu_vram_gb"):
            # Check RAM constraint if they offload or pass through
            ram_speed = r.get("ram_speed_mhz")
            if ram_speed is not None and ram_speed < 3200:
                continue # Skip slow RAM devices entirely
            conn = r.get("connection_type", "Network")
            pools.append(AllocationPool(
                name=r.get("node_name", f"Remote-PC{idx}"),
                role_type="REMOTE_GPU",
                capacity_gb=r["gpu_vram_gb"],
                priority=2,
                details=f"{r.get('node_name', f'Remote-PC{idx}')}: {r['gpu_model']} ({r['gpu_vram_gb']}GB VRAM) via {conn}"
            ))

    # 3. Local System RAM
    # Leave 4GB free for OS/runtime
    local_ram_avail = max(0.0, local_caps.get("system_ram_gb", 8.0) - 4.0)
    ram_speed_str = f" DDR-{local_caps['ram_speed_mhz']}" if local_caps.get("ram_speed_mhz") else ""
    pools.append(AllocationPool(
        name="Local RAM",
        role_type="LOCAL_RAM",
        capacity_gb=local_ram_avail,
        priority=3,
        details=f"Local RAM ({local_caps.get('system_ram_gb')}GB{ram_speed_str})"
    ))

    # 4. Remote System RAM (Only if speed >= 3200 MHz)
    for idx, r in enumerate(remote_nodes):
        ram_speed = r.get("ram_speed_mhz")
        if ram_speed is not None and ram_speed < 3200:
            continue
        
        r_ram = r.get("system_ram_gb", 8.0)
        r_ram_avail = max(0.0, r_ram - 4.0)
        conn = r.get("connection_type", "Network")
        ram_speed_str = f" DDR-{ram_speed}" if ram_speed else ""
        pools.append(AllocationPool(
            name=r.get("node_name", f"Remote-PC{idx} RAM"),
            role_type="REMOTE_RAM",
            capacity_gb=r_ram_avail,
            priority=4,
            details=f"{r.get('node_name', f'Remote-PC{idx}')}: RAM ({r_ram}GB{ram_speed_str}) via {conn}"
        ))

    # 5. Local CPU (as fallback)
    pools.append(AllocationPool(
        name="Local CPU",
        role_type="LOCAL_CPU",
        capacity_gb=9999.0, # Virtually unlimited but low priority
        priority=5,
        details=f"Local CPU ({local_caps.get('cpu_cores', 4)} Cores)"
    ))

    # Sort pools by priority
    pools.sort(key=lambda p: p.priority)

    # Distribute layers
    allocations = []
    layer_idx = 0

    for pool in pools:
        if layer_idx >= num_layers:
            break
            
        # How many layers can fit in this pool?
        max_layers_fit = int(pool.capacity_gb / mem_per_layer_gb)
        if max_layers_fit <= 0 and pool.priority < 5:
            continue
            
        if pool.priority == 5:  # CPU fallback
            layers_to_alloc = num_layers - layer_idx
        else:
            layers_to_alloc = min(max_layers_fit, num_layers - layer_idx)
            
        if layers_to_alloc > 0:
            start_layer = layer_idx
            end_layer = layer_idx + layers_to_alloc - 1
            allocations.append({
                "pool_name": pool.name,
                "role_type": pool.role_type,
                "start_layer": start_layer,
                "end_layer": end_layer,
                "layers_count": layers_to_alloc,
                "details": pool.details
            })
            layer_idx += layers_to_alloc

    # If some layers remain unallocated, force them to local CPU
    if layer_idx < num_layers:
        allocations.append({
            "pool_name": "Local CPU (Forced)",
            "role_type": "LOCAL_CPU",
            "start_layer": layer_idx,
            "end_layer": num_layers - 1,
            "layers_count": num_layers - layer_idx,
            "details": f"Local CPU ({local_caps.get('cpu_cores', 4)} Cores)"
        })

    return allocations
