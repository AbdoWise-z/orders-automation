import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Before any project import: several modules read configuration at import time,
# so loading .env afterwards would make behaviour depend on import order.
load_dotenv()

from flask import (Flask, flash, jsonify, redirect, render_template, request,  # noqa: E402
                   send_file, url_for)

from automation.normalization import normalize_record  # noqa: E402
from automation.validation import validate  # noqa: E402
from extraction.extraction import extract_order_fields, run_ocr  # noqa: E402
from extraction.schema import empty_order  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
OCR_DIR = BASE_DIR / "data" / "ocr_output"
ORDERS_DIR = BASE_DIR / "data" / "orders"
for _d in (UPLOAD_DIR, OCR_DIR, ORDERS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "tif", "tiff"}

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def order_path(order_id: str) -> Path:
    return ORDERS_DIR / f"{order_id}.json"


def save_order(order_id: str, data: dict) -> None:
    order_path(order_id).write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_order(order_id: str) -> dict:
    return json.loads(order_path(order_id).read_text(encoding="utf-8"))


def to_number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def build_record(payload: dict) -> dict:
    """Merge an extracted (or uploaded) payload onto the empty schema so every
    key the review page renders is present.

    The nested addresses are merged separately: a plain ``update`` would swap
    in the payload's own dict wholesale and lose any key it happens to omit.
    """
    record = empty_order()
    record["order"].update(payload.get("order") or {})

    debtor = dict(payload.get("debtor") or {})
    billing = debtor.pop("billing_address", None) or {}
    delivery = debtor.pop("delivery_address", None) or {}
    record["debtor"].update(debtor)
    record["debtor"]["billing_address"].update(billing)
    record["debtor"]["delivery_address"].update(delivery)

    record["items"] = payload.get("items") or []
    # Canonicalise up front so the review page shows the values the automation
    # will actually use — dates as ISO, countries as the combo's English name,
    # numbers parsed out of '1.234,56' and so on.
    normalize_record(record)
    return record


