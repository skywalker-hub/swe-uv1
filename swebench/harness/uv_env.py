import hashlib
import subprocess
import shutil
from pathlib import Path
import os
import re
from packaging.version import parse

# --- 最终配置 ---
# 1. 为旧版依赖专门创建的、隔离的conda环境中的Python解释器
#    它的名字叫 py38，它的Python解释器路径是固定的。
LEGACY_PYTHON_ENV_NAME = "py38"
LEGACY_PYTHON_PATH = f"/root/miniconda3/envs/{LEGACY_PYTHON_ENV_NAME}/bin/python"

# 2. 用来运行swebench主程序的、现代的Python版本
MODERN_PYTHON_BIN = "python3.10"

# 3. 触发使用旧版Python的依赖库及其版本阈值
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
    use_legacy_python = False
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
                        use_legacy_python = True
                except Exception:
                    continue
    
    # 第二步：在检查完所有脚本后，根据标志位做出最终决定
    if use_legacy_python:
        print(f"[INFO] 最终决定: 由于检测到旧版依赖，将使用隔离环境 '{LEGACY_PYTHON_ENV_NAME}' 中的Python。")
        # 直接检查我们指定的隔离环境中的python是否存在，这比搜索更可靠
        if not Path(LEGACY_PYTHON_PATH).exists():
            raise RuntimeError(
                f"在路径 {LEGACY_PYTHON_PATH} 找不到隔离的Python解释器。\n"
                f"请先运行 'conda create -n {LEGACY_PYTHON_ENV_NAME} python=3.8 -y' 来创建它。"
            )
        # 返回隔离环境的Python路径，和它的版本名（用于显示）
        return LEGACY_PYTHON_PATH, "python3.8"
    else:
        print(f"[INFO] 最终决定: 未检测到需要旧版Python的依赖，将使用主环境的Python ({MODERN_PYTHON_BIN})。")
        python_bin_path = shutil.which(MODERN_PYTHON_BIN)
        if not python_bin_path:
            raise RuntimeError(f"在主环境中找不到默认的Python版本 {MODERN_PYTHON_BIN}。")
        return python_bin_path, MODERN_PYTHON_BIN


def create_env(scripts: list[str], env_key: str | None = None) -> Path:
    """根据依赖动态选择Python版本，创建uv管理的环境并运行安装脚本。"""
    if env_key is None:
        env_key = _hash_scripts(scripts)
    env_path = get_env_path(env_key)

    if not env_path.exists():
        env_path.parent.mkdir(parents=True, exist_ok=True)
        python_bin_path, python_version_str = _decide_python_version(scripts)
        print(f"[{python_version_str.upper()}] 正在使用 {python_bin_path} 创建虚拟环境于: {env_path}")
        subprocess.run(["uv", "venv", "--python", python_bin_path, str(env_path)], check=True)
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