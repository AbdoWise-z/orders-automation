"""Flask glue for the automation package.

Register from app.py with two lines (after `load_order` / `save_order` and
`ORDERS_DIR` are defined):

    from automation_web import register_automation_routes
    register_automation_routes(app, ORDERS_DIR, load_order, save_order)

Adds:
    POST /automation/<order_id>/run   -> starts a background run, redirects to status
    GET  /automation/<order_id>       -> status page (polls itself)
    POST /automation/doctor           -> grounding self-check (JSON)

Desktop UI automation is exclusive (it drives the real mouse/keyboard), so runs
are serialised behind a single lock; a second run while one is active is
rejected with a clear message.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Callable

from flask import jsonify, redirect, render_template, request, url_for

_run_lock = threading.Lock()
_active: dict[str, str] = {}   # order_id -> status, for a quick "is it running" view


def register_automation_routes(app, orders_dir, load_order: Callable,
                               save_order: Callable) -> None:

    def _persist(order_id: str, result_dict: dict) -> None:
        data = load_order(order_id)
        data.setdefault("_meta", {})
        data["_meta"]["automation"] = {
            **result_dict,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        save_order(order_id, data)

    def _worker(order_id: str, stop_after: str) -> None:
        # Imported lazily so the Flask app still boots on machines without the
        # UIA stack installed (e.g. the extraction-only dev box).
        from automation.runner import run_automation
        try:
            data = load_order(order_id)
            result = run_automation(data, stop_after=stop_after)
            _persist(order_id, result.to_dict())
        except Exception as exc:  # noqa: BLE001
            _persist(order_id, {"status": "error",
                                "error": {"error": type(exc).__name__,
                                          "message": str(exc)}})
        finally:
            _active.pop(order_id, None)
            if _run_lock.locked():
                _run_lock.release()

    @app.route("/automation/<order_id>/run", methods=["POST"])
    def automation_run(order_id):
        stop_after = request.form.get("stop_after", "invoice")
        if not _run_lock.acquire(blocking=False):
            busy = next(iter(_active), "another order")
            _persist(order_id, {"status": "error",
                                "error": {"message":
                                          f"Automation busy running {busy}. "
                                          f"Desktop control is exclusive — "
                                          f"try again when it finishes."}})
            return redirect(url_for("automation_status", order_id=order_id))
        _active[order_id] = "running"
        _persist(order_id, {"status": "running", "steps": []})
        threading.Thread(target=_worker, args=(order_id, stop_after),
                         daemon=True).start()
        return redirect(url_for("automation_status", order_id=order_id))

    @app.route("/automation/<order_id>", methods=["GET"])
    def automation_status(order_id):
        data = load_order(order_id)
        auto = (data.get("_meta") or {}).get("automation") or {"status": "idle"}
        if request.args.get("format") == "json":
            return jsonify(auto)
        return render_template("automation.html", order_id=order_id,
                               order=data, automation=auto)

    @app.route("/automation/doctor", methods=["POST"])
    def automation_doctor():
        from automation.doctor import run_doctor
        return jsonify(run_doctor().to_dict())
