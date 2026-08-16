"""Central configuration for the Fakturama automation package.

Everything tunable lives here so the flow code stays declarative. All values
can be overridden via environment variables (loaded by the Flask app's
python-dotenv, or your shell).
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

# Load .env here rather than relying on the importer to have done it first.
# Settings are read at import time, so whichever module happens to be imported
# first would otherwise decide whether .env was visible at all — which made
# correctness depend on import order.
load_dotenv()

# python-dotenv expands backslash escapes, so a Windows path written plainly in
# .env (TESSERACT_CMD=C:\...\tesseract.exe) arrives with a literal TAB where
# '\t' was. Map the control characters back to the two-character sequences they
# were written as.
_ESCAPE_REPAIRS = {
    "\t": r"\t", "\n": r"\n", "\r": r"\r",
    "\b": r"\b", "\f": r"\f", "\v": r"\v", "\a": r"\a",
}

_TESSERACT_FALLBACKS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


def _repair_escapes(path: str) -> str:
    for control, literal in _ESCAPE_REPAIRS.items():
        path = path.replace(control, literal)
    return path


def _resolve_tesseract() -> Optional[str]:
    """First path that actually exists: the configured one, the same with
    escape damage repaired, the standard install locations, then PATH."""
    configured = os.environ.get("TESSERACT_CMD")
    candidates = []
    if configured:
        candidates += [configured, _repair_escapes(configured)]
    candidates += list(_TESSERACT_FALLBACKS)

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            if configured and candidate != configured:
                print(f"[config] TESSERACT_CMD={configured!r} does not exist; "
                      f"using {candidate!r} instead")
            return candidate
    return shutil.which("tesseract")


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # --- locating / launching Fakturama ---------------------------------
    # Path to the executable. Only used when attach_only is False.
    fakturama_path: str = os.environ.get(
        "FAKTURAMA_PATH", r"C:\Program Files\Fakturama2\Fakturama.exe"
    )
    # The top-level window title always starts with this. From the UIA dump:
    #   Name: "Fakturama - C:\Users\...\fakturama"
    window_title_substr: str = os.environ.get("FAKTURAMA_WINDOW", "Fakturama -")
    # By default we attach to an already-running instance rather than launch,
    # because the DB / workspace is chosen at startup and we don't want to
    # second-guess it. Set FAKTURAMA_ATTACH_ONLY=0 to allow launching.
    attach_only: bool = os.environ.get("FAKTURAMA_ATTACH_ONLY", "1") != "0"

    # --- timing ---------------------------------------------------------
    search_timeout: float = _f("UIA_TIMEOUT", 12.0)        # global default find timeout
    short_timeout: float = _f("UIA_SHORT_TIMEOUT", 4.0)    # "is it there yet?" checks
    action_pause: float = _f("UIA_ACTION_PAUSE", 0.25)     # settle time after an action
    editor_wait: float = _f("UIA_EDITOR_WAIT", 15.0)       # wait for a new editor tab
    launch_wait: float = _f("FAKTURAMA_LAUNCH_WAIT", 45.0) # cold-start splash can be slow

    # --- formatting -----------------------------------------------------
    # The Date edit displays like 'Aug 15, 2026'. We build this manually
    # (stripping the leading zero on the day) rather than relying on %d.
    # (kept here for reference/documentation)
    date_format_display: str = "%b %d, %Y"

    # --- artifacts ------------------------------------------------------
    screenshot_dir: str = os.environ.get("UIA_SHOT_DIR", "data/automation_shots")

    # --- OCR (NatTable row reads in selector dialogs) --------------------
    # Resolved to a path that exists, so a mistyped or escape-mangled
    # TESSERACT_CMD degrades to the standard install location instead of
    # failing at the first OCR call.
    tesseract_cmd: Optional[str] = _resolve_tesseract()


SETTINGS = Settings()
