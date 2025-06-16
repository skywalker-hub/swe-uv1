from __future__ import annotations

import json
import os
import platform
import subprocess
import traceback
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from pathlib import Path

if platform.system() == "Linux":
    import resource

from swebench.harness.constants import (
    APPLY_PATCH_FAIL,
    APPLY_PATCH_PASS,
    KEY_INSTANCE_ID,
    KEY_MODEL,
    KEY_PREDICTION,
    LOG_REPORT,
    LOG_INSTANCE,
    LOG_TEST_OUTPUT,
    RUN_EVALUATION_LOG_DIR,
    UTF8,
)
from swebench.harness.grading import get_eval_report
from swebench.harness.reporting import make_run_report
from swebench.harness.test_spec.test_spec import make_test_spec, TestSpec
from swebench.harness.utils import (
    EvaluationError,
    load_swebench_dataset,
    get_predictions_from_file,
    run_threadpool,
    str2bool,
)
from swebench.harness.uv_env import create_env

GIT_APPLY_CMDS = [
    "git apply --verbose",
    "git apply --verbose --reject",
    "patch --batch --fuzz=5 -p1 -i",
]


def setup_logger(instance_id: str, log_file: Path):
    log_file.parent.mkdir(parents=True, exist_ok=True)
    import logging

    logger = logging.getLogger(instance_id)
    handler = logging.FileHandler(log_file, mode="w", encoding=UTF8)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    setattr(logger, "log_file", log_file)
    return logger


def close_logger(logger):
    for handler in logger.handlers:
        handler.close()
        logger.removeHandler(handler)


def run_instance(
    test_spec: TestSpec,
    pred: dict,
    run_id: str,
    timeout: int | None = None,
    rewrite_reports: bool = False,
):
    instance_id = test_spec.instance_id
    model_name_or_path = pred.get(KEY_MODEL, "None").replace("/", "__")
    log_dir = RUN_EVALUATION_LOG_DIR / run_id / model_name_or_path / instance_id

    report_path = log_dir / LOG_REPORT
    if rewrite_reports:
        test_output_path = log_dir / LOG_TEST_OUTPUT
        if not test_output_path.exists():
            raise ValueError(f"Test output file {test_output_path} does not exist")
        report = get_eval_report(
            test_spec=test_spec,
            prediction=pred,
            test_log_path=test_output_path,
            include_tests_status=True,
        )
        with open(report_path, "w") as f:
            f.write(json.dumps(report, indent=4))
        return instance_id, report
    if report_path.exists():
        return instance_id, json.loads(report_path.read_text())

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / LOG_INSTANCE
    logger = setup_logger(instance_id, log_file)

    env_path = create_env(test_spec.env_script_list, test_spec.env_key)
    work_dir = log_dir / "work"
    if work_dir.exists():
        subprocess.run(["rm", "-rf", str(work_dir)])
    work_dir.mkdir(parents=True, exist_ok=True)

    repo_script = log_dir / "repo.sh"
    repo_script.write_text(test_spec.install_repo_script)
    eval_script = log_dir / "eval.sh"
    eval_script.write_text(test_spec.eval_script)
    patch_file = log_dir / "patch.diff"
    patch_file.write_text(pred[KEY_PREDICTION] or "")

    try:
        subprocess.run(
            f"bash {repo_script.resolve()}",
            shell=True,
            check=True,
            cwd=work_dir,
            executable="/bin/bash",
            env={**os.environ, "VIRTUAL_ENV": str(env_path)},
        )

        repo_dir = work_dir / "testbed"
        applied_patch = False
        for git_apply_cmd in GIT_APPLY_CMDS:
            full_cmd = f"source {env_path}/bin/activate && {git_apply_cmd} {patch_file.resolve()}"
            logger.info(f"Trying patch command: {full_cmd}")
            val = subprocess.run(
                full_cmd,
                shell=True,
                cwd=repo_dir,
                capture_output=True,
                text=True,
                executable="/bin/bash",
            )
            if val.returncode == 0:
                logger.info(f"{APPLY_PATCH_PASS}:\n{val.stdout}")
                applied_patch = True
                break
            else:
                logger.warning(f"Patch failed: {git_apply_cmd}\nSTDOUT: {val.stdout}\nSTDERR: {val.stderr}")
                
        if not applied_patch:
            logger.info(f"{APPLY_PATCH_FAIL}:\n{val.stdout}")
            raise EvaluationError(instance_id, f"{APPLY_PATCH_FAIL}: {val.stdout}", logger)

        with open(log_dir / "eval_out.txt", "w") as f:
            try:
                subprocess.run(
                    f"source {env_path}/bin/activate && bash {eval_script.resolve()}",
                    shell=True,
                    cwd=work_dir,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    executable="/bin/bash",
                )
            except subprocess.TimeoutExpired:
                f.write(f"\n\nTimeout error: {timeout} seconds exceeded.")
                raise EvaluationError(instance_id, f"Test timed out after {timeout} seconds.", logger)

        report = get_eval_report(
            test_spec=test_spec,
            prediction=pred,
            test_log_path=log_dir / "eval_out.txt",
            include_tests_status=True,
        )
        with open(report_path, "w") as f:
            f.write(json.dumps(report, indent=4))
        return instance_id, report
    except EvaluationError as e:
        logger.info(traceback.format_exc())
        print(e)
    except Exception as e:
        error_msg = (
            f"Error in evaluating model for {instance_id}: {e}\n"
            f"{traceback.format_exc()}\n"
            f"Check ({logger.log_file}) for more information."
        )
        logger.error(error_msg)
    finally:
        close_logger(logger)
    return


