"""
Python exec tool — run a short Python snippet and capture its output.
For calculations, data processing, date math — anything the model shouldn't guess.
Runs in a subprocess with a timeout. Local dev use only.
"""

from __future__ import annotations

import subprocess
import sys

SCHEMA = {
    "type": "function",
    "function": {
        "name": "python_exec",
        "description": (
            "Execute a short Python 3 script and return its stdout. Use for math, "
            "calculations, data processing, string manipulation, or date arithmetic — "
            "anything precise you should compute rather than guess. "
            "IMPORTANT: use print() to output results, or nothing is returned."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code. Use print() for output."},
            },
            "required": ["code"],
        },
    },
}

_TIMEOUT = 15


def execute(code: str) -> str:
    try:
        r = subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True, timeout=_TIMEOUT,
        )
        out = r.stdout.decode("utf-8", errors="replace").strip()
        err = r.stderr.decode("utf-8", errors="replace").strip()
        if r.returncode != 0:
            return f"[ERROR] Python exited {r.returncode}:\n{err or out}"
        if not out and err:
            return f"(stderr)\n{err}"
        return out or "(sin salida — recuerda usar print())"
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT] El código excedió {_TIMEOUT}s."
    except Exception as e:
        return f"[ERROR] {type(e).__name__}: {e}"
