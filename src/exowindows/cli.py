import os
import sys
import click
import httpx
import asyncio
from typing import Optional, List
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from exowindows.devices import get_local_capabilities
from exowindows.scanner import scan_for_nodes
from exowindows.ram_speed import get_ram_speed_mhz

console = Console()

def ensure_exo_installed(host: Optional[str] = None, api_port: int = 52415) -> bool:
    """Ensure the underlying exo engine and exo-rs binary packages are available."""
    try:
        import exo
        import exo_rs
        return True
    except ImportError:
        pass

    # If not present, pull from host PC
    if not host:
        console.print("[yellow]exo engine not found locally. Searching local networks for a host...[/yellow]")
        scan_res = asyncio.run(scan_for_nodes(timeout_seconds=3.0))
        nodes = scan_res.get("exo_nodes", [])
        nodes = [n for n in nodes if n.get("ip") != "127.0.0.1"]
        if nodes:
            host = nodes[0]["ip"]
            console.print(f"[green]Found host PC at: {host}[/green]")
        else:
            console.print("[red]ERROR: No running exo host discovered on the network, and --host was not provided.[/red]")
            console.print("Please make sure the exo server is active on the main machine and specify --host <IP>.")
            sys.exit(1)

    url = f"http://{host}:{api_port}/v1/share/exo_bundle.zip"
    console.print(f"[cyan]Downloading exo engine bundle from host {host}...[/cyan]")
    try:
        r = httpx.get(url, timeout=45.0)
        r.raise_for_status()
    except Exception as e:
        console.print(f"[red]Failed to download exo engine bundle: {e}[/red]")
        sys.exit(1)

    cache_dir = os.path.expanduser("~/.exowindows/cache")
    os.makedirs(cache_dir, exist_ok=True)

    console.print(f"[cyan]Extracting bundle to local cache: {cache_dir}...[/cyan]")
    import zipfile
    import io
    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        z.extractall(cache_dir)
    except Exception as e:
        console.print(f"[red]Failed to extract zip bundle: {e}[/red]")
        sys.exit(1)

    # Insert cache_dir at the beginning of the python path
    sys.path.insert(0, cache_dir)
    os.environ["PYTHONPATH"] = cache_dir + os.pathsep + os.environ.get("PYTHONPATH", "")
    return True

# ──────────────────────────────────────────────────────────────────────────────
# exowindows CLI Group
# ──────────────────────────────────────────────────────────────────────────────

@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli():
    """exowindows - Scale distributed ML/LLM workloads across Windows machines."""
    pass

@cli.command()
def scan():
    """Scan local networks for running nodes and display capabilities."""
    console.print("[bold cyan]Scanning local networks for exo and Ollama nodes...[/bold cyan]")
    local = get_local_capabilities()
    res = asyncio.run(scan_for_nodes(timeout_seconds=3.0))
    
    # Render table of discovered nodes
    table = Table(title="Discovered Cluster Nodes", show_header=True, header_style="bold cyan")
    table.add_column("IP Address", style="green")
    table.add_column("Node Name")
    table.add_column("GPU Model", style="yellow")
    table.add_column("VRAM", justify="right")
    table.add_column("RAM GB", justify="right")
    table.add_column("RAM Speed", justify="right")
    table.add_column("Qualified", justify="center")

    # Local node first
    local_speed = f"{local.ram_speed_mhz} MHz" if local.ram_speed_mhz else "Unknown"
    local_vram = f"{local.gpu_vram_gb:.1f} GB" if local.gpu_vram_gb else "—"
    local_qual = "[green]YES[/green]" if local.is_ram_qualified else "[red]NO (<3200)[/red]"
    table.add_row(
        "127.0.0.1 (Local)",
        local.node_name,
        local.gpu_model or "CPU Only",
        local_vram,
        f"{local.system_ram_gb:.1f} GB",
        local_speed,
        local_qual
    )

    for n in res.get("exo_nodes", []):
        ip = n.get("ip")
        if ip == "127.0.0.1":
            continue
        speed = f"{n.get('ram_speed_mhz')} MHz" if n.get("ram_speed_mhz") else "Unknown"
        vram = f"{n.get('gpu_vram_gb'):.1f} GB" if n.get("gpu_vram_gb") else "—"
        qual = "[green]YES[/green]" if n.get("is_ram_qualified", True) else "[red]NO (<3200)[/red]"
        table.add_row(
            ip,
            n.get("node_name", "Remote"),
            n.get("gpu_model") or "CPU Only",
            vram,
            f"{n.get('system_ram_gb', 0.0):.1f} GB",
            speed,
            qual
        )

    console.print(table)
    
    # Ollama hosts
    ollama_hosts = res.get("ollama_hosts", [])
    if ollama_hosts:
        console.print("\n[bold yellow]Discovered Ollama Servers:[/bold yellow]")
        for host in ollama_hosts:
            console.print(f"  • {host}:11434")

