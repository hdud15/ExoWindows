# exowindows

Fast, distributed ML scaling on Windows. Seamlessly connect machines via Ethernet, USB-C, or Thunderbolt, and run distributed PyTorch training/inference and Ollama LLMs with automatic hardware detection, heterogeneous partitioning, and speedup alerts.

## Installation

```bash
pip install exowindows
```

## Features

- **Distributed Compute & RAM speed filtering**: Automatically runs across local and remote GPUs, CPU, and RAM, with a 3200MHz RAM speed constraint to filter out slow devices.
- **Ollama CLI Integration**: Run `exowindows ollama run llama3.1` to scan for connected worker machines, see performance suggestions, and run distributed inference.
- **PyTorch Hook**: Integrates with PyTorch training to auto-detect optimal cluster nodes and show speedup recommendations.
- **Easy Worker Join**: Run `exowindows-node join` to connect workers.
