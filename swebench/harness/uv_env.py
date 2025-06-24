import hashlib
import subprocess
import shutil
from pathlib import Path
import os
import re
from packaging.version import parse

# --- 新增配置 ---
# 定义新旧Python版本及其查找路径
LEGACY_PYTHON_BIN = "python3.8"
MODERN_PYTHON_BIN = "python3.10"

# 定义触发旧版Python的包和版本阈值
# 如果numpy版本低于1.22，就很有可能在Python 3.10上编译失败
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
    # 正则表达式，用于从 "package==version", "package<version", "package>=version" 中提取包名和版本号
    # 例如: numpy==1.17.3, pandas<2.0.0
    pattern = re.compile(r"([a-zA-Z0-9_.-]+)\s*([<>=!~]+)\s*([0-9\.]+)")
    
    for command in scripts:
        matches = pattern.findall(command)
        for package, comparator, version_str in matches:
            # 检查这个包是否在我们的“旧版触发器”列表中
            if package in LEGACY_TRIGGERS:
                try:
                    required_version = parse(version_str)
                    threshold_version = LEGACY_TRIGGERS[package]
                    
                    # 如果要求的版本低于我们的阈值，就判定为需要旧版Python
                    if required_version < threshold_version:
                        print(
                            f"[INFO] 检测到旧版依赖: '{package}{comparator}{version_str}'. "
                            f"将使用旧版Python ({LEGACY_PYTHON_BIN}) 创建环境。"
                        )
                        python_bin_path = shutil.which(LEGACY_PYTHON_BIN)
                        if not python_bin_path:
                             raise RuntimeError(f"找不到 {LEGACY_PYTHON_BIN}, 但项目需要它。请先安装后再运行。")
                        return python_bin_path, LEGACY_PYTHON_BIN
                except Exception:
                    # 如果版本号解析失败，就跳过这个包
                    continue

    # 如果遍历完所有依赖都没有触发旧版规则，则使用现代版Python
    print(f"[INFO] 未检测到旧版依赖。将使用现代版Python ({MODERN_PYTHON_BIN}) 创建环境。")
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

        # 动态决定使用哪个Python可执行文件
        python_bin, _ = _decide_python_version(scripts)

        print(f"[INFO] 正在使用 {python_bin} 创建虚拟环境于: {env_path}")
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
    else:
        print(f"[INFO] 发现缓存的环境，直接使用: {env_path}")
        
    return env_path