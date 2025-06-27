import os
import posixpath
import re
import requests

from swebench.harness.constants import (
    SWEbenchInstance,
    MAP_REPO_TO_ENV_YML_PATHS,
    MAP_REPO_TO_INSTALL,
    MAP_REPO_TO_REQS_PATHS,
    MAP_REPO_VERSION_TO_SPECS,
    NON_TEST_EXTS,
    SWE_BENCH_URL_RAW,
    START_TEST_OUTPUT,
    END_TEST_OUTPUT,
)
from swebench.harness.utils import get_modified_files
from functools import cache

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36"
}

REPLACE_REQ_PACKAGES = [
    # pkg-to-replace, replacement
    ("types-pkg_resources", "types-setuptools")
]


@cache
def get_environment_yml_by_commit(repo: str, commit: str, env_name: str) -> str:
    for req_path in MAP_REPO_TO_ENV_YML_PATHS[repo]:
        reqs_url = posixpath.join(SWE_BENCH_URL_RAW, repo, commit, req_path)
        reqs = requests.get(reqs_url, headers=HEADERS)
        if reqs.status_code == 200:
            break
    else:
        raise ValueError(
            f"Could not find environment.yml at paths {MAP_REPO_TO_ENV_YML_PATHS[repo]} for repo {repo} at commit {commit}"
        )

    lines = reqs.text.split("\n")
    cleaned = []
    for line in lines:
        # Rename environment to given name
        if line.startswith("name:"):
            cleaned.append(f"name: {env_name}")
            continue
        cleaned.append(line)

    return "\n".join(cleaned)


def clean_environment_yml(yml_text: str) -> str:
    """
    Clean environment.yml by removing packages that have been yanked from PyPI

    conda style yamls take the form:
    ...
    - channels:
        ...
    - dependencies:
        ...
    - pip:
        - pkg_to_replace
        - pkg_to_replace
    - ... (more dependencies)

    We want to replace packages in the pip section only.
    """
    pip_match = re.search(r"^(\s*-\s*pip\s*:\s*\n)", yml_text, flags=re.MULTILINE)
    if not pip_match:
        return yml_text
    pip_line_start = pip_match.start()
    # get indentation level of pip line
    pip_indent = len(pip_match.group(1)) - len(pip_match.group(1).lstrip())
    pip_content_start = pip_match.end()
    # find where pip section ends by looking for a line that's at same or less indentation
    # or a line that starts a new top-level dependency (not pip)
    lines_after_pip = yml_text[pip_content_start:].split("\n")
    pip_section_end = pip_content_start
    for ix, line in enumerate(lines_after_pip):
        if line.strip() == "":
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent <= pip_indent:
            # +1 to account for the newline
            pip_section_end = pip_content_start + sum(
                len(l) + 1 for l in lines_after_pip[:ix]
            )
            break
    else:
        pip_section_end = len(yml_text)
    prefix = yml_text[:pip_content_start]
    pip_portion = yml_text[pip_content_start:pip_section_end]
    suffix = yml_text[pip_section_end:]
    for pkg_to_replace, replacement in REPLACE_REQ_PACKAGES:
        if replacement == None:
            pip_portion = re.sub(
                rf"^(\s*-\s*){re.escape(pkg_to_replace)}(?=[=\s]|$).*\n?",
                "",
                pip_portion,
                flags=re.MULTILINE,
            )
        else:
            pip_portion = re.sub(
                rf"^(\s*-\s*){re.escape(pkg_to_replace)}(?=[=\s]|$)",
                rf"\1{replacement}",
                pip_portion,
                flags=re.MULTILINE,
            )
    return prefix + pip_portion + suffix


def get_environment_yml(instance: SWEbenchInstance, env_name: str) -> str:
    """
    Get environment.yml for given task instance

    Args:
        instance (dict): SWE Bench Task instance
        env_name (str): Rename retrieved environment.yml to this name
    Returns:
        environment.yml (str): Returns environment.yml as string
    """
    # Attempt to find environment.yml at each path based on task instance's repo
    commit = (
        instance["environment_setup_commit"]
        if "environment_setup_commit" in instance
        else instance["base_commit"]
    )
    yml_text = get_environment_yml_by_commit(instance["repo"], commit, env_name)
    yml_text = clean_environment_yml(yml_text)
    return yml_text