@cli.command()
@click.option("--host", "-H", default="127.0.0.1", help="Host IP address")
@click.option("--api-port", default=52415, help="Host API port")
def status(host, api_port):
    """Show connection status and active cluster info."""
    url = f"http://{host}:{api_port}/v1/cluster"
    try:
        r = httpx.get(url, timeout=3.0)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        console.print(f"[red]Could not connect to cluster daemon at {url}: {e}[/red]")
        sys.exit(1)

    nodes = data.get("nodes", [])
    table = Table(title=f"Cluster Status ({host}:{api_port})", show_header=True, header_style="bold cyan")
    table.add_column("Node ID")
    table.add_column("Status")
    table.add_column("Role")
    table.add_column("Model")
    table.add_column("Speed (Tokens/s)", justify="right")

    for node in nodes:
        nid = node.get("id", "?")[:12] + "…"
        status_str = "[green]online[/green]" if node.get("online") else "[red]offline[/red]"
        role = node.get("role", "worker")
        model = node.get("model", "—")
        tps = node.get("tokens_per_second")
        tps_str = f"{tps:.1f}" if tps is not None else "—"
        table.add_row(nid, status_str, role, model, tps_str)

    console.print(table)

@cli.command()
def interfaces():
    """List network interfaces with connection types and speeds."""
    ensure_exo_installed()
    from exowindows.network import print_interfaces
    print_interfaces()

@cli.command(context_settings={"ignore_unknown_options": True})
@click.argument("cmd_args", nargs=-1, type=click.UNPROCESSED)
def train(cmd_args):
    """Intercept training scripts with automatic distributed recommendations."""
    if not cmd_args:
        console.print("[red]ERROR: No command arguments provided to wrap. Example: exowindows train -- python script.py[/red]")
        sys.exit(1)
        
    cmd = list(cmd_args)
    if cmd[0] == "--":
        cmd = cmd[1:]
        
    # Execute under distributed context hook
    from exowindows.torch_hook import distributed
    with distributed():
        import subprocess
        res = subprocess.run(cmd, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
        sys.exit(res.returncode)

@cli.command(context_settings={"ignore_unknown_options": True})
@click.argument("cmd_args", nargs=-1, type=click.UNPROCESSED)
def ollama(cmd_args):
    """Wrap the Ollama CLI client to run models across connected nodes."""
    from exowindows.ollama_hook import run_wrapped_ollama
    run_wrapped_ollama(list(cmd_args))

@cli.command()
def benchmark():
    """Benchmark RAM speed and capabilities on this machine."""
    local = get_local_capabilities()
    console.print(Panel(
        Text.from_markup(
            f"[bold cyan]Local Machine Performance Profile[/bold cyan]\n\n"
            f"  CPU Model : {local.cpu_model}\n"
            f"  Cores     : {local.cpu_cores}\n"
            f"  System RAM: {local.system_ram_gb:.1f} GB\n"
            f"  RAM Speed : {local.ram_speed_mhz or 'Unknown'} MHz "
            f"{'[green](Qualified >=3200MHz)[/green]' if local.is_ram_qualified else '[red](Slow <3200MHz)[/red]'}\n"
            f"  GPU Model : {local.gpu_model or 'None'}\n"
            f"  GPU VRAM  : {f'{local.gpu_vram_gb:.1f} GB' if local.gpu_vram_gb else 'N/A'}\n"
        ),
        title="Benchmark Profile",
        border_style="cyan"
    ))

# ──────────────────────────────────────────────────────────────────────────────
# Entry Point Scripts
# ──────────────────────────────────────────────────────────────────────────────

@click.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.option("--host", "-H", default=None, help="Host IP address to connect to.")
@click.option("--api-port", default=52415, type=int, help="Host API port.")
@click.option(
    "--connection",
    type=click.Choice(["auto", "wifi", "ethernet", "usb", "usb-c", "usb-ethernet", "thunderbolt"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Preferred connection method.",
)
@click.option("--interface-ip", default=None, help="Manually pin the local interface IP.")
@click.pass_context
def node_cli(ctx, host, api_port, connection, interface_ip):
    """Worker node client. Join a cluster automatically."""
    ensure_exo_installed(host, api_port)
    
    # Re-route CLI execution to the downloaded/cached exo_node CLI
    from exo_node.cli import cli as exo_node_cli
    
    # We reconstruct the arguments for the command
    args = ctx.args
    # Omit --host and -H from args as we already parsed/handled it
    filtered_args = []
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a in ("--host", "-H"):
            skip_next = True
            continue
        filtered_args.append(a)

    # Automatically set subcommand to join if none provided
    if not filtered_args:
        filtered_args = ["join"]
        
    if host and "join" in filtered_args and "--host" not in filtered_args and "-H" not in filtered_args:
        filtered_args.extend(["--host", host])
    if "join" in filtered_args:
        if "--connection" not in filtered_args:
            filtered_args.extend(["--connection", connection])
        if interface_ip and "--interface-ip" not in filtered_args:
            filtered_args.extend(["--interface-ip", interface_ip])
        
    try:
        exo_node_cli(filtered_args)
    except SystemExit as e:
        sys.exit(e.code)

@click.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.pass_context
def ollama_cli(ctx):
    """Direct drop-in CLI replacement for ollama."""
    from exowindows.ollama_hook import run_wrapped_ollama
    run_wrapped_ollama(ctx.args)

if __name__ == "__main__":
    cli()
