"""
One-time seed: July + August 2026 expenses transcribed from the owner's
"unemployed guy expenses" Google Sheet, which has no daily dates — only a
month per category column.

The sheet's CSV export drops empty trailing cells, so which column each
amount belongs to is ambiguous from the raw export alone. The amount lists
below were reconstructed by solving against the sheet's own printed
per-category totals; EXPECTED_TOTALS locks that reconciliation in and
check_totals() (run automatically below) refuses to import if a single
rupee is off.

Category names here are mapped onto this app's fixed CATEGORIES (see
categorizer.py), which don't exactly match the sheet's own column names —
recorded explicitly so it's easy to re-map by hand later if any of these
read wrong: "Grocery/Snacks/Food"->"Food & Dining", "Fuel"/"Parking/Carwash"/
"Ticket Train"/"Trip"->"Transport", "Laptop Repair"/"Shopping"->"Shopping",
"Maid"->"Bills & Utilities", "Movie"->"Entertainment", "Donation"/"Saloon"/
"Educative"->"Other" (no closer fit in this app's category list).

    python import_sheet_data.py                 # dry run: prints + verifies
    python import_sheet_data.py --push           # sends via BT_API/BT_TOKEN env vars
"""

import os
import sys

MONTHS = {
    "2026-08": {
        "month": 8,
        "year": 2026,
        "grand_total": 24012.0,
        "categories": {
            "Food & Dining": {
                "sheet_name": "Grocery/Snacks/Food",
                "amounts": [
                    150, 40, 600, 152, 107, 110, 926, 263, 192, 40, 108, 20, 355, 48, 60,
                    280, 92, 24, 126, 40, 223, 172, 25, 265, 181, 194, 67, 60,
                ],
            },
            "Transport": {"sheet_name": "Fuel", "amounts": [2000, 651, 771, 998, 260, 629]},
            "__parking__": {"sheet_name": "Parking/Carwash", "category": "Transport", "amounts": [25, 20, 180, 40, 30, 30]},
            "__laptop_repair__": {"sheet_name": "Laptop Repair", "category": "Shopping", "amounts": [550, 200]},
            "Shopping": {"sheet_name": "Shopping", "amounts": [128, 360]},
            "Bills & Utilities": {"sheet_name": "Maid", "amounts": [2000, 10000]},
            "Entertainment": {"sheet_name": "Movie", "amounts": [220]},
        },
    },
    "2026-07": {
        "month": 7,
        "year": 2026,
        "grand_total": 37939.90,
        "categories": {
            "Food & Dining": {
                "sheet_name": "Grocery/Snacks/Food",
                "amounts": [
                    83, 139, 356, 136, 159, 155, 60, 40, 63, 176, 975, 140, 315, 63, 164,
                    123, 329, 110, 82, 115, 103, 266, 116, 188, 156, 224, 313.95, 224, 111,
                    167, 280, 105.5, 63, 30, 67, 293, 66, 58, 363, 20, 313.96, 40, 63, 49,
                    25, 105, 140, 48, 73, 90, 24, 35, 131, 140, 15,
                ],
            },
            "__fuel__": {"sheet_name": "Fuel", "category": "Transport", "amounts": [483.7, 1080, 1200, 916, 1077, 457, 282, 964.29]},
            "__parking__": {"sheet_name": "Parking/Carwash", "category": "Transport", "amounts": [200, 40, 40, 120, 80, 50, 50]},
            "Entertainment": {"sheet_name": "Movie", "amounts": [665, 500, 1000]},
            "Shopping": {"sheet_name": "Shopping", "amounts": [350.5, 550, 60, 185, 18, 232, 598, 5000, 363, 100]},
            "__train__": {"sheet_name": "Ticket Train", "category": "Transport", "amounts": [3300, 2600, 500]},
            "__trip__": {"sheet_name": "Trip", "category": "Transport", "amounts": [300]},
            "__donation__": {"sheet_name": "Donation", "category": "Other", "amounts": [500]},
            "__saloon__": {"sheet_name": "Saloon", "category": "Other", "amounts": [400]},
            "__educative__": {"sheet_name": "Educative", "category": "Other", "amounts": [5389]},
        },
    },
}