@cache
def get_requirements_by_commit(repo: str, commit: str) -> str:
    for req_path in MAP_REPO_TO_REQS_PATHS[repo]:
        reqs_url = posixpath.join(SWE_BENCH_URL_RAW, repo, commit, req_path)
        reqs = requests.get(reqs_url, headers=HEADERS)
        if reqs.status_code == 200:
            break
    else:
        raise ValueError(
            f"Could not find requirements.txt at paths {MAP_REPO_TO_REQS_PATHS[repo]} for repo {repo} at commit {commit}"
        )

    lines = reqs.text
    original_req = []
    additional_reqs = []
    req_dir = "/".join(req_path.split("/")[:-1])
    exclude_line = lambda line: any(
        [line.strip().startswith(x) for x in ["-e .", "#", ".[test"]]
    )

    for line in lines.split("\n"):
        if line.strip().startswith("-r"):
            # Handle recursive requirements
            file_name = line[len("-r") :].strip()
            reqs_url = os.path.join(
                SWE_BENCH_URL_RAW,
                repo,
                commit,
                req_dir,
                file_name,
            )
            reqs = requests.get(reqs_url, headers=HEADERS)
            if reqs.status_code == 200:
                for line_extra in reqs.text.split("\n"):
                    if not exclude_line(line_extra):
                        additional_reqs.append(line_extra)
        else:
            if not exclude_line(line):
                original_req.append(line)

    # Combine all requirements into single text body
    additional_reqs.append("\n".join(original_req))
    all_reqs = "\n".join(additional_reqs)

    return all_reqs


def clean_requirements(requirements_text: str) -> str:
    """
    Clean requirements.txt by replacing / removing packages

    E.g. types-pkg_resources has been yanked from PyPI, so we replace it with types-setuptools
    """
    for pkg_to_replace, replacement in REPLACE_REQ_PACKAGES:
        if replacement == None:
            requirements_text = re.sub(
                rf"^{re.escape(pkg_to_replace)}(?=[<>=!~\s]|$)",
                "",
                requirements_text,
                flags=re.MULTILINE,
            )
        else:
            # this replacement removes version specifier of the original package
            requirements_text = re.sub(
                rf"^{re.escape(pkg_to_replace)}(?=[<>=!~\s]|$)",
                replacement,
                requirements_text,
                flags=re.MULTILINE,
            )
    return requirements_text


def get_requirements(instance: SWEbenchInstance) -> str:
    """
    Get requirements.txt for given task instance

    Args:
        instance (dict): task instance
    Returns:
        requirements.txt (str): Returns requirements.txt as string
    """
    # Attempt to find requirements.txt at each path based on task instance's repo
    commit = (
        instance["environment_setup_commit"]
        if "environment_setup_commit" in instance
        else instance["base_commit"]
    )

    requirements_text = get_requirements_by_commit(instance["repo"], commit)
    requirements_text = clean_requirements(requirements_text)
    return requirements_text


def get_test_directives(instance: SWEbenchInstance) -> list:
    """
    Get test directives from the test_patch of a task instance

    Args:
        instance (dict): task instance
    Returns:
        directives (list): List of test directives
    """
    # For seq2seq code repos, testing command is fixed
    if instance["repo"] == "swe-bench/humaneval":
        return ["test.py"]

    # Get test directives from test patch and remove non-test files
    diff_pat = r"diff --git a/.* b/(.*)"
    test_patch = instance["test_patch"]
    directives = re.findall(diff_pat, test_patch)
    directives = [
        d for d in directives if not any(d.endswith(ext) for ext in NON_TEST_EXTS)
    ]

    # For Django tests, remove extension + "tests/" prefix and convert slashes to dots (module referencing)
    if instance["repo"] == "django/django":
        directives_transformed = []
        for d in directives:
            d = d[: -len(".py")] if d.endswith(".py") else d
            d = d[len("tests/") :] if d.startswith("tests/") else d
            d = d.replace("/", ".")
            directives_transformed.append(d)
        directives = directives_transformed

    return directives




