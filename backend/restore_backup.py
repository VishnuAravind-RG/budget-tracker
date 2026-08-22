"""Restore a backup produced by GET /export/all.

    python restore_backup.py backup.json --dry-run
    python restore_backup.py backup.json --into sqlite:///./restored.db
    python restore_backup.py backup.json --into "$DATABASE_URL" --replace

The nightly workflow (.github/workflows/backup.yml) stores an encrypted
snapshot every night. That is only half a backup: an archive nobody has ever
restored is a guess, not insurance. This is the other half, and it is meant to
be run once on a throwaway database *before* it is ever needed for real.

Decrypt the artifact first:

    openssl enc -d -aes-256-cbc -pbkdf2 -in backup.json.enc -out backup.json

Safety, because this writes over real money data:

- Refuses to touch a database that already holds transactions unless
  --replace is passed explicitly. Restoring on top of live data would
  otherwise double every figure, which is the exact class of bug this
  project keeps having to fix.
- --replace empties the tables it is about to restore, and says how many rows
  it is deleting before it does. It does not touch tables absent from the
  backup — notably `gmail_auth`, which is deliberately never exported (it
  holds an OAuth refresh token, a credential rather than data), so a restore
  leaves an existing Gmail connection alone rather than destroying it.
- --dry-run reports exactly what would happen and writes nothing.
"""

import argparse
import json
import os
import sys
from datetime import datetime

# Import order matters: db.py reads DATABASE_URL at import time, so --into has
# to be in the environment before that happens.
_args_target = None
for i, arg in enumerate(sys.argv):
    if arg == "--into" and i + 1 < len(sys.argv):
        _args_target = sys.argv[i + 1]
if _args_target:
    os.environ["DATABASE_URL"] = _args_target

import db  # noqa: E402
from models import Budget, FuelFill, LendingReminder, Payee, Todo, Transaction, Vehicle  # noqa: E402

# Order is the order they are written in. No foreign keys are declared, so this
# is for readability rather than correctness.
TABLES = [
    ("transactions", Transaction),
    ("budgets", Budget),
    ("payees", Payee),
    ("vehicles", Vehicle),
    ("fuel_fills", FuelFill),
    ("todos", Todo),
    ("lending_reminders", LendingReminder),
]


def _to_python(model, column_name: str, value):
    """Turn one exported JSON value back into what the column expects.

    Datetimes are the only real work: they were serialised as ISO text with a
    trailing "Z", and everything in this app stores naive UTC (see
    timeutil.py). Handing SQLAlchemy the string instead would store text in a
    DATETIME column, which SQLite accepts silently and Postgres rejects —
    the sort of difference that only shows up when you actually need the
    restore to work.
    """
    if value is None:
        return None
    column = model.__table__.columns[column_name]
    if column.type.python_type is datetime and isinstance(value, str):
        return datetime.fromisoformat(value.rstrip("Z"))
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a /export/all backup.")
    parser.add_argument("backup", help="Path to the decrypted backup.json")
    parser.add_argument("--into", help="SQLAlchemy URL to restore into (default: $DATABASE_URL)")
    parser.add_argument("--replace", action="store_true",
                        help="Empty the target's tables first. Required if it already holds data.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen; write nothing.")
    args = parser.parse_args()

    with open(args.backup, encoding="utf-8") as handle:
        data = json.load(handle)

    missing = [name for name, _ in TABLES if name not in data]
    if missing:
        print(f"ERROR: this file is missing {', '.join(missing)} — is it a /export/all backup?")
        return 1

    # A backup can be valid JSON and still be worthless. The workflow refuses
    # to store an empty one for the same reason: it looks like a successful
    # run while recording nothing, and it would be the file someone reaches
    # for after losing the real data.
    if not data["transactions"]:
        print("ERROR: this backup contains no transactions — refusing to restore it.")
        return 1

    counts = data.get("counts", {})
    for name, _ in TABLES:
        stated, actual = counts.get(name), len(data[name])
        if stated is not None and stated != actual:
            print(f"ERROR: {name} says {stated} rows but carries {actual} — the file is truncated.")
            return 1

    print(f"Backup taken {data.get('exported_at', 'at an unknown time')}")
    print(f"Target       {db.DATABASE_URL.split('@')[-1]}")  # never print credentials
    for name, _ in TABLES:
        print(f"  {name:20} {len(data[name]):>5} rows")

    # Anyone running this is having a bad day already. A wrong path or an
    # unreachable host should say so in one line, not in sixty lines of
    # SQLAlchemy traceback that buries the actual problem.
    try:
        db.init_db()
    except Exception as exc:  # noqa: BLE001 — any driver's failure, same advice
        print(f"\nERROR: couldn't open the target database.\n  {type(exc).__name__}: {exc}")
        print("\nCheck --into. A local file needs an absolute path that already exists, e.g.")
        print(r'  --into "sqlite:///C:/temp/restored.db"   (Windows)')
        print('  --into "sqlite:////tmp/restored.db"       (Linux/macOS)')
        return 1

    session = db.SessionLocal()
    try:
        existing = {name: session.query(model).count() for name, model in TABLES}
        occupied = {name: n for name, n in existing.items() if n}

        if occupied and not args.replace:
            print("\nThe target already holds data:")
            for name, n in occupied.items():
                print(f"  {name:20} {n:>5} rows")
            print("\nRefusing to restore on top of it — that would double every figure.")
            print("Pass --replace to empty these tables first, or point --into at an empty database.")
            return 1

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            if occupied:
                print(f"Would have deleted {sum(occupied.values())} existing rows first.")
            return 0

        if occupied:
            print(f"\nDeleting {sum(occupied.values())} existing rows...")
            for name, model in reversed(TABLES):
                session.query(model).delete()

        written = 0
        for name, model in TABLES:
            columns = {c.name for c in model.__table__.columns}
            for row in data[name]:
                # Ignore any column this build doesn't have, so a backup taken
                # by a newer version still restores into an older one rather
                # than crashing on an unknown field.
                session.add(model(**{
                    key: _to_python(model, key, value)
                    for key, value in row.items() if key in columns
                }))
                written += 1
        session.commit()
        print(f"\nRestored {written} rows.")

        for name, model in TABLES:
            got = session.query(model).count()
            flag = "" if got == len(data[name]) else "  <-- MISMATCH"
            print(f"  {name:20} {got:>5} rows{flag}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
