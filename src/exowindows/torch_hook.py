import os
import sys
import time
import asyncio
import subprocess
from dataclasses import asdict
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from exowindows.devices import get_local_capabilities
from exowindows.scanner import scan_for_nodes
from exowindows.speedup import estimate_speedup
from exowindows.allocator import allocate_layers

console = Console()

def get_keyboard_input_windows(timeout: float = 10.0, default: str = "n") -> str:
    """Read a key on Windows with a visual countdown timer."""
    import msvcrt
    start_time = time.time()
    last_seconds = int(timeout)
    
    # Print initial line
    sys.stdout.write(f"\r  [Y] Use distributed  [N] Run local  [timeout: {last_seconds}s] ")
    sys.stdout.flush()
    
    while True:
        elapsed = time.time() - start_time
        remaining = max(0, int(timeout - elapsed))
        
        if remaining != last_seconds:
            last_seconds = remaining
            sys.stdout.write(f"\r  [Y] Use distributed  [N] Run local  [timeout: {remaining}s] ")
            sys.stdout.flush()
            
        if elapsed >= timeout:
            sys.stdout.write("\n")
            return default
            
        if msvcrt.kbhit():
            key = msvcrt.getch()
            # Decode key
            try:
                char = key.decode("utf-8").lower()
            except Exception:
                char = ""
            if char in ("y", "n"):
                sys.stdout.write(f"\n  Selected: {char.upper()}\n")
                return char
            elif char in ("\r", "\n"):
                sys.stdout.write(f"\n  Selected: {default.upper()} (default)\n")
                return default
                
        time.sleep(0.05)

def get_keyboard_input_unix(timeout: float = 10.0, default: str = "n") -> str:
    """Read a key on Linux/macOS with a visual countdown timer."""
    import select
    import tty
    import termios
    
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(sys.stdin.fileno())
        start_time = time.time()
        last_seconds = int(timeout)
        
        sys.stdout.write(f"\r  [Y] Use distributed  [N] Run local  [timeout: {last_seconds}s] ")
        sys.stdout.flush()
        
        while True:
            elapsed = time.time() - start_time
            remaining = max(0, int(timeout - elapsed))
            
            if remaining != last_seconds:
                last_seconds = remaining
                # We need to use carriage return to overwrite
                sys.stdout.write(f"\r  [Y] Use distributed  [N] Run local  [timeout: {remaining}s] ")
                sys.stdout.flush()
                
            if elapsed >= timeout:
                return default
                
            rlist, _, _ = select.select([sys.stdin], [], [], 0.05)
            if rlist:
                char = sys.stdin.read(1).lower()
                if char in ("y", "n"):
                    return char
                elif char in ("\r", "\n"):
                    return default
    except Exception:
        # Fallback if raw mode fails
        time.sleep(timeout)
        return default
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\n")

def get_user_choice(timeout: float = 10.0, default: str = "n") -> str:
    """Get Y/N choice from user with platform-specific timeout input."""
    if platform_is_windows():
        return get_keyboard_input_windows(timeout, default)
    else:
        return get_keyboard_input_unix(timeout, default)

def platform_is_windows() -> bool:
    return sys.platform == "win32"

