"""Bounded Python execution with static validation and process resource limits."""

from __future__ import annotations

import ast
import json
import subprocess
import tempfile
import textwrap
from pathlib import Path
from time import monotonic

from app.agents.schemas import (PythonSandboxExecution, PythonSandboxResult,
                                ToolCallRecord)
from app.core.config import Settings


class PythonSandboxTool:
    """Execute small analysis snippets under strict local limits."""

    def __init__(self, settings: Settings) -> None:
        """Store the timeout and resource limits for executions."""

        self._settings = settings

    async def execute(
        self,
        execution: PythonSandboxExecution,
    ) -> tuple[PythonSandboxResult, ToolCallRecord]:
        """Validate and run sandboxed code, returning results and audit metadata."""

        started = monotonic()
        try:
            _validate_code(execution.code)
        except ValueError as error:
            result = PythonSandboxResult(
                stderr=str(error),
                terminated_reason="validation_error",
            )
            return result, _build_tool_record(
                execution,
                result,
                started,
                status="failed",
                error_message=str(error),
            )
        result = await _run_subprocess_execution(self._settings, execution)
        status = "completed" if result.terminated_reason in {None, "completed"} else "failed"
        return result, _build_tool_record(
            execution,
            result,
            started,
            status=status,
            error_message=result.stderr or None,
        )


def _validate_code(code: str) -> None:
    """Reject imports and dangerous builtins before subprocess execution."""

    tree = ast.parse(code)
    banned_calls = {"open", "exec", "eval", "compile", "__import__", "input"}
    banned_nodes = (ast.Import, ast.ImportFrom, ast.With, ast.AsyncWith, ast.ClassDef)
    for node in ast.walk(tree):
        if isinstance(node, banned_nodes):
            msg = f"Unsupported construct in sandbox: {type(node).__name__}"
            raise ValueError(msg)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in banned_calls:
                msg = f"Unsupported function in sandbox: {node.func.id}"
                raise ValueError(msg)


async def _run_subprocess_execution(
    settings: Settings,
    execution: PythonSandboxExecution,
) -> PythonSandboxResult:
    """Run validated code in an isolated Python interpreter with resource caps."""

    script = _build_runner_script(execution)
    timeout = settings.python_sandbox_timeout_seconds
    start = monotonic()
    with tempfile.TemporaryDirectory(prefix="citebot-sandbox-") as temp_dir:
        script_path = Path(temp_dir) / "runner.py"
        script_path.write_text(script, encoding="utf-8")
        try:
            completed = await _run_process(
                ["python3", "-I", "-S", str(script_path)],
                cwd=Path(temp_dir),
                timeout=timeout,
                memory_mb=settings.python_sandbox_memory_mb,
            )
        except subprocess.TimeoutExpired:
            return PythonSandboxResult(
                stderr="Execution exceeded timeout.",
                terminated_reason="timeout",
                runtime_ms=(monotonic() - start) * 1000,
            )
    stdout = completed.stdout[: settings.python_sandbox_output_bytes]
    stderr = completed.stderr[: settings.python_sandbox_output_bytes]
    result_json = _parse_result(stdout)
    terminated_reason = "completed" if completed.returncode == 0 else "runtime_error"
    return PythonSandboxResult(
        stdout=stdout,
        stderr=stderr,
        result_json=result_json,
        terminated_reason=terminated_reason,
        runtime_ms=(monotonic() - start) * 1000,
    )


async def _run_process(
    command: list[str],
    cwd: Path,
    timeout: float,
    memory_mb: int,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with Linux resource limits applied in the child."""

    def _limit_resources() -> None:
        """Apply CPU, address-space, and file-size limits in the child process."""

        import resource

        memory_bytes = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_CPU, (max(1, int(timeout)), max(1, int(timeout) + 1)))
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))

    return await __import__("asyncio").to_thread(
        subprocess.run,
        command,
        cwd=cwd,
        env={"PYTHONUNBUFFERED": "1"},
        capture_output=True,
        text=True,
        timeout=timeout,
        preexec_fn=_limit_resources,
        check=False,
    )


def _build_runner_script(execution: PythonSandboxExecution) -> str:
    """Render the isolated runner script with safe builtins and JSON output."""

    safe_inputs = json.dumps(execution.inputs)
    safe_code = textwrap.indent(execution.code, "    ")
    return textwrap.dedent(
        f"""
        import json

        SAFE_BUILTINS = {{
            'abs': abs,
            'all': all,
            'any': any,
            'enumerate': enumerate,
            'len': len,
            'list': list,
            'dict': dict,
            'float': float,
            'int': int,
            'max': max,
            'min': min,
            'range': range,
            'round': round,
            'set': set,
            'sorted': sorted,
            'str': str,
            'sum': sum,
            'tuple': tuple,
            'zip': zip,
        }}

        namespace = {{'__builtins__': SAFE_BUILTINS, 'inputs': json.loads({safe_inputs!r})}}
        result = None
{safe_code}
        print(json.dumps({{'result': result}}, default=str))
        """
    ).strip()


def _parse_result(stdout: str) -> dict[str, object]:
    """Parse the final JSON line from sandbox stdout when present."""

    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return {}
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_tool_record(
    execution: PythonSandboxExecution,
    result: PythonSandboxResult,
    started: float,
    status: str,
    error_message: str | None,
) -> ToolCallRecord:
    """Construct the tool audit record from one sandbox execution."""

    return ToolCallRecord(
        tool_name="python_sandbox",
        status=status,
        input_summary=execution.code[:200],
        output_summary=(result.stdout or result.stderr or "No output")[:200],
        duration_ms=(monotonic() - started) * 1000,
        error_message=error_message,
        trace_id=execution.trace_id,
    )    )