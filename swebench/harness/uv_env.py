import hashlib
import subprocess
import shutil
from pathlib import Path
import os


CACHE_DIR = Path.home() / ".cache" / "swebench" / "envs"

def _hash_scripts(scripts: list[str]) -> str:
    m = hashlib.sha256()
    m.update("\n".join(scripts).encode())
    return m.hexdigest()[:22]

def get_env_path(env_key: str) -> Path:
    return CACHE_DIR / env_key

def create_env(scripts: list[str], env_key: str | None = None) -> Path:
    """Create a uv-managed environment and run install scripts using Python 3.10."""
    if env_key is None:
        env_key = _hash_scripts(scripts)
    env_path = get_env_path(env_key)

    if not env_path.exists():
        env_path.parent.mkdir(parents=True, exist_ok=True)

        # 明确指定使用 Python 3.10
        python_bin = shutil.which("python3.12")
        if not python_bin:
            raise RuntimeError("找不到 python3.12,请先安装后再运行 SWE-bench。")

        subprocess.run(["uv", "venv", "--python", python_bin, str(env_path)], check=True)

        for cmd in scripts:
            # 用 VIRTUAL_ENV + PATH 注入方式替代 source
            env = {
                **os.environ,
                "VIRTUAL_ENV": str(env_path),
                "PATH": f"{env_path}/bin:{os.environ['PATH']}"
            }
            subprocess.run(
                cmd,
                shell=True,
                check=True,
                executable="/bin/bash",
                env=env,
            )
    return env_path