def run_instances(
    predictions: dict,
    instances: list,
    max_workers: int,
    run_id: str,
    timeout: int,
    rewrite_reports: bool = False,
):
    test_specs = list(map(make_test_spec, instances))
    payloads = []
    for test_spec in test_specs:
        payloads.append(
            (
                test_spec,
                predictions[test_spec.instance_id],
                run_id,
                timeout,
                rewrite_reports,
            )
        )

    print(f"Running {len(instances)} instances...")
    run_threadpool(run_instance, payloads, max_workers)
    print("All instances run.")


def get_dataset_from_preds(
    dataset_name: str,
    split: str,
    instance_ids: list,
    predictions: dict,
    run_id: str,
    rewrite_reports: bool,
    exclude_completed: bool = True,
):
    dataset = load_swebench_dataset(dataset_name, split)
    dataset_ids = {i[KEY_INSTANCE_ID] for i in dataset}

    if instance_ids:
        missing_preds = set(instance_ids) - set(predictions.keys())
        if missing_preds:
            print(f"Warning: Missing predictions for {len(missing_preds)} instance IDs.")

    prediction_ids = set(predictions.keys())
    if prediction_ids - dataset_ids:
        raise ValueError(
            "Some prediction IDs not found in dataset!\n" + " ".join(prediction_ids - dataset_ids)
        )
    if instance_ids:
        dataset = [i for i in dataset if i[KEY_INSTANCE_ID] in instance_ids]

    if rewrite_reports:
        test_output_ids = set()
        for instance in dataset:
            if instance[KEY_INSTANCE_ID] not in predictions:
                continue
            prediction = predictions[instance[KEY_INSTANCE_ID]]
            test_output_file = (
                RUN_EVALUATION_LOG_DIR
                / run_id
                / prediction["model_name_or_path"].replace("/", "__")
                / prediction[KEY_INSTANCE_ID]
                / "eval_out.txt"
            )
            if test_output_file.exists():
                test_output_ids.add(instance[KEY_INSTANCE_ID])
        dataset = [
            i
            for i in dataset
            if i[KEY_INSTANCE_ID] in prediction_ids and i[KEY_INSTANCE_ID] in test_output_ids
        ]
        return dataset

    completed_ids = set()
    for instance in dataset:
        if instance[KEY_INSTANCE_ID] not in prediction_ids:
            continue
        prediction = predictions[instance[KEY_INSTANCE_ID]]
        report_file = (
            RUN_EVALUATION_LOG_DIR
            / run_id
            / prediction[KEY_MODEL].replace("/", "__")
            / prediction[KEY_INSTANCE_ID]
            / LOG_REPORT
        )
        if report_file.exists():
            completed_ids.add(instance[KEY_INSTANCE_ID])

    if completed_ids and exclude_completed:
        print(f"{len(completed_ids)} instances already run, skipping...")
        dataset = [i for i in dataset if i[KEY_INSTANCE_ID] not in completed_ids]

    empty_patch_ids = {k for k, v in predictions.items() if v[KEY_PREDICTION] == "" or v[KEY_PREDICTION] is None}

    dataset = [
        i
        for i in dataset
        if i[KEY_INSTANCE_ID] in prediction_ids and i[KEY_INSTANCE_ID] not in empty_patch_ids
    ]
    return dataset


