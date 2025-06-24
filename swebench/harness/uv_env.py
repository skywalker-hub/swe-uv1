import hashlib
import subprocess
import shutil
from pathlib import Path
import os
import re
from packaging.version import parse

# --- 配置 ---
LEGACY_PYTHON_BIN = "python3.8"
MODERN_PYTHON_BIN = "python3.10"
LEGACY_TRIGGERS = {
    "numpy": parse("1.22.0"),
    "scipy": parse("1.7.0"),
}
# --- 配置结束 ---

CACHE_DIR = Path.home() / ".cache" / "swebench" / "envs"

def _hash_scripts(scripts: list[str]) -> str:
    m = hashlib.sha256()
    m.update("\n".join(scripts).encode())
    return m.hexdigest()[:22]

def get_env_path(env_key: str) -> Path:
    return CACHE_DIR / env_key

def _decide_python_version(scripts: list[str]) -> (str, str):
    """
    根据安装脚本中的依赖版本，动态决定使用哪个Python版本。
    返回 (python_executable_path, python_version_string)
    """
    use_legacy_python = False  # 初始化决策标志位
    pattern = re.compile(r"([a-zA-Z0-9_.-]+)\s*([<>=!~]+)\s*([0-9\.]+)")
    
    # 第一步：遍历所有脚本，检查是否需要使用旧版Python
    for command in scripts:
        matches = pattern.findall(command)
        for package, comparator, version_str in matches:
            if package in LEGACY_TRIGGERS:
                try:
                    required_version = parse(version_str)
                    threshold_version = LEGACY_TRIGGERS[package]
                    if required_version < threshold_version:
                        print(f"[INFO] 检测到旧版依赖: '{package}{comparator}{version_str}'")
                        use_legacy_python = True  # 一旦发现，就将标志位置为True
                except Exception:
                    continue
    
    # 第二步：在检查完所有脚本后，根据标志位做出最终决定
    if use_legacy_python:
        print(f"[INFO] 最终决定: 由于检测到旧版依赖，将使用 {LEGACY_PYTHON_BIN} 创建环境。")
        python_bin_path = shutil.which(LEGACY_PYTHON_BIN)
        if not python_bin_path:
            raise RuntimeError(f"找不到 {LEGACY_PYTHON_BIN}, 但项目需要它。请先安装后再运行。")
        return python_bin_path, LEGACY_PYTHON_BIN
    else:
        print(f"[INFO] 最终决定: 未检测到需要旧版Python的依赖，将使用 {MODERN_PYTHON_BIN} 创建环境。")
        python_bin_path = shutil.which(MODERN_PYTHON_BIN)
        if not python_bin_path:
            raise RuntimeError(f"找不到默认的Python版本 {MODERN_PYTHON_BIN}。请安装后再运行。")
        return python_bin_path, MODERN_PYTHON_BIN


def create_env(scripts: list[str], env_key: str | None = None) -> Path:
    """根据依赖动态选择Python版本，创建uv管理的环境并运行安装脚本。"""
    if env_key is None:
        env_key = _hash_scripts(scripts)
    env_path = get_env_path(env_key)

    if not env_path.exists():
        env_path.parent.mkdir(parents=True, exist_ok=True)
        python_bin, _ = _decide_python_version(scripts)
        print(f"[INFO] 正在使用 {python_bin} 创建虚拟环境于: {env_path}")
        subprocess.run(["uv", "venv", "--python", python_bin, str(env_path)], check=True)
        for cmd in scripts:
            env = {
                **os.environ,
                "VIRTUAL_ENV": str(env_path),
                "PATH": f"{env_path}/bin:{os.environ['PATH']}"
            }
            subprocess.run(
                cmd, shell=True, check=True, executable="/bin/bash", env=env
            )
    else:
        print(f"[INFO] 发现缓存的环境，直接使用: {env_path}")
    return env_path