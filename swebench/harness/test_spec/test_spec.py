import hashlib
import json
import platform
from dataclasses import dataclass
from typing import Any, Union, cast

from swebench.harness.constants import (
    KEY_INSTANCE_ID,
    LATEST,
    MAP_REPO_TO_EXT,
    MAP_REPO_VERSION_TO_SPECS,
    USE_X86,
    SWEbenchInstance,
)
from swebench.harness.test_spec.create_scripts import (
    make_repo_script_list,
    make_env_script_list,
    make_eval_script_list,
)


# TestSpec 是一个数据类，用作结构化容器，封装运行单个测试实例所需的所有信息。
@dataclass
class TestSpec:
    instance_id: str
    repo: str
    version: str
    repo_script_list: list[str]
    eval_script_list: list[str]
    env_script_list: list[str]
    env_key: str
    # ===================================================================
    # START: 修改部分 1
    # 新增此字段，用于携带环境管理器的类型信号 ('uv' 或 'conda')
    env_manager: str
    # ===================================================================
    # END: 修改部分 1
    arch: str
    FAIL_TO_PASS: list[str]
    PASS_TO_PASS: list[str]
    language: str

    # 以下 @property 方法将命令列表动态转换为可执行的 shell 脚本字符串。
    # "set -euxo pipefail" 用于确保脚本在出错时立即退出，增强健壮性。
    @property
    def setup_env_script(self) -> str:
        return "\n".join(["#!/bin/bash", "set -euxo pipefail"] + self.env_script_list) + "\n"

    @property
    def eval_script(self) -> str:
        return "\n".join(["#!/bin/bash", "set -uxo pipefail"] + self.eval_script_list) + "\n"

    @property
    def install_repo_script(self) -> str:
        return "\n".join(["#!/bin/bash", "set -euxo pipefail"] + self.repo_script_list) + "\n"


# 这是一个工具函数，确保无论输入是原始数据还是已处理过的TestSpec，输出始终是TestSpec列表。
def get_test_specs_from_dataset(
    dataset: Union[list[SWEbenchInstance], list[TestSpec]],
) -> list[TestSpec]:
    # 如果数据集已经是 TestSpec 列表，直接返回。
    if isinstance(dataset[0], TestSpec):
        return cast(list[TestSpec], dataset)
    # 否则，对数据集中的每个原始实例调用 make_test_spec 进行转换。
    return list(map(make_test_spec, cast(list[SWEbenchInstance], dataset)))


# 核心函数，负责将一个原始的 SWEbenchInstance（通常是字典）转换为结构化的 TestSpec 对象。
def make_test_spec(instance: SWEbenchInstance) -> TestSpec:
    if isinstance(instance, TestSpec):
        return instance
    # 从原始实例中提取元数据。
    instance_id = instance[KEY_INSTANCE_ID]
    repo = instance["repo"]
    version = instance.get("version")
    base_commit = instance["base_commit"]
    test_patch = instance["test_patch"]

    # 定义一个内部辅助函数，用于处理可能是JSON字符串或已解析对象的字段。
    def _from_json_or_obj(key: str) -> Any:
        if key not in instance:
            return []
        if isinstance(instance[key], str):
            return json.loads(instance[key])
        return instance[key]

    # 解析预期的测试通过情况。
    pass_to_pass = _from_json_or_obj("PASS_TO_PASS")
    fail_to_pass = _from_json_or_obj("FAIL_TO_PASS")

    env_name = "testbed"
    repo_directory = f"{env_name}"
    # 根据仓库和版本，从常量映射中获取特定的构建规范。
    specs = MAP_REPO_VERSION_TO_SPECS[repo][version]

    # 调用分发函数，生成用于设置仓库、环境和评估的命令列表。
    repo_script_list = make_repo_script_list(specs, repo, repo_directory, base_commit, env_name)
    
    # ===================================================================
    # START: 修改部分 2
    # 接收 make_env_script_list 返回的元组，并将其解包到两个变量中
    env_script_list, env_manager = make_env_script_list(instance, specs, env_name)
    # ===================================================================
    # END: 修改部分 2
    
    eval_script_list = make_eval_script_list(instance, specs, env_name, repo_directory, base_commit, test_patch)

    # 检测当前运行环境的系统架构（如 x86_64 或 arm64），以实现跨平台兼容。
    if platform.machine() in {"aarch64", "arm64"}:
        arch = "arm64" if instance_id not in USE_X86 else "x86_64"
    else:
        arch = "x86_64"

    # 通过对环境设置脚本列表进行哈希计算，生成一个唯一的环境密钥 (env_key)。
    # 这个密钥是实现环境缓存、避免重复构建相同环境的关键。
    hash_key = str(env_script_list)
    hash_object = hashlib.sha256()
    hash_object.update(hash_key.encode("utf-8"))
    env_key = hash_object.hexdigest()[:22]

    # 将所有收集和生成的信息组装成一个完整的 TestSpec 对象并返回。
    return TestSpec(
        instance_id=instance_id,
        repo=repo,
        env_script_list=env_script_list,
        repo_script_list=repo_script_list,
        eval_script_list=eval_script_list,
        version=version,
        # ===============================================================
        # START: 修改部分 3
        # 在创建 TestSpec 实例时，传入新增的 env_manager
        env_manager=env_manager,
        # ===============================================================
        # END: 修改部分 3
        arch=arch,
        FAIL_TO_PASS=fail_to_pass,
        PASS_TO_PASS=pass_to_pass,
        language=MAP_REPO_TO_EXT[repo],
        env_key=env_key,
    )