# Offline model artifacts

Run `make models-provision` once to download the default Qwen3 embedding model,
Phi-4-mini GGUF, and PaddleOCR detection/recognition snapshots. The command
resolves every source revision to an immutable upstream commit, hashes the local
artifacts, and writes `manifest.lock.json`. Use the provisioning variables in
`.env` to select different repositories, revisions, or the destination directory.

Runtime images intentionally do not download model weights. After provisioning,
run `make models-verify` before starting Compose; all runtime services use only
the mounted local artifact directory. Large or licensed weights are not committed
to this repository. Set `HF_TOKEN` before provisioning when a selected source
requires Hugging Face authentication.

Large or licensed weights are not committed to this repository.
