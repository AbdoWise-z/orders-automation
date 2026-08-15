import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for

from extraction import extract_order_fields, run_ocr
from schema import empty_order

load_dotenv()

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


@app.route("/")
def index():
    return render_template("upload.html")


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

    record = empty_order()
    record["order"].update(extracted.get("order") or {})
    record["debtor"].update(extracted.get("debtor") or {})
    if extracted.get("debtor", {}).get("billing_address"):
        record["debtor"]["billing_address"].update(extracted["debtor"]["billing_address"])
    if extracted.get("debtor", {}).get("delivery_address"):
        record["debtor"]["delivery_address"].update(extracted["debtor"]["delivery_address"])
    record["items"] = extracted.get("items") or []
    record["_meta"] = {
        "order_id": order_id,
        "source_image": image_path.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "extracted",
    }
    save_order(order_id, record)
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


if __name__ == "__main__":
    app.run(debug=True, port=5000)