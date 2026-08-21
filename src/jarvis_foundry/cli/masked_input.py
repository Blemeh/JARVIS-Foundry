"""Windows-safe masked terminal input for secrets."""
from __future__ import annotations

import getpass
import sys


def read_masked_api_key(prompt: str = "API key: ") -> str:
    """Read a secret with ``*`` echo on Windows; ``getpass`` elsewhere."""
    if sys.platform == "win32":
        return _read_masked_windows(prompt)
    return getpass.getpass(prompt)


def _read_masked_windows(prompt: str) -> str:
    import msvcrt

    sys.stdout.write(prompt)
    sys.stdout.flush()
    chars: list[str] = []
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            sys.stdout.write("\n")
            sys.stdout.flush()
            return "".join(chars)
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x08":
            if chars:
                chars.pop()
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue
        chars.append(ch)
        sys.stdout.write("*")
        sys.stdout.flush()
