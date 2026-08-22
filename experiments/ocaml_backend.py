"""Drive the OCaml DSL interpreter as a subprocess.

Decision 1 of ``docs/ocaml_migration_decisions.md`` rules out FFI: the only boundary
between Python and OCaml is a process boundary, and this module is the Python half of
it. The OCaml half is ``interp/bin/kag_policy.ml``, whose header specifies the line
protocol; one JSON request per line in, one JSON response per line out.

Two shapes of use, matching the two shapes the shim serves:

``step(observation, registers)``   stateless — the caller supplies the register bank, so
                                   replaying golden vectors cannot leak state between
                                   unrelated vectors.
``act(observation)``               stateful — the process keeps its own bank, which is
                                   what a live episode through ``reference/run_game.py``
                                   needs.

The binary is built by ``dune build`` and is not checked in, so everything here degrades
to a clear error (or ``is_available() is False``) rather than an import failure when the
OCaml tree has not been built. Set ``KAG_POLICY_EXE`` to point at a different build.
"""

from __future__ import annotations

import atexit
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Self

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = REPO_ROOT / "_build" / "default" / "interp" / "bin" / "kag_policy.exe"

BUILD_HINT = "build it with 'dune build' from the repository root"


class OcamlBackendError(RuntimeError):
    """The shim refused a request, died, or was never built."""


def executable_path() -> Path:
    override = os.environ.get("KAG_POLICY_EXE")
    return Path(override) if override else DEFAULT_EXE


def is_available() -> bool:
    """Whether the shim has been built, so callers can skip rather than fail."""
    path = executable_path()
    return path.is_file() and os.access(path, os.X_OK)


class OcamlPolicy:
    """One shim process: one family, one candidate, one register bank.

    Not thread-safe and not shareable between players — the stateful ``act`` path owns a
    bank, so two players mean two instances, exactly as two ``Policy`` objects would.
    """

    def __init__(
        self,
        family_path: Path,
        candidate_path: Path,
        *,
        executable: Path | None = None,
        policy_id: str | None = None,
    ) -> None:
        exe = Path(executable) if executable else executable_path()
        if not exe.is_file():
            raise OcamlBackendError(f"{exe} does not exist; {BUILD_HINT}")

        command = [
            str(exe),
            "--family",
            str(family_path),
            "--candidate",
            str(candidate_path),
        ]
        if policy_id is not None:
            command += ["--policy-id", policy_id]

        self._command = command
        # Fixed argv, no shell: the only variable parts are the two paths.
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        atexit.register(self.close)
        # Fails loudly here if the family or candidate was rejected, rather than on the
        # first turn with a confusing empty read.
        self.ping()

    # -- protocol ---------------------------------------------------------

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        process = self._process
        if process.poll() is not None:
            raise OcamlBackendError(self._died())
        assert process.stdin is not None and process.stdout is not None
        try:
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()
        except BrokenPipeError:
            raise OcamlBackendError(self._died()) from None
        line = process.stdout.readline()
        if not line:
            raise OcamlBackendError(self._died())
        response = json.loads(line)
        if "error" in response:
            raise OcamlBackendError(response["error"])
        return response

    def _died(self) -> str:
        stderr = ""
        if self._process.stderr is not None:
            stderr = self._process.stderr.read() or ""
        return (
            f"{' '.join(self._command)} exited with {self._process.poll()}"
            f"{': ' + stderr.strip() if stderr.strip() else ''}"
        )

    # -- operations -------------------------------------------------------

    def ping(self) -> dict[str, Any]:
        """Family identity plus the current bank; also the startup health check."""
        return self._request({"op": "ping"})

    def reset(self) -> None:
        self._request({"op": "reset"})

    def step(
        self, observation: dict[str, Any], registers: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        response = self._request({"observation": observation, "registers": registers})
        return response["action"], response["registers"]

    def act(self, observation: dict[str, Any]) -> Any:
        return self._request({"observation": observation})["action"]

    def snapshot(self) -> dict[str, Any]:
        """The held bank, in declaration order — mirrors ``Policy.snapshot``."""
        return self.ping()["registers"]

    def close(self) -> None:
        process = getattr(self, "_process", None)
        if process is None or process.poll() is not None:
            return
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
