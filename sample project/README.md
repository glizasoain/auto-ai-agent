# circle-demo

A tiny Python 3.10+ project that demonstrates how the "Autonomous Aider Agent" can draw a filled circle, save it to `artifacts/circle.png`, and expose the image over a minimal Flask endpoint.

## Requirements
- Python 3.10+
- macOS or Linux environment with virtualenv support

## Setup
1. `python -m venv venv`
2. `source venv/bin/activate`
3. `pip install -r requirements.txt`
4. `pytest`
5. `python -m circle_demo.cli --radius 120 --color "#228be6"` (add `PYTHONPATH=src` when running from an editable checkout)
6. `flask --app app run`

## Usage
- CLI: `python -m circle_demo.cli --radius 120 --color "#228be6"`
- Flask endpoint: `curl http://127.0.0.1:5000/circle --output circle.png`

The CLI writes to `artifacts/circle.png` by default. The Flask route regenerates the file when needed.

## Guardrails Summary
- Work only on issues labeled `AI_ALLOWED` and `Ready for Dev`.
- Use branches formatted `ai/{ISSUE_KEY}-{slug}`.
- Never touch `infra/**` or `secrets/**`.
- Always add/update tests when code paths change.
- Ensure all tests pass before preparing a PR.
- Summarize changes and link the Jira ticket in the PR body.

See `guardrails.yaml` for the YAML source, `.cursor/rules/ai-allowed.mdc` for an IDE reminder, and `guardrails.plan.md` for enforcement notes.

## Next steps for the autonomous agent
- Add additional CLI flags (e.g., background color, image size) and expand unit tests to cover those branches.
- Write Flask route tests and integrate automated formatting/linting (e.g., ruff + pre-commit).

