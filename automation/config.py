"""Central configuration for the Fakturama automation package.

Everything tunable lives here so the flow code stays declarative. All values
can be overridden via environment variables (loaded by the Flask app's
python-dotenv, or your shell).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


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


SETTINGS = Settings()
