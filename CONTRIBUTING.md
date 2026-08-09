# Contributing to CiteBot

Thanks for helping improve CiteBot. Keep changes focused, document behavior changes, and add or update tests for user-facing behavior.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
```

The default environment uses SQLite and deterministic local providers, so tests do not require Docker or API credentials.

## Checks before a pull request

```bash
make test
make lint
```

For changes involving Docker, also run `docker compose config -q` and describe the tested workflow in the pull request. Do not commit `.env`, generated databases, downloaded corpora, storage files, or evaluation artifacts.

## Pull requests

Explain the problem, the user-visible change, and how it was verified. Keep secrets out of commits and use the security process for vulnerabilities rather than opening a public issue.
