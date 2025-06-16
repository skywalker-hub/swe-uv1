# uv Environment Setup Guide

SWE-bench now relies on [uv](https://github.com/astral-sh/uv) instead of Docker for managing isolated environments. Installing uv is simple:

```bash
pip install uv
```

During evaluation, the harness will automatically create and cache virtual environments under `~/.cache/swebench/envs/`. No additional setup is required.
