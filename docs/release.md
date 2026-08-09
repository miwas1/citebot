# Release checklist

1. Update the version in `pyproject.toml` and `app/core/config.py` if needed.
2. Add a dated entry to `CHANGELOG.md`.
3. Run `uv lock`, `make lint`, `make test`, and the sample ingest/search workflow.
4. Build the package with `python -m pip wheel --no-build-isolation --no-deps .` or the normal networked `uv build` workflow.
5. Review bundled data against `THIRD_PARTY_NOTICES.md`; remove or document anything without redistribution rights.
6. Review the Docker image and Compose configuration, especially credentials, published ports, and optional profiles.
7. Create an annotated Git tag matching the package version and publish release notes.
