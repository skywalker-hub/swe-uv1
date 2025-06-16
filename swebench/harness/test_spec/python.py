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


def make_repo_script_list_py(
    specs, repo, repo_directory, base_commit, env_name
) -> list:
    """
    Create a list of bash commands to set up the repository for testing.
    This is the setup script for the instance image.
    """
    setup_commands = [
        f"git clone -o origin https://github.com/{repo} {repo_directory}",
        f"chmod -R 777 {repo_directory}",  # So nonroot user can run tests
        f"cd {repo_directory}",
        f"git reset --hard {base_commit}",
        # Remove the remote so the agent won't see newer commits.
        "git remote remove origin",
    ]
    if repo in MAP_REPO_TO_INSTALL:
        setup_commands.append(MAP_REPO_TO_INSTALL[repo])

    # Run pre-install set up if provided
    if "pre_install" in specs:
        for pre_install in specs["pre_install"]:
            setup_commands.append(pre_install)

    if "install" in specs:
        setup_commands.append(specs["install"])

    # If the setup modifies the repository in any way, it can be
    # difficult to get a clean diff.  This ensures that `git diff`
    # will only reflect the changes from the user while retaining the
    # original state of the repository plus setup commands.
    clean_diff_commands = [
        "git config --global user.email setup@swebench.config",
        "git config --global user.name SWE-bench",
        "git commit --allow-empty -am SWE-bench",
    ]

    setup_commands += clean_diff_commands

    return setup_commands

###主要修改部分
def make_env_script_list_py(instance, specs, env_name) -> list:
    """
    构建 uv 安装依赖的指令列表：
    1. 优先读取 specs["packages"]
    2. 若缺失，则默认尝试使用 requirements.txt
    3. 若为 environment.yml 则也支持
    """
    HEREDOC_DELIMITER = "EOF_59812759871"
    cmds = []

    # 兜底策略：如果 specs 中没有 packages 字段，默认设为 requirements.txt
    pkgs = specs.get("packages") or "requirements.txt"

    if pkgs == "requirements.txt":
        reqs = get_requirements(instance)
        path_to_reqs = "requirements.txt"
        cmds.append(
            f"cat <<'{HEREDOC_DELIMITER}' > {path_to_reqs}\n{reqs}\n{HEREDOC_DELIMITER}"
        )
        cmds.append(f"uv pip install -r {path_to_reqs}")
        cmds.append(f"rm {path_to_reqs}")

    elif pkgs == "environment.yml":
        reqs = get_environment_yml(instance, env_name)
        path_to_reqs = "environment.yml"
        cmds.append(
            f"cat <<'{HEREDOC_DELIMITER}' > {path_to_reqs}\n{reqs}\n{HEREDOC_DELIMITER}"
        )
        cmds.append(f"uv pip install -r {path_to_reqs}")
        cmds.append(f"rm {path_to_reqs}")

    elif pkgs:
        # 例如 pkgs="numpy pandas"
        cmds.append(f"uv pip install {pkgs}")

    # 额外 pip 包（一般用于补丁环境）
    if "pip_packages" in specs:
        pip_packages = " ".join(specs["pip_packages"])
        cmds.append(f"uv pip install {pip_packages}")

    return cmds



def make_eval_script_list_py(
    instance, specs, env_name, repo_directory, base_commit, test_patch
) -> list:
    """
    Applies the test patch and runs the tests.
    """
    HEREDOC_DELIMITER = "EOF_114329324912"
    test_files = get_modified_files(test_patch)
    # Reset test files to the state they should be in before the patch.
    reset_tests_command = f"git checkout {base_commit} {' '.join(test_files)}"
    apply_test_patch_command = (
        f"git apply -v - <<'{HEREDOC_DELIMITER}'\n{test_patch}\n{HEREDOC_DELIMITER}"
    )
    test_command = " ".join(
        [
            MAP_REPO_VERSION_TO_SPECS[instance["repo"]][instance["version"]][
                "test_cmd"
            ],
            *get_test_directives(instance),
        ]
    )
    eval_commands = [f"cd {repo_directory}"]
    if "eval_commands" in specs:
        eval_commands += specs["eval_commands"]
    eval_commands += [
        f"git config --global --add safe.directory {repo_directory}",  # for nonroot user
        f"cd {repo_directory}",
        # This is just informational, so we have a record
        "git status",
        "git show",
        f"git -c core.fileMode=false diff {base_commit}",
    ]
    if "install" in specs:
        eval_commands.append(specs["install"])
    eval_commands += [
        reset_tests_command,
        apply_test_patch_command,
        f": '{START_TEST_OUTPUT}'",
        test_command,
        f": '{END_TEST_OUTPUT}'",
        reset_tests_command,  # Revert tests after done, leave the repo in the same state as before
    ]
    return eval_commands
