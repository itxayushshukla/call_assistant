import json
import os
import re
import sys
import csv
import argparse
from datetime import date
from urllib.parse import urlencode

from dotenv import load_dotenv
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

load_dotenv()  # reads .env in the same folder

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
BASE_URL = (os.getenv("BASE_URL") or "").rstrip("/")

STUDENTS_FILE = "students.json"
LOG_FILE = "call_log.csv"

REQUIRED_STUDENT_FIELDS = [
    "id", "name", "phone", "parent_name", "class", "batch", "subject", "teacher"
]

# Basic E.164 check: + followed by 8-15 digits
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def load_students():
    if not os.path.exists(STUDENTS_FILE):
        print(f"Could not find {STUDENTS_FILE}. Create it first.")
        sys.exit(1)
    try:
        with open(STUDENTS_FILE, "r", encoding="utf-8") as f:
            students = json.load(f)
    except json.JSONDecodeError as e:
        print(f"{STUDENTS_FILE} is not valid JSON: {e}")
        sys.exit(1)

    if not isinstance(students, list) or not students:
        print(f"{STUDENTS_FILE} must contain a non-empty list of student records.")
        sys.exit(1)

    seen_ids = set()
    valid = []
    for i, s in enumerate(students):
        missing = [f for f in REQUIRED_STUDENT_FIELDS if f not in s or s[f] in (None, "")]
        if missing:
            print(f"Skipping record #{i} (missing fields: {', '.join(missing)}): {s}")
            continue
        if s["id"] in seen_ids:
            print(f"Skipping record #{i}: duplicate id {s['id']}.")
            continue
        if not E164_RE.match(str(s["phone"])):
            print(f"Skipping {s['name']} (id {s['id']}): phone '{s['phone']}' is not a valid "
                  f"E.164 number, e.g. +919876543210.")
            continue
        seen_ids.add(s["id"])
        valid.append(s)

    if not valid:
        print("No valid student records found after validation. Fix students.json and retry.")
        sys.exit(1)

    return valid


def display_roster(students):
    print("\n=== Today's Roster ===")
    for s in students:
        print(f"{s['id']}. {s['name']}  |  Class {s['class']} - {s['batch']}  |  {s['subject']} ({s['teacher']})")
    print()


def get_absent_ids(students):
    valid_ids = {s["id"] for s in students}
    raw = input("Enter S.No of absent student(s), comma-separated (e.g. 1,3,4): ").strip()
    if not raw:
        return []
    ids = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece.isdigit():
            print(f"  Skipping invalid entry: '{piece}'")
            continue
        sid = int(piece)
        if sid not in valid_ids:
            print(f"  No student with S.No {sid}, skipping.")
            continue
        if sid in ids:
            print(f"  S.No {sid} entered twice, skipping duplicate.")
            continue
        ids.append(sid)
    return ids


def check_credentials(dry_run):
    required = [
        ("TWILIO_ACCOUNT_SID", TWILIO_ACCOUNT_SID),
        ("TWILIO_AUTH_TOKEN", TWILIO_AUTH_TOKEN),
        ("TWILIO_PHONE_NUMBER", TWILIO_PHONE_NUMBER),
        ("BASE_URL", BASE_URL),
    ]
    missing = [name for name, val in required if not val]
    if missing and not dry_run:
        print("Missing required environment variables: " + ", ".join(missing))
        print("Copy env.example to .env and fill in your credentials.")
        sys.exit(1)
    if BASE_URL and not (BASE_URL.startswith("http://") or BASE_URL.startswith("https://")):
        print(f"BASE_URL '{BASE_URL}' looks invalid - it should start with http:// or https://")
        sys.exit(1)


def trigger_call(client, student):
    """Trigger a Twilio call into the local NA_Bot Flask voice webhook,
    passing student context as query params so it can personalize
    the conversation (student_name, subject, teacher, etc.)."""
    params = {
        "student_name": student["name"],
        "parent_name": student["parent_name"],
        "class": student["class"],
        "batch": student["batch"],
        "subject": student["subject"],
        "teacher": student["teacher"],
        "date": str(date.today()),
    }
    voice_url = f"{BASE_URL}/twilio/voice?{urlencode(params)}"

    call = client.calls.create(
        to=student["phone"],
        from_=TWILIO_PHONE_NUMBER,
        url=voice_url,
        status_callback=f"{BASE_URL}/twilio/status",
        status_callback_event=["completed"],
        status_callback_method="POST",
    )
    return call


def log_call(student, call_sid, status):
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["date", "student_name", "phone", "call_sid", "status"])
        writer.writerow([str(date.today()), student["name"], student["phone"], call_sid, status])


def main():
    parser = argparse.ArgumentParser(description="Mark students absent and trigger NA_Bot parent calls.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Preview roster/selection and print what would be called, without placing real calls.")
    args = parser.parse_args()

    check_credentials(args.dry_run)
    students = load_students()
    display_roster(students)

    absent_ids = get_absent_ids(students)
    if not absent_ids:
        print("No students marked absent. Exiting.")
        return

    by_id = {s["id"]: s for s in students}
    selected = [by_id[sid] for sid in absent_ids]

    print("\n=== About to call ===")
    for s in selected:
        print(f"  {s['name']} -> {s['parent_name']} at {s['phone']}")

    if args.dry_run:
        print("\n[DRY RUN] No calls placed.")
        return

    confirm = input(f"\nPlace {len(selected)} real call(s) now? (y/n): ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Cancelled. No calls placed.")
        return

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    print("\n=== Triggering calls ===")
    for student in selected:
        print(f"{student['name']} marked absent -> calling {student['parent_name']} at {student['phone']}...")
        try:
            call = trigger_call(client, student)
            print(f"  Call started. SID: {call.sid}")
            log_call(student, call.sid, "initiated")
        except TwilioRestException as e:
            print(f"  Twilio error for {student['name']}: {e}")
            log_call(student, "N/A", f"failed: {e}")
        except Exception as e:
            print(f"  Failed to call {student['name']}: {e}")
            log_call(student, "N/A", f"failed: {e}")

    print(f"\nDone. Log saved to {LOG_FILE}.")


if __name__ == "__main__":
    main()
