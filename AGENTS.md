# Repository Guidelines

## Project Structure & Module Organization

Application code lives in `app/`: API routes are in `app/api/`, shared infrastructure in `app/core/`, and domain workflows in packages such as `ingestion/`, `retrieval/`, `compliance/`, and `workflow/`. The FastAPI entry point is `app/main.py`. Keep tests in `tests/` and operational tools in `scripts/`. Architecture notes belong in `docs/`; datasets and reports are grouped under `evaluation/`. Runtime artifacts under `data/` are excluded from Git.

## Build, Test, and Development Commands

Use Python 3.12 or newer. For a full local environment:

```bash
python -m venv .venv
python -m pip install -e '.[dev,ocr,index,agent]'
python -m app.main
```

Run `pytest` for tests, `ruff format --check .` for formatting, `ruff check .` for lint and import checks, and `mypy app` for strict typing. Run all four before submitting. For containers, use `docker compose build agent` then `docker compose up --detach agent`. Run evaluations with `python scripts/run_evaluation.py` and `python scripts/run_demo_cases.py`.

## Coding Style & Naming Conventions

Use four-space indentation and a maximum line length of 100 characters. Ruff enforces `E`, `F`, `I`, `UP`, and `B` rules; let it organize imports and prefer modern Python syntax. Mypy runs in strict mode, so annotate public functions and avoid untyped escape hatches. Use `snake_case` for functions, variables, and modules; `PascalCase` for classes and Pydantic models; and `UPPER_SNAKE_CASE` for constants. Keep capability-specific logic within its package and shared concerns in `app/core/` or `app/domain/`.

## Testing Guidelines

Tests use pytest and follow `tests/test_<feature>.py` with functions named `test_<expected_behavior>`. Add focused unit tests for new logic and integration tests for API or workflow boundaries. Use pytest fixtures and `monkeypatch` to isolate configuration and external services. There is no fixed coverage threshold, but every bug fix should include a regression test.

## Commit & Pull Request Guidelines

Recent history uses Conventional Commit subjects such as `feat: upgrade adaptive retrieval`. Write concise, imperative subjects with prefixes like `feat:`, `fix:`, `test:`, or `docs:`. Pull requests should explain the problem and approach, identify configuration or data-format changes, link relevant issues, and list verification commands. Include screenshots for changes to `app/web/` and sample report output for evaluation changes.

## Security & Configuration

Copy `.env.example` for local configuration; never commit `.env`, API keys, enterprise documents, databases, indexes, or generated OCR data. Keep reusable correction examples sanitized in `corrections.example.yaml`.
