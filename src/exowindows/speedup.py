from typing import List, Dict, Any, Optional

def estimate_tflops(gpu_model: Optional[str], cpu_cores: int) -> float:
    """Estimate FP16/FP32 TFLOPS based on GPU model or CPU cores."""
    if gpu_model:
        g = gpu_model.lower()
        if "4090" in g:
            return 83.0
        if "4080" in g:
            return 49.0
        if "4070" in g:
            return 29.0
        if "4060" in g:
            return 15.0
        if "3090" in g:
            return 36.0
        if "3080" in g:
            return 30.0
        if "3070" in g:
            return 20.0
        if "3060" in g:
            return 13.0
        if "a100" in g:
            return 78.0
        if "h100" in g:
            return 150.0
        if "apple" in g or "silicon" in g:
            return 22.0
        if "rtx" in g or "nvidia" in g:
            return 18.0
        return 10.0  # Fallback for older/unrecognized GPU
    
    # CPU only
    return float(cpu_cores) * 0.15

def get_link_bandwidth_gbps(interface_type: str, speed_mbps: Optional[int]) -> float:
    """Get bandwidth in Gbps based on interface type or speed."""
    if speed_mbps:
        return float(speed_mbps) / 1000.0
        
    t = interface_type.lower()
    if "thunderbolt" in t or "tb" in t:
        return 40.0
    if "usb-c" in t or "usb 3.2" in t:
        return 10.0
    if "ethernet" in t:
        return 1.0
    if "wifi" in t or "wireless" in t:
        return 0.3
    return 0.1

def estimate_speedup(local_caps: Dict[str, Any], remote_nodes: List[Dict[str, Any]], model_size_gb: float = 15.0) -> tuple[float, str]:
    """
    Calculate estimated training speedup using a communication-weighted Amdahl's Law.
    Returns:
        tuple: (speedup_factor, formatted_description)
    """
    local_tflops = estimate_tflops(local_caps.get("gpu_model"), local_caps.get("cpu_cores", 4))
    
    # Model bandwidth requirement: rough estimate of transfer size during training
    # For training forward/backward passes, we pass activations and gradients.
    # Higher bandwidth = less communication bottleneck.
    model_req_gbps = model_size_gb * 0.5
    
    total_effective_tflops = local_tflops
    
    for r in remote_nodes:
        # Check if RAM is qualified (must be >= 3200 MHz if detectable)
        ram_speed = r.get("ram_speed_mhz")
        if ram_speed is not None and ram_speed < 3200:
            # Skip this node or penalize heavily as RAM speed constraint is unmet
            continue
            
        r_tflops = estimate_tflops(r.get("gpu_model"), r.get("cpu_cores", 4))
        
        # Link efficiency based on connection
        iface_type = r.get("connection_type", "Ethernet")
        speed_mbps = r.get("connection_speed_mbps")
        link_bw = get_link_bandwidth_gbps(iface_type, speed_mbps)
        
        link_efficiency = min(1.0, link_bw / model_req_gbps)
        
        # Add to total cluster capacity
        total_effective_tflops += r_tflops * link_efficiency

    speedup = total_effective_tflops / local_tflops
    pct_increase = int((speedup - 1.0) * 100)
    
    if pct_increase <= 0:
        desc = "No estimated speedup"
    else:
        desc = f"+{pct_increase}% Training Speed Increase"
        
    return speedup, desc
