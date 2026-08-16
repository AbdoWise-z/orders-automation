# Fakturama Order Automation

Turn a photo or scan of a customer order into a saved, verified **Order** and
linked **Invoice** inside [Fakturama](https://www.fakturama.info/) — without
retyping anything.

```
order image ──► OCR ──► LLM ──► review & fix ──► validate ──► drive Fakturama
                (layout)  (schema)   (browser)    (pre-flight)   (UI automation)
```

Two halves that can be used independently:

| Half | What it does | Runs on |
|---|---|---|
| **Extraction** | Image → structured order JSON, reviewed in a browser | any OS |
| **Automation** | Order JSON → Fakturama, driven through its real UI | Windows only |

---

## 1. Setup

**You need:** Python 3.10+ (developed on 3.12), and for the automation half:
Windows, Fakturama 2.x, and the Tesseract OCR **binary**.

```bash
pip install -r requirements.txt
```

**Tesseract** (automation only) — Fakturama's grids are canvases that expose no
rows to UI Automation, so they are read by screenshot + OCR. Install the binary
from [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki).
It's auto-detected at the standard install path; set `TESSERACT_CMD` only if
yours lives elsewhere.

### Configuration — `.env`

```ini
OCR_PROVIDER=hf_public          # hf_public (default) | hf_private
HF_TOKEN=...                    # optional, for the public Space
HF_OCR_SPACE=...                # optional, override the Space id

HF_PRIVATE_OCR_URL=...          # only for OCR_PROVIDER=hf_private
HF_PRIVATE_OCR_ENDPOINT=...
HF_PRIVATE_OCR_API_KEY=...

LLM_PROVIDER=anthropic          # anthropic (default) | openai
ANTHROPIC_API_KEY=...           # read by the SDK itself
# ANTHROPIC_LLM=...             # optional model override

# for LLM_PROVIDER=openai (also any OpenAI-compatible endpoint)
# OPENAI_API_KEY=...
# OPENAI_BASEURL=...
# OPENAI_LLM=...
# OPENAI_PROVIDER_NAME=...

# TESSERACT_CMD=C:/Program Files/Tesseract-OCR/tesseract.exe
```

> ⚠️ **Windows paths in `.env` need forward slashes or single quotes.**
> `python-dotenv` expands backslash escapes, so
> `TESSERACT_CMD=C:\...\tesseract.exe` becomes `C:\...⇥esseract.exe` — the `\t`
> turns into a TAB. Use `C:/Program Files/...` or `'C:\Program Files\...'`.
> The config layer repairs this specific case and logs when it does, but the
> same trap applies to any `\t`, `\n`, `\b`, `\r` in a path.

**`OCR_PROVIDER=local`** (PaddleOCR running on your own machine) is
**deliberately disabled** — its runtime is a very large install, and keeping it
opt-in means the app works without it. To enable: uncomment `paddleocr` in
`requirements.txt`, then `LocalOCRProvider` in `providers/ocr/__init__.py` and
`providers/ocr/factory.py`.

---

## 2. Using it

```bash
python app.py          # http://localhost:5000
```

**From an image** — upload → OCR + LLM extract → **review page** (every field
editable; this is where you fix extraction mistakes) → *Save & begin automation*.

**From existing JSON** — upload a `.json` record on the home page, or pick any
previous order from the **Recent orders** list. Useful for replaying a run
without paying for OCR again. Any order can be downloaded as JSON and
re-uploaded later.

**Running the automation** — on the automation page pick how far to go, then
**Run**:

| Stop after | Covers |
|---|---|
| `header` | §1 — open Order, set Date / Cust.Ref. / price mode |
| `debtor` | + §2 — select or create the Debtor |
| `product` | + §3 — select or create each Product, fill the line items |
| `order` | + §4 — verify totals, save, confirm in Documents |
| `invoice` | + §5 — linked Invoice, payment, paid state, verify |

> Automation drives the **real mouse and keyboard**. Keep Fakturama visible and
> don't use the machine while it runs. Runs are serialised behind a lock — a
> second run is refused, not queued.

**Check grounding** on the same page runs a read-only probe that confirms every
control the flows depend on is findable in *this* Fakturama session. Worth
doing after a Fakturama upgrade.

### From a REPL

```python
from automation.runner import run_automation
from app import load_order

run_automation(load_order("be7e9340c4ea"), stop_after="order")
```

---

## 3. Architecture

```
app.py                  Flask: upload, review, automation routes, run lock
extraction/             OCR text + LLM → order schema
providers/
  ocr/                  hf_public | hf_private | local (disabled)
  llm/                  anthropic | openai-compatible
automation/
  config.py             settings + .env loading + Tesseract resolution
  normalization.py      canonicalise values (dates, countries, money, codes)
  validation.py         pre-flight checks — blocking vs advisory
  models.py             typed read-only view over the JSON + order arithmetic
  runner.py             orchestrates the flow, records steps/warnings
  session.py            owns the Fakturama window and its editors
  uia.py                the only module that touches UI Automation
  widgets.py            semantic helpers ("the edit labelled Street")
  flows/
    order.py            §1 header, §4 totals/save/Documents/follow-up
    debtor.py           §2 select-or-create debtor, terms of payment
    product.py          §3 select-or-create product, VAT, line items
    invoice.py          §5 linked invoice, payment method, paid state
    selectors.py        shared search-dialog + all OCR of Fakturama's grids
```

### Why it's shaped this way

**One module touches UI Automation.** Everything else asks in semantic terms
(`widgets.set_labelled(ed, "Street", ...)`) or flow intent. When Fakturama's
tree surprises us, the fix lands in one place.

**Never ground on `AutomationId`.** In this SWT build it's the window handle in
decimal — it changes every launch. Grounding is by `Name`, control type, and
structural/geometric position.

**Drive with real input, not UIA patterns.** SWT/JFace only commits a bound
value when a real input event fires. Text is set by clipboard paste, combo
items are *physically clicked* (never `SelectionItemPattern.Select()`), and
focus is moved off afterwards. A pattern-level select updates what a combo
*displays* while the model keeps its old value — which is exactly how an order
once verified as `Net` and still saved as `Gross`.

**Verify by read-back, never by "no exception".** Every write is read back.

### The OCR-of-the-screen problem

Fakturama's grids — the item lines, the selector dialogs, the Documents list —
are NatTable canvases with **no rows in the accessibility tree at all**. They
are read by screenshotting the control and running Tesseract over it.

That is inherently unreliable, so it's used carefully:

- **Columns are anchored, not counted.** Header words are fuzzy-matched onto
  the known column order and unmatched tokens ignored — otherwise a column
  border read as `l`, or `Item No.` splitting into two words, shifts every
  index and a click aimed at VAT lands on Description.
- **Cells are clicked and read over UIA where it matters.** Clicking an item
  cell spawns a real edit control whose value can be read exactly, so line VAT
  is verified that way rather than from OCR text.
- **Filters do the matching where possible.** The Documents check searches for
  the document number rather than OCR-ing it — Tesseract reads `PO000004` as
  `@` or `HPOO0000` in every page-segmentation mode.
- **Unreadable ≠ wrong.** Post-save Documents checks are reported as
  *warnings*, never failures. See below.

### Validation, warnings, and stopping safely

| Outcome | Meaning |
|---|---|
| `invalid` | Pre-flight found blocking problems — **nothing was touched** |
| `manual_review` | A deliberate, safe stop; Fakturama is left consistent |
| `error` | Something genuinely went wrong |
| `ok` (+ warnings) | Completed; some post-hoc check couldn't be *confirmed* |

**Pre-flight validation runs before Fakturama is touched at all.** A missing
payment method would otherwise die at §2.10, `PAID` with no date at §5.3, and a
SKU-less item risks creating a duplicate product at §3.3 — each *after*
Fakturama already holds a half-built order that has to be unwound by hand.

**Normalization** runs before anything touches Fakturama and is deliberately
conservative: it fixes what's unambiguous (ISO dates, `DE` → `Germany`,
`bezahlt` → `PAID`, `1.234,56` → `1234.56`) and *flags* what isn't. A
`discount_pct` of `0.1` is never silently read as 10% — guessing there changes
what a customer is billed.