def show_recommendation_box(local_caps: Dict[str, Any], remote_nodes: List[Dict[str, Any]]) -> str:
    """Render a beautiful recommendation panel and request user feedback."""
    if not remote_nodes:
        # No remote nodes found, run locally
        return "n"

    num_devices = len(remote_nodes)
    device_plural = "device" if num_devices == 1 else "devices"
    
    # Calculate speedup
    speedup, speedup_desc = estimate_speedup(local_caps, remote_nodes)
    
    # Build text for nodes
    node_lines = []
    for r in remote_nodes:
        node_name = r.get("node_name", "Remote Node")
        ip = r.get("ip", "Unknown IP")
        gpu = r.get("gpu_model") or r.get("npu_model") or "CPU Only"
        if r.get("gpu_vram_gb"):
            vram = f"{r.get('gpu_vram_gb')}GB dedicated VRAM"
        elif r.get("gpu_shared_vram_gb"):
            vram = f"{r.get('gpu_shared_vram_gb')}GB shared VRAM"
        elif r.get("npu_tops"):
            vram = f"{r.get('npu_tops')} TOPS NPU"
        else:
            vram = "System RAM"
        conn = r.get("connection_type", "Ethernet")
        node_lines.append(f"  - [cyan]{node_name:<12}[/cyan] [{ip:<14}]  {gpu} ({vram}) via {conn}")
        
    nodes_text = "\n".join(node_lines)
    
    # Estimates
    est_local_time = 10.0  # arbitrary scale
    est_dist_time = round(est_local_time / speedup, 1)
    time_str = f"Est. time   : {est_local_time} min -> {est_dist_time} min"

    # Layer allocations preview
    allocs = allocate_layers(32, local_caps, remote_nodes)
    alloc_lines = []
    for a in allocs:
        alloc_lines.append(f"    Layers {a['start_layer']}-{a['end_layer']} -> {a['pool_name']} ({a['details']})")
    alloc_text = "\n".join(alloc_lines)

    panel_content = Text.from_markup(
        f"[bold green]RECOMMENDATION: Run with {num_devices} connected {device_plural}[/bold green]\n\n"
        f"{nodes_text}\n\n"
        f"  [bold yellow]Estimated: {speedup_desc}[/bold yellow]\n"
        f"  {time_str}\n\n"
        f"  [bold]Heterogeneous Layer Allocation Preview (e.g. 32 layers):[/bold]\n"
        f"{alloc_text}\n"
    )

    panel = Panel(
        panel_content,
        title="[bold]exowindows Optimizer[/bold]",
        border_style="green",
        expand=False
    )
    console.print(panel)
    
    choice = get_user_choice(timeout=10.0, default="n")
    return choice

def start_distributed_cluster(local_caps: Dict[str, Any], remote_nodes: List[Dict[str, Any]]):
    """Start local daemon and connect remote nodes."""
    console.print("\n[bold green][OK] Initializing distributed cluster...[/bold green]")
    try:
        # Check if local daemon is running on port 52415
        import httpx
        r = httpx.get("http://127.0.0.1:52415/node_id", timeout=1.0)
        if r.status_code == 200:
            console.print("  [dim]Local exo daemon is already running.[/dim]")
            return
    except Exception:
        pass

    console.print("  [yellow]Local exo daemon not detected. Starting in background...[/yellow]")
    # Run the exo master daemon
    try:
        # Try running using uv run if in workspace or sys.executable -m exo
        # Since host has exo, we can launch it:
        # We start it as a background process redirecting output to a log file
        log_file = open("exowindows_daemon.log", "w")
        subprocess.Popen(
            [sys.executable, "-m", "exo"],
            stdout=log_file,
            stderr=log_file,
            close_fds=True
        )
        console.print("  [green]Local exo daemon launched. Log: exowindows_daemon.log[/green]")
        # Give it a second to bind
        time.sleep(2.0)
    except Exception as e:
        console.print(f"  [red]Failed to start local exo daemon: {e}[/red]")

@contextmanager
def distributed():
    """Context manager to wrap PyTorch training and suggest scaling."""
    console.print("\n[bold cyan]exowindows: Scanning network for compute nodes...[/bold cyan]")
    local_caps = get_local_capabilities()
    
    # Run async scanner
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # Fallback scan in running loop
        import nest_asyncio
        nest_asyncio.apply()
    
    scan_results = asyncio.run(scan_for_nodes(timeout_seconds=3.0))
    remote_nodes = scan_results.get("exo_nodes", [])
    # Filter out localhost from remote nodes
    remote_nodes = [r for r in remote_nodes if r.get("ip") != "127.0.0.1"]
    
    choice = show_recommendation_box(asdict(local_caps), remote_nodes)
    if choice == "y":
        start_distributed_cluster(asdict(local_caps), remote_nodes)
        
    try:
        yield
    finally:
        pass

def patch_pytorch():
    """Auto-patch PyTorch modules to hook at startup/import time."""
    try:
        import torch.nn
        original_init = torch.nn.Module.__init__
        
        has_run = False
        
        def patched_init(self, *args, **kwargs):
            nonlocal has_run
            if not has_run:
                has_run = True
                console.print("[dim]exowindows: PyTorch training module detected, scanning...[/dim]")
                # Run the hook
                with distributed():
                    pass
            original_init(self, *args, **kwargs)
            
        torch.nn.Module.__init__ = patched_init
    except ImportError:
        pass