def main(
    dataset_name: str,
    split: str,
    instance_ids: list,
    predictions_path: str,
    max_workers: int,
    open_file_limit: int,
    run_id: str,
    timeout: int,
    rewrite_reports: bool,
    report_dir: str = ".",
):
    if dataset_name == "SWE-bench/SWE-bench_Multimodal" and split == "test":
        print(
            "⚠️ Local evaluation for the test split of SWE-bench Multimodal is not supported. "
            "Please check out sb-cli (https://github.com/swe-bench/sb-cli/) for instructions on how to submit predictions."
        )
        return

    assert len(run_id) > 0, "Run ID must be provided"
    if report_dir is not None:
        report_dir = Path(report_dir)
        if not report_dir.exists():
            report_dir.mkdir(parents=True)

    predictions = get_predictions_from_file(predictions_path, dataset_name, split)
    predictions = {pred[KEY_INSTANCE_ID]: pred for pred in predictions}

    dataset = get_dataset_from_preds(dataset_name, split, instance_ids, predictions, run_id, rewrite_reports)
    full_dataset = load_swebench_dataset(dataset_name, split, instance_ids)

    if platform.system() == "Linux":
        resource.setrlimit(resource.RLIMIT_NOFILE, (open_file_limit, open_file_limit))

    if not dataset:
        print("No instances to run.")
    else:
        run_instances(
            predictions,
            dataset,
            max_workers,
            run_id,
            timeout,
            rewrite_reports=rewrite_reports,
        )

    return make_run_report(predictions, full_dataset, run_id)


if __name__ == "__main__":
    parser = ArgumentParser(
        description="Run evaluation harness for the given dataset and predictions.",
        formatter_class=ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--dataset_name",
        default="SWE-bench/SWE-bench_Lite",
        type=str,
        help="Name of dataset or path to JSON file.",
    )
    parser.add_argument("--split", type=str, default="test", help="Split of the dataset")
    parser.add_argument(
        "--instance_ids",
        nargs="+",
        type=str,
        help="Instance IDs to run (space separated)",
    )
    parser.add_argument(
        "--predictions_path",
        type=str,
        help="Path to predictions file - if 'gold', uses gold predictions",
        required=True,
    )
    parser.add_argument(
        "--max_workers",
        type=int,
        default=4,
        help="Maximum number of workers (should be <= 75% of CPU cores)",
    )
    parser.add_argument("--open_file_limit", type=int, default=4096, help="Open file limit")
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout (in seconds) for running tests for each instance",
    )
    parser.add_argument("--run_id", type=str, required=True, help="Run ID - identifies the run")
    parser.add_argument(
        "--rewrite_reports",
        type=str2bool,
        default=False,
        help="Doesn't run new instances, only writes reports for instances with existing test outputs",
    )
    parser.add_argument("--report_dir", type=str, default=".", help="Directory to write reports to")

    args = parser.parse_args()
    main(**vars(args))
