# Offline model artifacts

Compose automatically runs the idempotent model-provision service when the
artifact manifest is missing or incomplete. It downloads the default BGE
embedding model, Phi-4-mini GGUF, and PaddleOCR detection/recognition snapshots,
resolves every source revision to an immutable upstream commit, hashes the local
artifacts, and writes `manifest.lock.json`. Use the provisioning variables in
`.env` to select different repositories, revisions, or the destination directory.

The model-provision service retries transient registry and DNS failures before
the offline runtime starts. All inference services use only the mounted local
artifact directory after provisioning. Large or licensed weights are not
committed to this repository. Set `HF_TOKEN` before startup when a selected
source requires Hugging Face authentication.

Large or licensed weights are not committed to this repository.