# ======================================================================================
#                     生成 Python 项目的代码仓库设置脚本
#
# 该函数负责创建一系列的 shell 命令，用于完整地、可复现地建立一个待测试的
# Python 代码仓库环境。它包括了从克隆、版本重置到执行特定安装指令的全过程。
# ======================================================================================
def make_repo_script_list_py(
    specs, repo, repo_directory, base_commit, env_name
) -> list:
    """
    Create a list of bash commands to set up the repository for testing.
    This is the setup script for the instance image.
    """
    # 定义基础的仓库设置命令列表。
    setup_commands = [
        # 1. 从 GitHub 克隆指定的代码仓库。
        f"git clone -o origin https://github.com/{repo} {repo_directory}",
        # 2. 修改权限，以确保非 root 用户也能在容器内执行操作。
        f"chmod -R 777 {repo_directory}",
        # 3. 进入仓库目录。
        f"cd {repo_directory}",
        # 4. 将代码重置到指定的 `base_commit`，确保每次测试的起点都完全一致。
        f"git reset --hard {base_commit}",
        # 5. 移除远程源，以防止后续操作与远程仓库发生意外交互，增强环境的隔离性。
        "git remote remove origin",
    ]
    
    # 如果常量映射中存在针对该仓库的特定安装命令，则添加它。
    if repo in MAP_REPO_TO_INSTALL:
        setup_commands.append(MAP_REPO_TO_INSTALL[repo])

    # 如果测试规范（specs）中定义了“预安装”指令，则将其添加到命令列表。
    if "pre_install" in specs:
        for pre_install in specs["pre_install"]:
            setup_commands.append(pre_install)

    # 添加规范中定义的主要安装指令。
    if "install" in specs:
        setup_commands.append(specs["install"])

    # 这是一个非常巧妙的“干净 diff”技巧。
    # 背景：前面的安装和设置步骤可能会修改仓库中的文件（例如，生成配置文件）。
    # 目的：为了在后续评估模型补丁时，`git diff` 只显示模型自身的改动，而不是这些设置步骤引入的“噪音”。
    # 方法：在所有设置操作完成后，创建一个空的 commit。这将建立一个新的、干净的基线。
    #      后续应用模型补丁后，与这个新基线进行比较，就能得到纯净的、仅由模型产生的 diff。
    clean_diff_commands = [
        "git config --global user.email setup@swebench.config",
        "git config --global user.name SWE-bench",
        "git commit --allow-empty -am SWE-bench",
    ]

    # 将 "干净 diff" 的相关命令追加到总的命令列表中。
    setup_commands += clean_diff_commands

    # 返回最终构建好的所有设置命令。
    return setup_commands





###环境构建命令
# ======================================================================================
#                   生成 Python 项目的环境依赖安装脚本
#
# 该函数负责创建一系列的 shell 命令，用于安装一个测试实例所需的所有 Python
# 依赖。它能够智能地处理多种依赖格式（如 requirements.txt, environment.yml）
# 并使用现代化的 `uv` 工具进行高效安装。
# ======================================================================================
# In harness/test_spec/python.py

# (请确保此文件顶部有 import yaml, 以及 get_requirements, get_environment_yml 等辅助函数)

# ======================================================================================
#                       最终版 make_env_script_list_py 函数
# ======================================================================================
def make_env_script_list_py(instance, specs, env_name) -> tuple[list[str], str]:
    """
    生成一个统一的、用于安装所有Python依赖的脚本命令列表，并返回所使用的环境管理器类型。
    """
    HEREDOC_DELIMITER = "EOF_59812759871"
    
    # 关键修复1: 将 UV 安装参数的定义移到函数顶部，确保它们在所有逻辑分支中都可用。
    # 这将彻底解决 UnboundLocalError 的问题。
    PYPI_MIRROR = "--index-url https://pypi.tuna.tsinghua.edu.cn/simple"
    TRUSTED_HOST = "--trusted-host pypi.tuna.tsinghua.edu.cn"
    UV_INSTALL_ARGS = f"{PYPI_MIRROR} {TRUSTED_HOST}"

    # 从 specs 配置中获取依赖类型
    pkgs_type = specs.get("packages", "requirements.txt")
    cmds = []
    
    # 核心逻辑：根据依赖类型，选择正确的工具并生成相应指令
    if pkgs_type == "environment.yml":
        # 策略一：当依赖文件是 environment.yml 时，使用 Conda
        env_manager = "conda"
        print(f"✓ 检测到 {pkgs_type}，切换到 {env_manager.upper()} 策略。")
        
        yml_content = get_environment_yml(instance, env_name)
        path_to_yml = "environment.yml"
        
        cmds.extend([
            # 在执行任何 conda 网络操作之前，先设置 ssl_verify 为 false
            "conda config --set ssl_verify false",
            # 使用 Heredoc 将 yml 内容写入容器内的文件
            f"cat <<'{HEREDOC_DELIMITER}' > {path_to_yml}\n{yml_content}\n{HEREDOC_DELIMITER}",
            # 生成 Conda 命令来创建新环境
            f"conda env create --file {path_to_yml} --name {env_name} --force",
            # 清理临时文件
            f"rm {path_to_yml}"
        ])

    else:
        # 策略二：对于 requirements.txt 或直接的包名，使用我们优化好的 uv 流程
        env_manager = "uv"
        print(f"✓ 依赖类型: {pkgs_type}，使用 {env_manager.upper()} 策略。")
        
        # 定义需要预安装的系统C库
        system_packages = "pkg-config libcairo2-dev libgirepository1.0-dev"
        cmds.extend([
            "apt-get update",
            f"apt-get install -y {system_packages}",
            "ldconfig"
        ])
        
        # 定义强制注入的环境变量，解决 pkg-config 路径问题
        ENV_PREFIX = "env PKG_CONFIG_PATH=/usr/lib/x86_64-linux-gnu/pkgconfig"
        
        # 关键修复2: 使用精简的逻辑直接处理依赖，不再需要任何复杂的辅助函数。
        reqs_text = get_requirements(instance) if pkgs_type == "requirements.txt" else pkgs_type.replace(' ', '\n')
        
        if reqs_text and reqs_text.strip():
            path_to_reqs = "requirements.txt"
            cmds.extend([
                f"cat <<'{HEREDOC_DELIMITER}' > {path_to_reqs}\n{reqs_text}\n{HEREDOC_DELIMITER}",
                f"{ENV_PREFIX} uv pip install -r {path_to_reqs} {UV_INSTALL_ARGS}",
                f"rm {path_to_reqs}"
            ])

    # 额外的 pip 包安装。这段代码现在可以安全地使用在函数顶部定义的 UV_INSTALL_ARGS 了。
    if "pip_packages" in specs:
        pip_packages = " ".join(specs["pip_packages"])
        cmds.append(f"uv pip install {pip_packages} {UV_INSTALL_ARGS}")
        
    return cmds, env_manager