Payment methods are mapped to Fakturama's payment-code labels up front
(§2.10.4) — `Bank Transfer` → `Credit transfer`, `Credit Card` → `Credit card`,
`SEPA Direct Debit` → `SEPA direct debit`. Doing it *before* the run matters:
the automation creates a term of payment named after this value, so
normalising later would leave the dropdown accumulating raw extracted wording.
The mapping is idempotent, and an unmapped method (e.g. `PayPal`) passes
through unchanged with a warning.

---

## 4. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Tesseract OCR not found at …` | Install the binary; check the `.env` backslash trap above. The resolver logs which path it fell back to. |
| `ValueError: Unknown OCR_PROVIDER 'local'` | `local` is disabled on purpose — see §1. |
| Run refused, "already running" | Desktop control is exclusive; wait for the active run. |
| `ControlNotFound` after a Fakturama upgrade | Run **Check grounding**; labels or structure moved. |
| Grid clicks land one column off | Header OCR misfired — the log prints the anchored columns and warns when it falls back to cached positions. |
| Order saved as `Gross`, wrong Date, empty Cust.Ref. | Selecting a Debtor re-applies that contact's defaults. The header is deliberately re-applied immediately before save. |

Failure screenshots land in `data/automation_shots/`; every OCR capture is
saved there too, which is usually the fastest way to see what Tesseract saw.

---

## 5. Known limitations

- **§4.1 / §5.1 deep re-verification is not implemented.** Values are verified
  as they're entered (exact-match debtor rule, per-line VAT read-back, order
  totals), so a second OCR-based pass would add unreliability, not assurance.
- **The payment-code dropdown is grounded on an untranslated i18n key.**
  Fakturama renders that label literally as `!editorPaymentPaymentcode!`, and
  its accessible name matches — so that is what the automation looks for. If a
  future release fixes the translation, `_PAYMENT_CODE_LABEL` in
  `flows/debtor.py` has to change with it. (The terms-of-payment editor writes
  every field *before* its single Save, so an unresolved label aborts unsaved
  rather than leaving a half-configured term behind.)
- **Delivery-address role assignment (§2.8)** needs a capture of the role picker.
- **The Documents State column** is effectively unreadable by Tesseract, so
  "open"/"paid" confirmations are advisory.
- **Reading Fakturama's H2 database directly would be far more reliable** than
  OCR for all verification steps, and is the recommended upgrade path.