def list_orders(limit: int = 30) -> list:
    """Recent order records, newest first, summarised for the home page."""
    paths = sorted(ORDERS_DIR.glob("*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    rows = []
    for path in paths[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue          # a half-written or hand-edited file shouldn't 500 the page
        meta = data.get("_meta") or {}
        automation = meta.get("automation") or {}
        debtor = data.get("debtor") or {}
        rows.append({
            "order_id": meta.get("order_id") or path.stem,
            "status": meta.get("status") or "unknown",
            "automation_status": automation.get("status"),
            "company": debtor.get("company") or debtor.get("contact_name"),
            "reference": (data.get("order") or {}).get("external_reference"),
            "item_count": len(data.get("items") or []),
            "updated_at": meta.get("updated_at") or meta.get("created_at"),
            "source": meta.get("source", "image"),
        })
    return rows


@app.route("/")
def index():
    return render_template("upload.html", orders=list_orders())


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("order_image")
    if not file or file.filename == "":
        flash("Choose an image file first.")
        return redirect(url_for("index"))
    if not allowed_file(file.filename):
        flash("Unsupported file type. Use PNG, JPG, WEBP, or TIFF.")
        return redirect(url_for("index"))

    order_id = uuid.uuid4().hex[:12]
    image_path = UPLOAD_DIR / f"{order_id}_{file.filename}"
    file.save(image_path)

    ocr_dir = OCR_DIR / order_id  # fresh per-order dir, see extraction.run_ocr

    try:
        (markdown_text, json_ocr) = run_ocr(str(image_path), ocr_dir)
        extracted = extract_order_fields(markdown_text, json_ocr)
    except Exception as exc:  # noqa: BLE001 -- surfaced to the user, not swallowed
        flash(f"Extraction failed: {exc}")
        return redirect(url_for("index"))

    record = build_record(extracted)
    record["_meta"] = {
        "order_id": order_id,
        "source": "image",
        "source_image": image_path.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "extracted",
    }
    save_order(order_id, record)
    return redirect(url_for("review", order_id=order_id))


@app.route("/upload-json", methods=["POST"])
def upload_json():
    """Load an already-extracted record straight from a .json file, skipping
    OCR entirely — for re-running a previous extraction, or for a record
    produced elsewhere."""
    file = request.files.get("order_json")
    if not file or file.filename == "":
        flash("Choose a JSON file first.")
        return redirect(url_for("index"))
    if not file.filename.lower().endswith(".json"):
        flash("Unsupported file type. Upload a .json order record.")
        return redirect(url_for("index"))

    try:
        payload = json.loads(file.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        flash(f"That file is not valid JSON: {exc}")
        return redirect(url_for("index"))
    if not isinstance(payload, dict):
        flash("Expected a JSON object with 'order', 'debtor' and 'items' keys.")
        return redirect(url_for("index"))

    # A fresh id, so re-uploading an exported record never overwrites the
    # original run's history.
    order_id = uuid.uuid4().hex[:12]
    record = build_record(payload)
    record["_meta"] = {
        "order_id": order_id,
        "source": "json",
        "source_filename": file.filename,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "extracted",
    }
    save_order(order_id, record)
    flash(f"Loaded {file.filename} — check the values before running automation.")
    return redirect(url_for("review", order_id=order_id))


@app.route("/review/<order_id>", methods=["GET"])
def review(order_id):
    data = load_order(order_id)
    return render_template("review.html", order=data, order_id=order_id)


@app.route("/review/<order_id>", methods=["POST"])
def save_review(order_id):
    data = load_order(order_id)
    form = request.form

    data["order"]["order_date"] = form.get("order_date") or None
    data["order"]["external_reference"] = form.get("external_reference") or None
    data["order"]["customer_id"] = form.get("customer_id") or None
    data["order"]["currency"] = form.get("currency") or None

    data["debtor"]["company"] = form.get("debtor_company") or None
    data["debtor"]["contact_name"] = form.get("debtor_contact_name") or None
    data["debtor"]["alias"] = form.get("debtor_alias") or None
    data["debtor"]["email"] = form.get("debtor_email") or None
    data["debtor"]["phone"] = form.get("debtor_phone") or None
    data["debtor"]["payment_method"] = form.get("payment_method") or None
    data["debtor"]["paid_status"] = form.get("paid_status") or None
    data["debtor"]["payment_date"] = form.get("payment_date") or None
    data["debtor"]["billing_address"] = {
        "street": form.get("billing_street") or None,
        "zip": form.get("billing_zip") or None,
        "city": form.get("billing_city") or None,
        "country": form.get("billing_country") or None,
    }
    data["debtor"]["delivery_address"] = {
        "street": form.get("delivery_street") or None,
        "zip": form.get("delivery_zip") or None,
        "city": form.get("delivery_city") or None,
        "country": form.get("delivery_country") or None,
    }

    fields = [
        "item_sku", "item_description", "item_quantity", "item_unit",
        "item_unit_net_price", "item_discount_pct", "item_vat_pct", "item_line_net_total",
    ]
    columns = {f: form.getlist(f) for f in fields}
    row_count = len(columns["item_sku"])

    items = []
    for i in range(row_count):
        sku = columns["item_sku"][i].strip()
        description = columns["item_description"][i].strip()
        if not sku and not description:
            continue  # skip blank trailing rows from the "+ Add item" button
        items.append({
            "sku": sku or None,
            "description": description or None,
            "quantity": to_number(columns["item_quantity"][i]),
            "unit": columns["item_unit"][i] or None,
            "unit_net_price": to_number(columns["item_unit_net_price"][i]),
            "discount_pct": to_number(columns["item_discount_pct"][i]),
            "vat_pct": to_number(columns["item_vat_pct"][i]),
            "line_net_total": to_number(columns["item_line_net_total"][i]),
        })
    data["items"] = items

    normalize_record(data)   # the operator may have typed a local date format

    action = form.get("action")
    data["_meta"]["status"] = "ready_for_automation" if action == "finalize" else "edited"
    data["_meta"]["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_order(order_id, data)

    if action == "finalize":
        return redirect(url_for("finalized", order_id=order_id))
    flash("Changes saved.")
    return redirect(url_for("review", order_id=order_id))


@app.route("/review/<order_id>/finalized")
def finalized(order_id):
    data = load_order(order_id)
    return render_template("finalized.html", order=data, order_id=order_id)


@app.route("/orders/<order_id>.json")
def download_order(order_id):
    """Download the record — the counterpart to /upload-json, so a run can be
    exported and replayed later."""
    return send_file(order_path(order_id), mimetype="application/json",
                     as_attachment=True, download_name=f"{order_id}.json")


# --------------------------------------------------------------------------- #
# Automation
#
# Driving the desktop is exclusive — it moves the real mouse and keyboard — so
# runs are serialised behind a single lock and a second request is refused
# rather than queued. The run itself happens on a background thread so the
# request can return immediately; the page polls for progress.
# --------------------------------------------------------------------------- #
_run_lock = threading.Lock()
_active_order: Optional[str] = None


def _persist_automation(order_id: str, payload: dict) -> None:
    data = load_order(order_id)
    data.setdefault("_meta", {})["automation"] = {
        **payload,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_order(order_id, data)


def _automation_worker(order_id: str, stop_after: str) -> None:
    global _active_order
    # Imported lazily so the app still boots on a machine without the UIA
    # stack (extraction and review work fine there).
    from automation.runner import run_automation
    try:
        result = run_automation(load_order(order_id), stop_after=stop_after)
        _persist_automation(order_id, result.to_dict())
    except Exception as exc:  # noqa: BLE001 — must reach the page, not a log
        _persist_automation(order_id, {
            "status": "error",
            "error": {"error": type(exc).__name__, "message": str(exc),
                      "user_message": "The automation stopped unexpectedly."},
        })
    finally:
        _active_order = None
        _run_lock.release()


@app.route("/automation/<order_id>/run", methods=["POST"])
def automation_run(order_id):
    global _active_order
    stop_after = request.form.get("stop_after", "invoice")

    if not _run_lock.acquire(blocking=False):
        flash(f"Automation is already running {_active_order or 'another order'}. "
              f"Desktop control is exclusive — wait for it to finish.")
        return redirect(url_for("automation_status", order_id=order_id))

    _active_order = order_id
    _persist_automation(order_id, {"status": "running", "steps": [],
                                   "stop_after": stop_after})
    threading.Thread(target=_automation_worker, args=(order_id, stop_after),
                     daemon=True).start()
    return redirect(url_for("automation_status", order_id=order_id))


@app.route("/automation/<order_id>", methods=["GET"])
def automation_status(order_id):
    data = load_order(order_id)
    state = (data.get("_meta") or {}).get("automation") or {"status": "idle"}
    if request.args.get("format") == "json":
        return jsonify(state)

    # Show what would block a run before the operator clicks it, rather than
    # after the automation has already touched Fakturama.
    issues = [i.to_dict() for i in validate(data)]
    return render_template("automation.html", order_id=order_id, order=data,
                           automation=state, issues=issues,
                           busy=_active_order not in (None, order_id))


@app.route("/automation/doctor", methods=["POST"])
def automation_doctor():
    from automation.doctor import run_doctor
    return jsonify(run_doctor().to_dict())


if __name__ == "__main__":
    app.run(debug=True, port=5000)