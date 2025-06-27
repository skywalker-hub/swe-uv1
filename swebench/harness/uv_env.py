# 建议将此文件名修改为 env_creator.py
import hashlib
import subprocess
import shutil
from pathlib import Path
import os
import re
from packaging.version import parse

# --- 配置 ---
# 旧版Python兼容性逻辑，主要用于 UV 流程
LEGACY_PYTHON_ENV_NAME = "py38"
LEGACY_PYTHON_PATH = f"/root/miniconda3/envs/{LEGACY_PYTHON_ENV_NAME}/bin/python"
MODERN_PYTHON_BIN = "python3.10"
LEGACY_TRIGGERS = {
    "numpy": parse("1.22.0"),
    "scipy": parse("1.7.0"),
}

# --- 路径配置 (修改后) ---
# 将不同类型的环境分目录管理，更加清晰
CACHE_DIR = Path.home() / ".cache" / "swebench"
UV_ENVS_DIR = CACHE_DIR / "uv_envs"
# Conda 环境的默认安装路径
CONDA_ENVS_DIR = Path(os.environ.get("CONDA_PREFIX", "/root/miniconda3")) / "envs"


def _hash_scripts(scripts: list[str]) -> str:
    """根据脚本内容生成唯一的哈希键，用于缓存。"""
    m = hashlib.sha256()
    m.update("\n".join(scripts).encode())
    return m.hexdigest()[:22]


def _decide_python_version(scripts: list[str]) -> tuple[str, str]:
    """
    (此函数仅用于 UV 流程)
    根据依赖决定创建 uv 虚拟环境时使用哪个 Python 解释器。
    """
    use_legacy_python = False
    pattern = re.compile(r"([a-zA-Z0-9_.-]+)\s*([<>=!~]+)\s*([0-9\.]+)")
    for command in scripts:
        matches = pattern.findall(command)
        for package, comparator, version_str in matches:
            if package in LEGACY_TRIGGERS:
                try:
                    required_version = parse(version_str)
                    threshold_version = LEGACY_TRIGGERS[package]
                    if required_version < threshold_version:
                        use_legacy_python = True
                        break
                except Exception:
                    continue
        if use_legacy_python:
            break
            
    if use_legacy_python:
        print(f"[INFO] 检测到旧版依赖，UV将使用隔离环境 '{LEGACY_PYTHON_ENV_NAME}' 中的Python。")
        if not Path(LEGACY_PYTHON_PATH).exists():
            raise RuntimeError(f"找不到隔离的Python解释器: {LEGACY_PYTHON_PATH}")
        return LEGACY_PYTHON_PATH, "python3.8"
    else:
        print(f"[INFO] 未检测到需要旧版Python的依赖，UV将使用主环境的Python ({MODERN_PYTHON_BIN})。")
        python_bin_path = shutil.which(MODERN_PYTHON_BIN)
        if not python_bin_path:
            raise RuntimeError(f"找不到主环境的Python: {MODERN_PYTHON_BIN}")
        return python_bin_path, MODERN_PYTHON_BIN


# ======================================================================================
#                       核心：重构后的 create_env 函数
# ======================================================================================
def create_env(
    install_scripts: list[str], 
    env_manager: str, # 新增参数，接收 'uv' 或 'conda'
    env_key: str | None = None
) -> Path:
    """
    根据指定的 env_manager (uv/conda) 创建并配置一个隔离的环境。
    """
    if env_key is None:
        env_key = _hash_scripts(install_scripts)

    # 核心分支逻辑：根据 env_manager 选择不同的路径和创建方法
    if env_manager == "conda":
        env_name = env_key  # 对于 conda，我们用 key 作为环境名
        env_path = CONDA_ENVS_DIR / env_name
        print(f"✓ [CONDA] 策略启动，目标环境: {env_name}")
        
        # 检查 Conda 环境是否已缓存
        if not env_path.exists():
            env_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"[CONDA] 缓存未命中，正在创建名为 '{env_name}' 的新 Conda 环境...")
            
            # 直接执行由 make_env_script_list_py 生成的 conda 命令
            # 这些命令通常是 'conda env create -f ...'
            for cmd in install_scripts:
                print(f"[CONDA] 执行命令: {cmd}")
                subprocess.run(cmd, shell=True, check=True, executable="/bin/bash")
        else:
            print(f"[CONDA] 发现缓存的 Conda 环境，直接使用: {env_name}")
    
    elif env_manager == "uv":
        env_path = UV_ENVS_DIR / env_key
        print(f"✓ [UV] 策略启动，目标环境路径: {env_path}")

        # 检查 UV 环境是否已缓存
        if not env_path.exists():
            env_path.parent.mkdir(parents=True, exist_ok=True)
            python_bin_path, python_version_str = _decide_python_version(install_scripts)
            
            print(f"[{python_version_str.upper()}] 正在使用 {python_bin_path} 创建 UV 虚拟环境...")
            subprocess.run(["uv", "venv", "--python", python_bin_path, str(env_path)], check=True)

            # 为旧版Python兼容性安装基础构建工具 (原逻辑保留)
            print(f"[{python_version_str.upper()}] 正在为新环境安装基础构建工具...")
            base_tools_env = {**os.environ, "VIRTUAL_ENV": str(env_path), "PATH": f"{env_path}/bin:{os.environ['PATH']}"}
            subprocess.run("uv pip install 'pip<24' 'setuptools<60' wheel", shell=True, check=True, executable="/bin/bash", env=base_tools_env)
            
            print(f"[UV] 正在安装依赖...")
            # 执行由 make_env_script_list_py 生成的 uv 命令
            for cmd in install_scripts:
                print(f"[UV] 执行命令: {cmd}")
                dep_env = {**os.environ, "VIRTUAL_ENV": str(env_path), "PATH": f"{env_path}/bin:{os.environ['PATH']}"}
                subprocess.run(cmd, shell=True, check=True, executable="/bin/bash", env=dep_env)
        else:
            print(f"[UV] 发现缓存的 UV 环境，直接使用: {env_path}")
    
    else:
        raise ValueError(f"未知的环境管理器类型: '{env_manager}'。只接受 'uv' 或 'conda'。")

    # 激活环境：将新创建的环境的 bin 目录添加到 PATH 的最前面
    # 这样后续的命令（如 'python', 'pytest'）就会优先使用这个环境里的可执行文件
    os.environ["PATH"] = f"{env_path}/bin:{os.environ['PATH']}"
    print(f"✓ [INFO] 环境注入成功: 后续所有命令将优先使用 '{env_path}/bin' 中的工具。")
    
    return env_path