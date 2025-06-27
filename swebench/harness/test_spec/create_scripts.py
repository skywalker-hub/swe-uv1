from swebench.harness.constants import MAP_REPO_TO_EXT
from swebench.harness.test_spec.javascript import (
    make_eval_script_list_js,
)
from swebench.harness.test_spec.python import (
    make_repo_script_list_py,
    make_env_script_list_py,
    make_eval_script_list_py,
)
from swebench.harness.test_spec.utils import (
    make_env_script_list_common,
    make_eval_script_list_common,
    make_repo_script_list_common,
)


# 该函数是生成“仓库设置”脚本命令的分发器。
def make_repo_script_list(specs, repo, repo_directory, base_commit, env_name) -> list:
    """
    Create a list of bash commands to set up the repository for testing.
    This is the setup script for the instance image.
    """
    # 1. 根据仓库名称确定其编程语言扩展名（如 'py'）。
    ext = MAP_REPO_TO_EXT[repo]
    # 2. 使用字典和 .get() 方法实现动态分发（工厂模式）。
    #    如果语言是 'py'，则选择专门的Python实现函数。
    #    对于任何其他语言，则回退到通用的实现函数。
    func = {
        "py": make_repo_script_list_py,
    }.get(ext, make_repo_script_list_common)
    # 3. 调用被选中的函数，生成并返回最终的命令列表。
    return func(specs, repo, repo_directory, base_commit, env_name)


# 该函数是生成“环境设置”脚本命令的分发器。
# 该函数是生成“环境设置”脚本命令的分发器。
def make_env_script_list(instance, specs, env_name) -> tuple[list[str], str]:
    """
    Creates the list of commands to set up the environment for testing, 
    and returns the appropriate environment manager type.
    """
    # 1. 根据实例的仓库名称确定语言。
    ext = MAP_REPO_TO_EXT[instance["repo"]]
    
    # 2. 核心修改：为不同的语言路径提供不同的处理方式
    if ext == "py":
        # 如果是Python，直接调用我们修改好的 _py 函数，它会返回 (scripts, env_manager) 元组
        return make_env_script_list_py(instance, specs, env_name)
    else:
        # 对于其他通用语言，我们假设它们默认使用 uv 环境。
        # 调用原来的 common 函数，它只返回一个脚本列表。
        scripts = make_env_script_list_common(instance, specs, env_name)
        # 我们手动将它包装成我们需要的元组格式，并指定默认的管理器为 'uv'。
        return scripts, "uv"


# 该函数是生成“执行评估”脚本命令的分发器。
def make_eval_script_list(
    instance, specs, env_name, repo_directory, base_commit, test_patch
) -> list:
    """
    Applies the test patch and runs the tests.
    """
    # 1. 确定语言类型。
    ext = MAP_REPO_TO_EXT[instance["repo"]]
    common_func = make_eval_script_list_common
    # 2. 动态分发：此处的映射表比前两个函数更丰富。
    #    它为 JavaScript ('js') 和 Python ('py') 都定义了专门的评估脚本生成函数。
    func = {
        "js": make_eval_script_list_js,
        "py": make_eval_script_list_py,
    }.get(ext, common_func)
    # 3. 执行并返回最终的命令列表。
    return func(instance, specs, env_name, repo_directory, base_commit, test_patch)