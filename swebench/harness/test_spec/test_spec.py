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
    
    instance_id = instance[KEY_INSTANCE_ID]
    repo = instance["repo"]
    version = instance.get("version")
    base_commit = instance["base_commit"]
    test_patch = instance["test_patch"]

    def _from_json_or_obj(key: str) -> Any:
        if key not in instance: return []
        if isinstance(instance[key], str): return json.loads(instance[key])
        return instance[key]

    pass_to_pass = _from_json_or_obj("PASS_TO_PASS")
    fail_to_pass = _from_json_or_obj("FAIL_TO_PASS")

    # 定义一个临时的、固定的仓库目录名
    repo_directory = "testbed"
    
    # ===================================================================
    # START: 缓存修复逻辑
    # ===================================================================

    # 1. 定义一个唯一的占位符。
    ENV_NAME_PLACEHOLDER = "___ENV_NAME_PLACEHOLDER___"
    
    # 2. 调用分发函数，但传递的是占位符而不是具体的名字。
    #    这样生成的脚本列表，除了名字部分，其他都已确定。
    specs = MAP_REPO_VERSION_TO_SPECS[repo][version]
    env_script_list, env_manager = make_env_script_list(instance, specs, ENV_NAME_PLACEHOLDER)
    
    # 3. 基于这个含有占位符的脚本列表，计算一个稳定的哈希值 (env_key)。
    #    这个 key 现在可以作为我们期望的、唯一的环境名称。
    hash_key = str(env_script_list)
    hash_object = hashlib.sha256()
    hash_object.update(hash_key.encode("utf-8"))
    env_key = hash_object.hexdigest()[:22]

    # 4. 关键一步：将脚本列表中的占位符，替换为我们刚刚计算出的、真正的环境名 (env_key)。
    final_env_script_list = [s.replace(ENV_NAME_PLACEHOLDER, env_key) for s in env_script_list]
    
    # ===================================================================
    # END: 缓存修复逻辑
    # ===================================================================

    # 生成其他的脚本列表
    repo_script_list = make_repo_script_list(specs, repo, repo_directory, base_commit, env_key)
    eval_script_list = make_eval_script_list(instance, specs, env_key, repo_directory, base_commit, test_patch)
    
    # 检测系统架构
    if platform.machine() in {"aarch64", "arm64"}:
        arch = "arm64" if instance_id not in USE_X86 else "x86_64"
    else:
        arch = "x86_64"

    # 将所有信息组装成 TestSpec 对象
    return TestSpec(
        instance_id=instance_id,
        repo=repo,
        # 使用我们最终修正过的脚本列表
        env_script_list=final_env_script_list,
        repo_script_list=repo_script_list,
        eval_script_list=eval_script_list,
        version=version,
        env_manager=env_manager,
        arch=arch,
        FAIL_TO_PASS=fail_to_pass,
        PASS_TO_PASS=pass_to_pass,
        language=MAP_REPO_TO_EXT[repo],
        env_key=env_key,
    )