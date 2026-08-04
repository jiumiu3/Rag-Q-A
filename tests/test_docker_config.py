from pathlib import Path

import yaml


def test_container_exposes_project_root_on_python_path() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    assert "PYTHONPATH=/app" in dockerfile
    assert compose["services"]["agent"]["environment"]["PYTHONPATH"] == "/app"
    assert "./app:/app/app:ro" in compose["services"]["agent"]["volumes"]
    assert "./scripts:/app/scripts:ro" in compose["services"]["agent"]["volumes"]
    assert "./evaluation:/app/evaluation" in compose["services"]["agent"]["volumes"]
    assert "COPY --chown=app:app evaluation ./evaluation" in dockerfile
    assert "Acquire::Retries=10 \\  " not in dockerfile