###评估命令
# ======================================================================================
#                     生成 Python 项目的核心评估与测试脚本
#
# 该函数负责创建评测流程中最核心的指令列表。它精确地编排了应用补丁、
# 运行测试、记录日志以及事后清理的全过程，以确保评测的准确性和可复现性。
# ======================================================================================
def make_eval_script_list_py(
    instance, specs, env_name, repo_directory, base_commit, test_patch
) -> list:
    """
    Applies the test patch and runs the tests.
    """
    # 定义一个唯一的字符串作为"Here Document"的分隔符，用于安全地将多行补丁内容传递给git命令。
    HEREDOC_DELIMITER = "EOF_114329324912"
    # 从测试补丁中获取所有被修改的测试文件名。
    test_files = get_modified_files(test_patch)
    
    # 定义一个命令，用于将测试文件恢复到`base_commit`时的状态。这确保了应用补丁前文件的清洁和一致。
    reset_tests_command = f"git checkout {base_commit} {' '.join(test_files)}"
    
    # 定义应用测试补丁的命令。它使用"Here Document"语法将`test_patch`变量的内容通过标准输入传递给`git apply`。
    apply_test_patch_command = (
        f"git apply -v - <<'{HEREDOC_DELIMITER}'\n{test_patch}\n{HEREDOC_DELIMITER}"
    )
    
    # 构造完整的测试命令。它由基础测试命令（如 'pytest'）和从补丁中提取出的具体测试目标（文件或模块）组成。
    test_command = " ".join(
        [
            MAP_REPO_VERSION_TO_SPECS[instance["repo"]][instance["version"]][
                "test_cmd"
            ],
            *get_test_directives(instance),
        ]
    )
    
    # 初始化评估命令列表，首先进入代码仓库目录。
    eval_commands = [f"cd {repo_directory}"]
    
    # 如果规范中有额外的评估前置命令，则添加它们。
    if "eval_commands" in specs:
        eval_commands += specs["eval_commands"]
        
    # 添加一系列用于记录和调试的git命令，它们会在日志中显示仓库在测试前的状态。
    eval_commands += [
        f"git config --global --add safe.directory {repo_directory}",  # 确保非root用户也能执行git命令
        f"cd {repo_directory}",
        # 以下命令仅用于信息记录。
        "git status",
        "git show",
        f"git -c core.fileMode=false diff {base_commit}",
    ]
    
    # 如果有安装步骤，在此处也执行一次，以确保评估环境是完全配置好的。
    if "install" in specs:
        eval_commands.append(specs["install"])
        
    # 按顺序组合核心的评估流程命令。
    eval_commands += [
        # 1. 重置测试文件，确保环境干净。
        reset_tests_command,
        # 2. 应用测试补丁，这个补丁通常包含了运行测试所需的断言或修改。
        apply_test_patch_command,
        # 3. 打印一个开始标记，这对于后续从日志中精确提取测试输出至关重要。
        f": '{START_TEST_OUTPUT}'",
        # 4. 执行真正的测试命令。
        test_command,
        # 5. 打印一个结束标记，标志着测试输出的结束。
        f": '{END_TEST_OUTPUT}'",
        # 6. 测试完成后，再次重置测试文件，将仓库恢复到测试前的状态，以避免对后续步骤产生副作用。
        reset_tests_command,
    ]
    
    # 返回最终构建好的所有评估命令。
    return eval_commands