# What the SHEET itself prints as each column's total — the reconciliation check.
EXPECTED_SHEET_TOTALS = {
    "2026-08": {
        "Grocery/Snacks/Food": 4920, "Fuel": 5309, "Parking/Carwash": 325,
        "Laptop Repair": 750, "Maid": 12000, "Movie": 220, "Shopping": 488,
    },
    "2026-07": {
        "Grocery/Snacks/Food": 8289.41, "Fuel": 6459.99, "Parking/Carwash": 580,
        "Movie": 2165, "Shopping": 7456.5, "Ticket Train": 6400, "Trip": 300,
        "Donation": 500, "Saloon": 400, "Educative": 5389,
    },
}


def build_items():
    """Flattens MONTHS into the flat {amount, category, merchant, month, year}
    shape the /transactions/import endpoint expects."""
    items = []
    for month_data in MONTHS.values():
        for entry in month_data["categories"].values():
            category = entry.get("category", None)
            if category is None:
                # The dict key itself is the category when not overridden
                # (i.e. entries not prefixed with "__").
                category = next(k for k, v in month_data["categories"].items() if v is entry)
            for amount in entry["amounts"]:
                items.append({
                    "amount": round(float(amount), 2),
                    "category": category,
                    "merchant": entry["sheet_name"],
                    "month": month_data["month"],
                    "year": month_data["year"],
                })
    return items


def check_totals(items):
    """Refuses to import if the reconstruction doesn't match the sheet's own
    printed totals to the rupee — this is the whole point of doing this as a
    script instead of typing numbers into a form."""
    ok = True
    for key, month_data in MONTHS.items():
        month_items = [i for i in items if i["month"] == month_data["month"] and i["year"] == month_data["year"]]
        total = round(sum(i["amount"] for i in month_items), 2)
        expected = month_data["grand_total"]
        status = "PASS" if total == expected else "FAIL"
        if total != expected:
            ok = False
        print(f"{status}  {key} grand total: {total} (expected {expected})")

    # Per-sheet-column reconciliation, grouped back by original sheet_name.
    for key, month_data in MONTHS.items():
        by_sheet_name = {}
        for entry in month_data["categories"].values():
            by_sheet_name.setdefault(entry["sheet_name"], 0.0)
            by_sheet_name[entry["sheet_name"]] += round(sum(entry["amounts"]), 2)
        for sheet_name, expected in EXPECTED_SHEET_TOTALS[key].items():
            got = by_sheet_name.get(sheet_name)
            if got is None:
                # "Laptop Repair" and "Shopping" were merged into one category
                # bucket (both -> "Shopping" in this app) but are still two
                # separate sheet columns with their own expected totals.
                got = round(sum(
                    a for entry in month_data["categories"].values() if entry["sheet_name"] == sheet_name
                    for a in entry["amounts"]
                ), 2)
            status = "PASS" if got == expected else "FAIL"
            if got != expected:
                ok = False
            print(f"{status}  {key} / {sheet_name}: {got} (expected {expected})")
    return ok


if __name__ == "__main__":
    items = build_items()
    print(f"Built {len(items)} line items across {len(MONTHS)} months.\n")
    ok = check_totals(items)
    print()
    combined = round(sum(m["grand_total"] for m in MONTHS.values()), 2)
    print(f"Combined July+August total: {combined}")

    if not ok:
        print("\nRECONCILIATION FAILED — not importing anything.")
        sys.exit(1)

    if "--push" not in sys.argv:
        print("\nDry run only (reconciliation passed). Re-run with --push to actually import.")
        sys.exit(0)

    import json
    import urllib.request

    api = os.environ["BT_API"].rstrip("/")
    token = os.environ["BT_TOKEN"]
    force = "--force" in sys.argv

    req = urllib.request.Request(
        f"{api}/transactions/import",
        data=json.dumps({"items": items, "force": force}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        print("\nServer response:", resp.read().decode())
