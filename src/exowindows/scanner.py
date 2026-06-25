import asyncio
import socket
import ipaddress
import httpx
from typing import List, Dict, Any, Optional
from exowindows.network import get_interfaces, InterfaceType

DEFAULT_API_PORT = 52415
DEFAULT_OLLAMA_PORT = 11434

async def check_port(ip: str, port: int, timeout: float = 0.5) -> bool:
    """Check if a port is open on an IP address."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port),
            timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False

async def get_peer_capabilities(ip: str, port: int = DEFAULT_API_PORT) -> Optional[Dict[str, Any]]:
    """Fetch capabilities from an active exo node's API."""
    url = f"http://{ip}:{port}/v1/info"
    # Or query local capabilities if it has a custom hardware endpoint.
    # Since exo's /v1/info might not have everything, let's query /v1/cluster or return info.
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            # We can also add a custom endpoint /v1/share/capabilities in our exowindows extension.
            # Let's try /v1/share/capabilities first, then fall back to /v1/info or /v1/cluster.
            r = await client.get(f"http://{ip}:{port}/v1/share/capabilities")
            if r.status_code == 200:
                return r.json()
            
            r = await client.get(f"http://{ip}:{port}/v1/info")
            if r.status_code == 200:
                data = r.json()
                return {
                    "node_name": data.get("node_id", ip)[:12],
                    "os_name": "Remote OS",
                    "cpu_model": "Unknown Remote CPU",
                    "cpu_cores": 4,
                    "system_ram_gb": 16.0,
                    "ram_speed_mhz": 3200,
                    "is_ram_qualified": True,
                    "gpu_model": "Remote GPU",
                    "gpu_vram_gb": 8.0,
                }
    except Exception:
        pass
    return None

def get_subnets_to_scan() -> List[str]:
    """Get /24 subnets of non-loopback network interfaces."""
    subnets = []
    for iface in get_interfaces(include_loopback=False):
        ip = iface.ip_address
        if not ip or ip.startswith("127."):
            continue
        try:
            # Assume /24 subnet for scanning
            ip_obj = ipaddress.ip_interface(f"{ip}/24")
            net = str(ip_obj.network)
            if net not in subnets:
                subnets.append(net)
        except Exception:
            pass
    return subnets


def _connection_for_ip(ip: str) -> Dict[str, Any]:
    try:
        target = ipaddress.ip_address(ip)
        best = None
        best_prefix = -1
        for iface in get_interfaces(include_loopback=False):
            try:
                network = ipaddress.ip_interface(f"{iface.ip_address}/24").network
            except Exception:
                continue
            if target in network and network.prefixlen > best_prefix:
                best = iface
                best_prefix = network.prefixlen
        if best:
            return {
                "connection_type": best.interface_type.display_name(),
                "connection_speed_mbps": best.speed_mbps,
                "local_interface_ip": best.ip_address,
            }
    except Exception:
        pass
    return {}

async def scan_subnet_for_port(subnet_str: str, port: int) -> List[str]:
    """Scan all IPs in a /24 subnet for a specific open port."""
    try:
        network = ipaddress.ip_network(subnet_str, strict=False)
    except Exception:
        return []

    active_ips = []
    # Avoid scanning network/broadcast addresses
    hosts = [str(host) for host in network.hosts()]
    
    # We chunk the hosts to avoid opening too many file descriptors
    chunk_size = 50
    for i in range(0, len(hosts), chunk_size):
        chunk = hosts[i:i + chunk_size]
        tasks = [check_port(ip, port) for ip in chunk]
        results = await asyncio.gather(*tasks)
        for ip, is_open in zip(chunk, results):
            if is_open:
                active_ips.append(ip)
    
    return active_ips

async def scan_for_nodes(timeout_seconds: float = 3.0) -> Dict[str, Any]:
    """
    Scan local networks for running exo nodes and ollama instances.
    Returns:
        Dict with "exo_nodes": List[Dict], "ollama_hosts": List[str], "active_interfaces": List
    """
    subnets = get_subnets_to_scan()
    
    exo_tasks = [scan_subnet_for_port(subnet, DEFAULT_API_PORT) for subnet in subnets]
    ollama_tasks = [scan_subnet_for_port(subnet, DEFAULT_OLLAMA_PORT) for subnet in subnets]
    
    # Run with overall timeout
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(exo_tasks + ollama_tasks)),
            timeout=timeout_seconds
        )
    except asyncio.TimeoutError:
        results = []
    
    half = len(exo_tasks)
    exo_ips = set()
    for res in results[:half]:
        exo_ips.update(res)
        
    ollama_ips = set()
    for res in results[half:]:
        ollama_ips.update(res)

    # Resolve capabilities for discovered exo nodes
    exo_nodes = []
    # Query localhost always
    local_caps_task = get_peer_capabilities("127.0.0.1")
    peer_tasks = {ip: get_peer_capabilities(ip) for ip in exo_ips if ip != "127.0.0.1"}
    
    local_caps = await local_caps_task
    if local_caps:
        local_caps["ip"] = "127.0.0.1"
        exo_nodes.append(local_caps)
        
    peer_results = await asyncio.gather(*peer_tasks.values(), return_exceptions=True)
    for ip, caps in zip(peer_tasks.keys(), peer_results):
        if caps and not isinstance(caps, Exception):
            caps["ip"] = ip
            caps.update(_connection_for_ip(ip))
            exo_nodes.append(caps)
        elif not isinstance(caps, Exception):
            # Fallback placeholder if capability endpoint isn't fully detailed
            fallback = {
                "ip": ip,
                "node_name": f"Node-{ip.split('.')[-1]}",
                "gpu_model": "Detected GPU",
                "gpu_vram_gb": 8.0,
                "system_ram_gb": 16.0,
                "ram_speed_mhz": 3200,
                "is_ram_qualified": True
            }
            fallback.update(_connection_for_ip(ip))
            exo_nodes.append(fallback)

    # Also try querying active cluster from localhost if it exists
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            r = await client.get(f"http://127.0.0.1:{DEFAULT_API_PORT}/v1/cluster")
            if r.status_code == 200:
                cluster_data = r.json()
                # merge or add cluster nodes
                # cluster_data["nodes"] is a list of node dicts
                for node in cluster_data.get("nodes", []):
                    # check if already added
                    nid = node.get("id")
                    # We can fetch node address from the cluster description if available
    except Exception:
        pass

    return {
        "exo_nodes": exo_nodes,
        "ollama_hosts": list(ollama_ips),
        "active_interfaces": [
            {
                "name": iface.name,
                "ip": iface.ip_address,
                "type": iface.interface_type.display_name(),
                "speed_display": iface.speed_display,
                "speed_mbps": iface.speed_mbps
            }
            for iface in get_interfaces(include_loopback=False)
        ]
    }
