""  # Temporary workaround until Poetry supports scripts
# https://github.com/sdispater/poetry/issues/241.
from subprocess import check_call
from pathlib import Path


module_id = "titiler"
project_id = "titiler"
project = "ccore-looknorth"


def format() -> None:
    check_call(["black", module_id, "tests/"])


def start() -> None:
    check_call(["python", "-m", f"{module_id}.main"])


def api() -> None:
    check_call(["python", "-m", f"{module_id}.api"])


def test() -> None:
    check_call(["pytest", "tests/"])


def freeze() -> None:
    with open("requirements.txt", "w") as file:
        check_call(["poetry", "export", "-f", "requirements.txt"], stdout=file)


def docker_build() -> None:
    check_call(["docker", "build", "-t", f"gcr.io/{project}/{project_id}:latest", "."])


def docker_run() -> None:
    check_call(
        [
            "docker",
            "run",
            "--rm",
            "-it",
            "-p",
            "8080:8080",
            "-t",
            f"gcr.io/{project}/{project_id}:latest",
        ]
    )


def docker() -> None:
    freeze()
    docker_build()
    docker_run()


def docker_run_bash() -> None:
    freeze()
    docker_build()
    check_call(
        [
            "docker",
            "run",
            "--rm",
            "-it",
            "-p",
            "8080:8080",
            "-t",
            f"gcr.io/{project}/{project_id}:latest",
            "/bin/bash",
        ]
    )


def docker_push() -> None:
    check_call(["docker", "push", f"gcr.io/{project}/{project_id}:latest"])


def docker_deploy() -> None:
    check_call(
        [
            "gcloud",
            "beta",
            "run",
            "deploy",
            project_id,
            "--image",
            f"gcr.io/{project}/{project_id}:latest",
            "--platform=managed",
            "--allow-unauthenticated",
            "--concurrency=80",
            "--memory=512Mi",
            "--timeout=1m",
            f"--project={project}",
        ]
    )


def deploy() -> None:
    docker_build()
    docker_push()
    docker_deploy()
