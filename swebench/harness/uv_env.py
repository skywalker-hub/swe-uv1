# 文件名: harness/uv_env.py (或 harness/env_creator.py)

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

# ======================================================================================
# START: 路径修改
# ======================================================================================

# 1. 定义您指定的数据盘路径作为所有环境的根目录
CUSTOM_ENVS_ROOT_DIR = Path("/root/autodl-tmp/swebench_envs")

# 2. 将 UV 和 Conda 的环境目录都设置在这个新路径下
UV_ENVS_DIR = CUSTOM_ENVS_ROOT_DIR / "uv_envs"
CONDA_ENVS_DIR = CUSTOM_ENVS_ROOT_DIR / "conda_envs"

# ======================================================================================
# END: 路径修改
# ======================================================================================


def _hash_scripts(scripts: list[str]) -> str:
    """根据脚本内容生成唯一的哈希键，用于缓存。"""
    m = hashlib.sha256()
    m.update("\n".join(scripts).encode())
    return m.hexdigest()[:22]


def _decide_python_version(scripts: list[str]) -> tuple[str, str]:
    """(此函数仅用于 UV 流程) 根据依赖决定创建 uv 虚拟环境时使用哪个 Python 解释器。"""
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
                        use_legacy_python = True; break
                except Exception: continue
        if use_legacy_python: break
            
    if use_legacy_python:
        print(f"[INFO] 检测到旧版依赖，UV将使用隔离环境 '{LEGACY_PYTHON_ENV_NAME}' 中的Python。")
        if not Path(LEGACY_PYTHON_PATH).exists(): raise RuntimeError(f"找不到隔离的Python解释器: {LEGACY_PYTHON_PATH}")
        return LEGACY_PYTHON_PATH, "python3.8"
    else:
        print(f"[INFO] 未检测到需要旧版Python的依赖，将使用主环境的Python ({MODERN_PYTHON_BIN})。")
        python_bin_path = shutil.which(MODERN_PYTHON_BIN);
        if not python_bin_path: raise RuntimeError(f"找不到主环境的Python: {MODERN_PYTHON_BIN}")
        return python_bin_path, MODERN_PYTHON_BIN


def create_env(
    install_scripts: list[str], 
    env_manager: str, 
    env_key: str | None = None
) -> Path:
    """
    根据指定的 env_manager (uv/conda) 创建并配置一个隔离的环境。
    """
    if env_key is None:
        env_key = _hash_scripts(install_scripts)

    if env_manager == "conda":
        env_name = env_key
        # Conda 环境的路径现在由我们自定义的 CONDA_ENVS_DIR 决定
        env_path = CONDA_ENVS_DIR / env_name
        print(f"✓ [CONDA] 策略启动，目标环境路径: {env_path}")
        
        if not env_path.exists():
            env_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"[CONDA] 缓存未命中，正在创建新的 Conda 环境于: {env_path}...")
            
            for cmd in install_scripts:
                print(f"[CONDA] 原始命令: {cmd}")
                # ==========================================================
                # START: Conda 命令修改
                # 将 --name {名称} 替换为 --prefix {完整路径}
                # ==========================================================
                modified_cmd = cmd.replace(f"--name {env_name}", f"--prefix {env_path}")
                print(f"[CONDA] 修改后命令: {modified_cmd}")
                # ==========================================================
                # END: Conda 命令修改
                # ==========================================================
                subprocess.run(modified_cmd, shell=True, check=True, executable="/bin/bash")
        else:
            print(f"[CONDA] 发现缓存的 Conda 环境，直接使用: {env_path}")
    
    elif env_manager == "uv":
        # UV 环境的路径现在也由我们自定义的 UV_ENVS_DIR 决定
        env_path = UV_ENVS_DIR / env_key
        print(f"✓ [UV] 策略启动，目标环境路径: {env_path}")

        if not env_path.exists():
            env_path.parent.mkdir(parents=True, exist_ok=True)
            python_bin_path, python_version_str = _decide_python_version(install_scripts)
            
            print(f"[{python_version_str.upper()}] 正在使用 {python_bin_path} 创建 UV 虚拟环境...")
            # uv venv 直接接受完整路径，所以这里无需修改命令
            subprocess.run(["uv", "venv", "--python", python_bin_path, str(env_path)], check=True)

            print(f"[{python_version_str.upper()}] 正在为新环境安装基础构建工具...")
            base_tools_env = {**os.environ, "VIRTUAL_ENV": str(env_path), "PATH": f"{env_path}/bin:{os.environ['PATH']}"}
            subprocess.run("uv pip install 'pip<24' 'setuptools<60' wheel", shell=True, check=True, executable="/bin/bash", env=base_tools_env)
            
            print(f"[UV] 正在安装依赖...")
            for cmd in install_scripts:
                print(f"[UV] 执行命令: {cmd}")
                dep_env = {**os.environ, "VIRTUAL_ENV": str(env_path), "PATH": f"{env_path}/bin:{os.environ['PATH']}"}
                subprocess.run(cmd, shell=True, check=True, executable="/bin/bash", env=dep_env)
        else:
            print(f"[UV] 发现缓存的 UV 环境，直接使用: {env_path}")
    
    else:
        raise ValueError(f"未知的环境管理器类型: '{env_manager}'。只接受 'uv' 或 'conda'。")

    # 激活环境
    os.environ["PATH"] = f"{env_path}/bin:{os.environ['PATH']}"
    print(f"✓ [INFO] 环境注入成功: 后续所有命令将优先使用 '{env_path}/bin' 中的工具。")
    
    return env_path