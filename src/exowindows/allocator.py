from dataclasses import dataclass
from typing import List, Dict, Any, Optional

@dataclass
class AllocationPool:
    name: str
    role_type: str
    capacity_gb: float
    priority: int
    details: str
    bandwidth_mbps: float = 0.0

def allocate_layers(
    num_layers: int,
    local_caps: Dict[str, Any],
    remote_nodes: List[Dict[str, Any]],
    mem_per_layer_gb: float = 0.8,
    priority_order: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Allocate model layers to GPU VRAM, System RAM, and CPU according to resource priorities.
    RAM speed must be >= 3200 MHz to qualify for RAM offloading.
    """
    pools: List[AllocationPool] = []

    priorities = {
        name: index + 1
        for index, name in enumerate(
            priority_order
            or [
                "LOCAL_DEDICATED_VRAM",
                "REMOTE_DEDICATED_VRAM",
                "LOCAL_SHARED_VRAM",
                "REMOTE_SHARED_VRAM",
                "LOCAL_NPU",
                "REMOTE_NPU",
                "LOCAL_RAM",
                "REMOTE_RAM",
                "LOCAL_CPU",
                "REMOTE_CPU",
            ]
        )
    }

    def priority(name: str) -> int:
        return priorities.get(name, 100)

    def conn_score(node: Dict[str, Any]) -> float:
        speed = node.get("connection_speed_mbps") or node.get("speed_mbps") or 0
        try:
            return float(speed)
        except (TypeError, ValueError):
            return 0.0

    # 1. Local dedicated GPU VRAM
    if local_caps.get("gpu_model") and local_caps.get("gpu_vram_gb"):
        pools.append(AllocationPool(
            name="Local GPU",
            role_type="LOCAL_DEDICATED_VRAM",
            capacity_gb=local_caps["gpu_vram_gb"],
            priority=priority("LOCAL_DEDICATED_VRAM"),
            details=f"{local_caps['gpu_model']} ({local_caps['gpu_vram_gb']}GB VRAM)"
        ))

    # 2. Remote dedicated GPU VRAM
    for idx, r in enumerate(remote_nodes):
        if r.get("gpu_model") and r.get("gpu_vram_gb"):
            conn = r.get("connection_type", "Network")
            pools.append(AllocationPool(
                name=r.get("node_name", f"Remote-PC{idx}"),
                role_type="REMOTE_DEDICATED_VRAM",
                capacity_gb=r["gpu_vram_gb"],
                priority=priority("REMOTE_DEDICATED_VRAM"),
                details=f"{r.get('node_name', f'Remote-PC{idx}')}: {r['gpu_model']} ({r['gpu_vram_gb']}GB VRAM) via {conn}",
                bandwidth_mbps=conn_score(r),
            ))

    # 3. Shared GPU memory. It is slower than dedicated VRAM but better than raw CPU RAM.
    if local_caps.get("gpu_model") and local_caps.get("gpu_shared_vram_gb"):
        pools.append(AllocationPool(
            name="Local Shared VRAM",
            role_type="LOCAL_SHARED_VRAM",
            capacity_gb=local_caps["gpu_shared_vram_gb"],
            priority=priority("LOCAL_SHARED_VRAM"),
            details=f"{local_caps['gpu_model']} shared memory ({local_caps['gpu_shared_vram_gb']}GB)",
        ))

    for idx, r in enumerate(remote_nodes):
        if r.get("gpu_model") and r.get("gpu_shared_vram_gb"):
            conn = r.get("connection_type", "Network")
            pools.append(AllocationPool(
                name=r.get("node_name", f"Remote-PC{idx} Shared VRAM"),
                role_type="REMOTE_SHARED_VRAM",
                capacity_gb=r["gpu_shared_vram_gb"],
                priority=priority("REMOTE_SHARED_VRAM"),
                details=f"{r.get('node_name', f'Remote-PC{idx}')}: {r['gpu_model']} shared memory ({r['gpu_shared_vram_gb']}GB) via {conn}",
                bandwidth_mbps=conn_score(r),
            ))

    # 4. NPUs. Capacity is represented as an equivalent layer budget when provided.
    if local_caps.get("npu_model"):
        pools.append(AllocationPool(
            name="Local NPU",
            role_type="LOCAL_NPU",
            capacity_gb=local_caps.get("npu_memory_gb") or max(1.0, float(local_caps.get("npu_tops", 8)) / 8.0),
            priority=priority("LOCAL_NPU"),
            details=f"{local_caps['npu_model']} ({local_caps.get('npu_tops', 'unknown')} TOPS)",
        ))

    for idx, r in enumerate(remote_nodes):
        if r.get("npu_model"):
            conn = r.get("connection_type", "Network")
            pools.append(AllocationPool(
                name=r.get("node_name", f"Remote-PC{idx} NPU"),
                role_type="REMOTE_NPU",
                capacity_gb=r.get("npu_memory_gb") or max(1.0, float(r.get("npu_tops", 8)) / 8.0),
                priority=priority("REMOTE_NPU"),
                details=f"{r.get('node_name', f'Remote-PC{idx}')}: {r['npu_model']} ({r.get('npu_tops', 'unknown')} TOPS) via {conn}",
                bandwidth_mbps=conn_score(r),
            ))

    # 5. Local System RAM
    # Leave 4GB free for OS/runtime
    local_ram_avail = max(0.0, local_caps.get("system_ram_gb", 8.0) - 4.0)
    ram_speed_str = f" DDR-{local_caps['ram_speed_mhz']}" if local_caps.get("ram_speed_mhz") else ""
    pools.append(AllocationPool(
        name="Local RAM",
        role_type="LOCAL_RAM",
        capacity_gb=local_ram_avail,
        priority=priority("LOCAL_RAM"),
        details=f"Local RAM ({local_caps.get('system_ram_gb')}GB{ram_speed_str})"
    ))

    # 6. Remote System RAM (Only if speed >= 3200 MHz)
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
            priority=priority("REMOTE_RAM"),
            details=f"{r.get('node_name', f'Remote-PC{idx}')}: RAM ({r_ram}GB{ram_speed_str}) via {conn}",
            bandwidth_mbps=conn_score(r),
        ))

    # 7. CPU fallback
    pools.append(AllocationPool(
        name="Local CPU",
        role_type="LOCAL_CPU",
        capacity_gb=9999.0, # Virtually unlimited but low priority
        priority=priority("LOCAL_CPU"),
        details=f"Local CPU ({local_caps.get('cpu_cores', 4)} Cores)"
    ))

    for idx, r in enumerate(remote_nodes):
        if r.get("allow_remote_cpu"):
            conn = r.get("connection_type", "Network")
            pools.append(AllocationPool(
                name=r.get("node_name", f"Remote-PC{idx} CPU"),
                role_type="REMOTE_CPU",
                capacity_gb=9999.0,
                priority=priority("REMOTE_CPU"),
                details=f"{r.get('node_name', f'Remote-PC{idx}')}: CPU ({r.get('cpu_cores', 4)} Cores) via {conn}",
                bandwidth_mbps=conn_score(r),
            ))

    pools.sort(key=lambda p: (p.priority, -p.bandwidth_mbps))

    # Distribute layers
    allocations = []
    layer_idx = 0

    for pool in pools:
        if layer_idx >= num_layers:
            break
            
        # How many layers can fit in this pool?
        max_layers_fit = int(pool.capacity_gb / mem_per_layer_gb)
        if max_layers_fit <= 0 and pool.role_type not in ("LOCAL_CPU", "REMOTE_CPU"):
            continue
            
        if pool.role_type in ("LOCAL_CPU", "REMOTE_CPU"):
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
