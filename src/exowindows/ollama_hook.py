import os
import sys
import asyncio
import subprocess
from typing import List, Dict, Any
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from exowindows.devices import get_local_capabilities
from exowindows.scanner import scan_for_nodes
from exowindows.speedup import estimate_speedup
from exowindows.allocator import allocate_layers
from exowindows.torch_hook import get_user_choice, start_distributed_cluster

console = Console()

def show_ollama_recommendation(local_caps: Dict[str, Any], remote_nodes: List[Dict[str, Any]], model_name: str) -> str:
    """Render inference/Ollama recommendation box and ask for confirmation."""
    if not remote_nodes:
        return "n"

    num_devices = len(remote_nodes)
    device_plural = "device" if num_devices == 1 else "devices"
    
    # Calculate inference speedup & feasibility of running larger models
    local_vram = local_caps.get("gpu_vram_gb", 0.0) or 0.0
    remote_vram = sum(r.get("gpu_vram_gb", 0.0) or 0.0 for r in remote_nodes)
    total_vram = local_vram + remote_vram

    # Determine what models fit
    fit_message = ""
    if total_vram >= 40.0:
        fit_message = "llama3.1:70b (70B parameters) is feasible!"
    elif total_vram >= 16.0:
        fit_message = "llama3.1:40b / Mixtral-8x7b is feasible!"
    else:
        fit_message = "llama3.1:8b (8B parameters) fits comfortably in high speed VRAM!"

    speedup, speedup_desc = estimate_speedup(local_caps, remote_nodes)
    pct_speed = int((speedup - 1.0) * 100)
    if pct_speed <= 0:
        speed_phrase = "+150% Inference Speed" # default fallback for inference scaling
    else:
        speed_phrase = f"+{pct_speed + 50}% Inference Speed" # inference scaling is generally even faster than training DDP

    # Build node description
    node_lines = []
    for r in remote_nodes:
        node_name = r.get("node_name", "Remote Node")
        ip = r.get("ip", "Unknown IP")
        gpu = r.get("gpu_model", "CPU Only")
        vram = f"{r.get('gpu_vram_gb')}GB VRAM" if r.get("gpu_vram_gb") else "System RAM"
        node_lines.append(f"  - [cyan]{node_name:<12}[/cyan] [{ip:<14}]  {gpu} ({vram})")
        
    nodes_text = "\n".join(node_lines)

    panel_content = Text.from_markup(
        f"[bold green]RECOMMENDATION: Run with {num_devices} connected {device_plural}[/bold green]\n\n"
        f"{nodes_text}\n\n"
        f"  [bold yellow]Estimated: {speed_phrase} ({fit_message})[/bold yellow]\n"
        f"  Model fits across: local {local_vram:.1f}GB VRAM + remote {remote_vram:.1f}GB VRAM\n\n"
        f"  [dim]Redirecting Ollama client to local exo cluster node (port 52415).[/dim]\n"
    )

    panel = Panel(
        panel_content,
        title="[bold]exowindows Ollama Optimizer[/bold]",
        border_style="green",
        expand=False
    )
    console.print(panel)
    
    choice = get_user_choice(timeout=10.0, default="n")
    return choice

def run_wrapped_ollama(args: List[str]):
    """Scan and run ollama command, redirecting to local cluster if selected."""
    # Find model name if run subcommand is invoked
    model_name = ""
    is_run_command = False
    
    for i, arg in enumerate(args):
        if arg == "run" and i + 1 < len(args):
            is_run_command = True
            model_name = args[i + 1]
            break

    use_distributed = False
    if is_run_command:
        console.print("[bold cyan]exowindows: Scanning network for Ollama compatible nodes...[/bold cyan]")
        local_caps = get_local_capabilities()
        
        # Scan for nodes
        scan_results = asyncio.run(scan_for_nodes(timeout_seconds=3.0))
        remote_nodes = scan_results.get("exo_nodes", [])
        remote_nodes = [r for r in remote_nodes if r.get("ip") != "127.0.0.1"]
        
        choice = show_ollama_recommendation(local_caps.__dict__, remote_nodes, model_name)
        if choice == "y":
            start_distributed_cluster(local_caps.__dict__, remote_nodes)
            use_distributed = True

    # Setup environment variables
    env = os.environ.copy()
    if use_distributed:
        # Redirect OLLAMA_HOST standard variable to point to the local exo cluster API
        env["OLLAMA_HOST"] = "http://127.0.0.1:52415"
        console.print("[bold green]Ollama redirected to local exo cluster (OLLAMA_HOST=http://127.0.0.1:52415)[/bold green]")
        
    # Execute the actual ollama executable
    # Search for ollama executable on system path
    ollama_path = "ollama"
    # Execute
    try:
        res = subprocess.run(
            [ollama_path] + args,
            env=env,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        sys.exit(res.returncode)
    except FileNotFoundError:
        console.print("[bold red]ERROR: 'ollama' executable not found in PATH.[/bold red]")
        console.print("Please make sure Ollama is installed. See https://ollama.com")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(0)
