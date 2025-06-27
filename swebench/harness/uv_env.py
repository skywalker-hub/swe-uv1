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

# --- 路径配置 (已根据您的要求修改) ---
# 1. 定义您的数据盘根目录
#    所有环境和缓存都将安装在此目录下
BASE_STORAGE_PATH = Path("/root/autodl-tmp")

# 2. 为不同类型的环境和缓存定义子目录，保持结构清晰
#    UV 环境将被安装在 /root/autodl-tmp/swe_uv_envs/
UV_ENVS_DIR = BASE_STORAGE_PATH / "swe_uv_envs"
#    Conda 环境将被安装在 /root/autodl-tmp/swe_conda_envs/
CONDA_ENVS_DIR = BASE_STORAGE_PATH / "swe_conda_envs"

#    原有的 CACHE_DIR 逻辑，现在也重定向到数据盘
CACHE_DIR = BASE_STORAGE_PATH / "swebench_cache"


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
        # 使用我们新配置的 CONDA_ENVS_DIR
        env_path = CONDA_ENVS_DIR / env_name
        print(f"✓ [CONDA] 策略启动，目标环境: {env_name} (位于: {env_path})")
        
        # 检查 Conda 环境是否已缓存
        if not env_path.exists():
            env_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"[CONDA] 缓存未命中，正在创建名为 '{env_name}' 的新 Conda 环境...")
            
            # 由于我们不再使用默认的 conda envs 路径，
            # 需要在 conda create 命令中用 --prefix 明确指定安装位置。
            # 这里我们假设 install_scripts 包含 'conda env create' 命令，需要对其进行修改。
            
            for cmd in install_scripts:
                # 智能地修改 conda 命令以使用 --prefix
                if "conda env create" in cmd or "conda create" in cmd:
                    # 移除可能存在的 -n 或 --name 参数，替换为 --prefix
                    cmd = re.sub(r'(-n|--name)\s+\S+', '', cmd)
                    # 确保命令中包含 --prefix
                    if "--prefix" not in cmd:
                        # 在 create 命令后插入 prefix 参数
                        cmd = cmd.replace("create", f"create --prefix {env_path}", 1)
                
                print(f"[CONDA] 执行命令: {cmd}")
                subprocess.run(cmd, shell=True, check=True, executable="/bin/bash")
        else:
            print(f"[CONDA] 发现缓存的 Conda 环境，直接使用: {env_path}")
    
    elif env_manager == "uv":
        # 使用我们新配置的 UV_ENVS_DIR
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
