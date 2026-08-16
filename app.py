import csv
import os
from datetime import date
from urllib.parse import urlencode

from dotenv import load_dotenv
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather

load_dotenv()  # reads .env in the same folder (PORT, FLASK_DEBUG)

app = Flask(__name__)

LOG_FILE = "call_log.csv"

# Language for Twilio speech recognition. Change to "hi-IN" or "mr-IN"
# if parents mostly respond in Hindi/Marathi. Twilio auto-detects fairly
# well within one language at a time; it can't auto-switch mid-call.
SPEECH_LANGUAGE = "en-IN"

# Keyword buckets -> matches NA_Bot's example reasons
REASON_KEYWORDS = {
    "Illness":         ["fever", "sick", "ill", "not well", "unwell", "cold", "cough", "headache"],
    "Family Function": ["function", "wedding", "family", "marriage", "ceremony"],
    "Travelling":      ["travel", "traveling", "travelling", "out of town", "trip", "outstation"],
    "Exam":            ["exam", "test", "school exam"],
    "Personal Work":   ["work", "personal", "some work", "urgent work"],
    "Forgot":          ["forgot", "forget"],
    "Emergency":       ["emergency", "urgent", "hospital", "accident"],
}

YES_WORDS = ["yes", "yeah", "yep", "sure", "correct", "speaking", "ha", "haan"]
NO_WORDS = ["no", "nope", "wrong", "not", "nahi"]


def match_reason(text):
    text = (text or "").lower()
    for label, keywords in REASON_KEYWORDS.items():
        if any(k in text for k in keywords):
            return label
    return f"Other: {text.strip()}" if text.strip() else "Unclear response"


def match_yes_no(text):
    text = (text or "").lower()
    if any(w in text for w in YES_WORDS):
        return "Yes"
    if any(w in text for w in NO_WORDS):
        return "No"
    return "Not Sure"


def carry_params(extra=None):
    """Carry student context + collected answers forward through each step's URL."""
    params = {k: v for k, v in request.values.items() if k not in ("SpeechResult", "Confidence")}
    if extra:
        params.update(extra)
    return urlencode(params)


def log_call(params, status):
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "date", "student_name", "parent_name", "call_status",
                "reason", "attend_next_class", "language_used"
            ])
        writer.writerow([
            str(date.today()),
            params.get("student_name", ""),
            params.get("parent_name", ""),
            status,
            params.get("reason", ""),
            params.get("attend_next", ""),
            SPEECH_LANGUAGE,
        ])


# ---------------------------------------------------------------------------
# Step 1: Greet + confirm parent
# ---------------------------------------------------------------------------
@app.route("/twilio/voice", methods=["GET", "POST"])
def voice():
    parent_name = request.values.get("parent_name", "there")

    vr = VoiceResponse()
    gather = Gather(
        input="speech",
        action=f"/twilio/confirm?{carry_params()}",
        method="POST",
        speech_timeout="auto",
        language=SPEECH_LANGUAGE,
    )
    gather.say(f"Hello, may I speak with {parent_name}?", voice="Polly.Aditi")
    vr.append(gather)
    # If no speech detected, retry once then end
    vr.say("Sorry, I didn't catch that. Goodbye.", voice="Polly.Aditi")
    vr.hangup()
    return Response(str(vr), mimetype="text/xml")


# ---------------------------------------------------------------------------
# Step 2: Handle parent confirmation -> inform absence -> ask reason
# ---------------------------------------------------------------------------
@app.route("/twilio/confirm", methods=["GET", "POST"])
def confirm():
    speech = request.values.get("SpeechResult", "")
    answer = match_yes_no(speech)
    student_name = request.values.get("student_name", "your child")
    subject = request.values.get("subject", "class")

    vr = VoiceResponse()

    if answer == "No":
        vr.say(
            "My apologies. Could you please ask the parent or guardian to call us back "
            "at the academy office? Thank you. Goodbye.",
            voice="Polly.Aditi",
        )
        log_call(dict(request.values), "Wrong Person")
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # Treat "Yes" or "Not Sure" as proceed (per spec: don't force, but move forward politely)
    gather = Gather(
        input="speech",
        action=f"/twilio/reason?{carry_params()}",
        method="POST",
        speech_timeout="auto",
        language=SPEECH_LANGUAGE,
    )
    gather.say(
        f"Good evening. I'm calling from National Academy regarding {student_name}. "
        f"Our attendance records show that {student_name} was absent from today's {subject} class. "
        f"May I know the reason for today's absence?",
        voice="Polly.Aditi",
    )
    vr.append(gather)
    # This only plays if Twilio's Gather actually times out with no speech
    vr.say("Sorry, I didn't catch that. We'll follow up later. Goodbye.", voice="Polly.Aditi")
    vr.hangup()
    return Response(str(vr), mimetype="text/xml")


# ---------------------------------------------------------------------------
# Step 3: Handle reason -> acknowledge -> ask about next class
# ---------------------------------------------------------------------------
@app.route("/twilio/reason", methods=["GET", "POST"])
def reason():
    speech = request.values.get("SpeechResult", "")
    reason_label = match_reason(speech)
    student_name = request.values.get("student_name", "your child")

    acknowledgements = {
        "Illness": "I'm sorry to hear that. We hope they recover soon.",
        "Exam": "Thank you for letting us know. Best wishes for the examination.",
        "Emergency": "I understand. We hope everything is alright.",
    }
    ack = acknowledgements.get(reason_label, "Thank you for letting us know.")

    vr = VoiceResponse()
    gather = Gather(
        input="speech",
        action=f"/twilio/nextclass?{carry_params({'reason': reason_label})}",
        method="POST",
        speech_timeout="auto",
        language=SPEECH_LANGUAGE,
    )
    gather.say(
        f"{ack} Will {student_name} be able to attend the next scheduled class?",
        voice="Polly.Aditi",
    )
    vr.append(gather)
    # This only plays if Twilio's Gather actually times out with no speech
    vr.say("Sorry, I didn't catch that. We'll follow up later. Goodbye.", voice="Polly.Aditi")
    vr.hangup()
    return Response(str(vr), mimetype="text/xml")


# ---------------------------------------------------------------------------
# Step 4: Handle next-class answer -> close politely -> log
# ---------------------------------------------------------------------------
@app.route("/twilio/nextclass", methods=["GET", "POST"])
def nextclass():
    speech = request.values.get("SpeechResult", "")
    attend_next = match_yes_no(speech)

    vr = VoiceResponse()
    vr.say(
        "Thank you for your time. Your response has been recorded. Have a wonderful day.",
        voice="Polly.Aditi",
    )
    vr.hangup()

    log_call({**dict(request.values), "attend_next": attend_next}, "Completed")
    return Response(str(vr), mimetype="text/xml")


# ---------------------------------------------------------------------------
# Optional: Twilio status callback, if you wire it up in mark_absent.py later
# ---------------------------------------------------------------------------
@app.route("/twilio/status", methods=["POST"])
def status_callback():
    call_status = request.values.get("CallStatus", "unknown")
    log_call(dict(request.values), f"Status: {call_status}")
    return ("", 204)


if __name__ == "__main__":
    # DEBUG is off by default. Only enable it locally (never when exposed via ngrok),
    # by setting FLASK_DEBUG=1 in your .env - Flask's debug mode allows arbitrary
    # code execution via the browser if the app is reachable from the internet.
    debug_mode = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=debug_mode)
