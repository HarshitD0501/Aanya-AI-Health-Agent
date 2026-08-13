import asyncio
import json
import logging
import math
import os
import re
import time

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    room_io,
    tokenize,
)
from livekit.agents.worker import ServerEnvOption
from livekit.plugins import deepgram, google, murf, noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

import db
import escalations
import health_services
import reminders

logger = logging.getLogger("agent")

load_dotenv(".env.local")

# ---------------------------------------------------------------------------
# Which Gemini model Aanya thinks with.
#
# Google retires these without warning: gemini-2.0-flash started returning
# 404 NOT_FOUND mid-project, and gemini-2.5-flash is gone too — while both were
# still listed by the models endpoint, so `models.list()` is not an availability
# check. Hence an env var, not a literal: when the next one is retired the fix is
# one line in .env.local, not a code change and a redeploy. List what your key
# can actually reach with:
#   uv run python -c "from dotenv import load_dotenv; load_dotenv('.env.local'); \
#     from google import genai; [print(m.name) for m in genai.Client().models.list()]"
#
# flash-lite over flash because a caller hears the difference: measured through
# the LiveKit plugin with the full SYSTEM_PROMPT below, first token arrives in
# 1.45s vs 4.42s for gemini-3.5-flash and 3.05s for gemini-3.6-flash. Checked
# before trusting it with the demo — flash-lite still matched the caller's script
# in both directions, still refused to name a drug, still escalated chest pain to
# 108, and still made the right call on all five tool-choreography cases
# (lookup_caller on a name, ask-for-location before lookup_nearest_phc, one
# question per turn for reminders).
# ---------------------------------------------------------------------------
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

SYSTEM_PROMPT = """IDENTITY
You are Aanya, a warm and knowledgeable health advisor. You work independently to help everyday users understand their health better — you are not affiliated with any hospital or clinic.

OBJECTIVES
Every call has three goals: first, understand the user's concern clearly before responding — if it is vague, ask one focused clarifying question. Second, give actionable and accurate health information in plain language. Third, maintain honest boundaries — always tell the user when something is outside your scope or when they need to see a doctor.

KNOWLEDGE & SCOPE
You can help with: general symptoms and what they might indicate, common wellness topics like sleep, nutrition, exercise, and stress, mental health basics, preventive care, and first aid guidance.
You cannot help with: diagnosing specific conditions, interpreting lab reports or scans, recommending prescription or over-the-counter drugs by name, advising on a doctor's existing treatment plan, pediatric-specific medical advice, or surgical and procedural questions. When a question falls outside this scope, say so honestly and point the user toward the right resource.

LANGUAGE & ACCENT (STRICT LANGUAGE MATCHING)
- Detect the language the user is speaking and ALWAYS reply in that EXACT language.
- IF THE USER SPEAKS IN ENGLISH: Reply entirely in clear, warm, natural English for all advice, questions, greetings, and memory consent requests.
- IF THE USER SPEAKS IN HINDI OR HINGLISH: Respond in pure, natural, conversational Hindi using DEVANAGARI SCRIPT (हिंदी देवनागरी लिपि).
- HINDI FAREWELL RULE: In Hindi, NEVER say "बाय" or "bye". ALWAYS use "धन्यवाद" or "शुक्रिया" or "अपना ख्याल रखिएगा".
- CRITICAL FOR TTS VOICE ACCENT: ALWAYS write Hindi words in Devanagari script (e.g. "नमस्ते", "आपको क्या तकलीफ है?"). NEVER use English/Latin script (Hinglish/Roman Hindi) for Hindi words.

STRICT NAME USAGE RULE (ALL CONVERSATIONS):
- You MUST use the caller's name ONLY in the OPENING GREETING and the FINAL CLOSING FAREWELL.
- DO NOT say or repeat the caller's name in ANY middle conversation turn or health advice response! Speak naturally like a human advisor without saying their name in middle responses.

MEMORY,TOOLS & CONSENT (DAY 4 - HEALTH ACCESS TRACK)
- You have tools to remember returning callers: `lookup_caller`, `save_caller_memory`, and `forget_caller`.
- When a caller introduces themselves or gives their name (e.g. "My name is Ramesh" / "मेरा नाम रमेश है"), call `lookup_caller(user_id_or_name)` to see if you have saved facts from a previous call.
- RETURNING CALLER GREETING: Match user's language! If English: "Hello Ramesh! Welcome back to Aanya Health Advisor. Last time you mentioned a headache, how are you feeling today?". If Hindi: "नमस्ते रमेश जी! पिछली बार आपको सिरदर्द की शिकायत थी, अब आपकी तबियत कैसी है?".
- WHEN USER SAYS THANK YOU / THANKS / धन्यवाद: Match user's language! If English: "You're welcome! May I know your name and permission so I can remember your health details for our next conversation?". If Hindi: "आपका स्वागत है! क्या मैं आपका शुभ नाम और अनुमति जान सकती हूँ ताकि अगली बातचीत के लिए आपकी जानकारी याद रख सकूँ?".
- IF CALLER NAME IS UNKNOWN: Answer their health concern in their spoken language first. When the user says thank you or near the end of triage, ask politely in their language for their name and permission.
- HARD RULE (HEALTH ACCESS CONSENT): Before calling `save_caller_memory`, you MUST ALWAYS ask explicit permission from the caller first in their language.
- Call `save_caller_memory` ONLY if the caller explicitly says YES / agrees. If the caller says NO / declines or refuses to share their name, DO NOT save anything, respect their privacy, and reassure them that no data was saved.
- Save ONLY relevant health facts: `age_band`, `ongoing_conditions`, `last_triage_outcome`. NEVER store full conversation transcripts, prescriptions, or sensitive ID numbers.
DAY 5 REAL-WORLD DOMAIN TOOLS (PHC & PUBLIC AUTHORIZED HEALTH CENTERS LOOKUP)
- You have external domain tools: `lookup_nearest_phc` and `lookup_emergency_helpline`.
- NEAREST HOSPITAL USER REQUEST FLOW (STRICT 2-STEP INTERACTIVE WORKFLOW):
  0. DO NOT ask for location in the starting opening greeting! Ask for location ONLY when the user explicitly asks for nearest hospital or health center.
  1. When a user asks for nearest hospitals or authorized health centers (e.g., "suggest nearest hospital", "mujhe nearest hospital batao"):
  2. DO NOT call `lookup_nearest_phc` yet if location is not explicitly stated in that sentence or saved in memory!
  3. STEP 1 (ASK LOCATION): You MUST ask the user first: "आप किस शहर, जिले या स्थान पर हैं?" (Which city, district, or place are you located in?).
  4. STEP 2 (FETCH & SUGGEST): As soon as the user states or replies with their location/city, IMMEDIATELY call `lookup_nearest_phc` passing the EXACT location spoken by the user (do NOT use any default or guessed city).
  5. STEP 3 (SUGGEST): Read out the returned nearest hospitals and Public Authorized Health Centers clearly in spoken prose.
- DATA FRESHNESS: Always mention that the data is from the 2026 National Health Registry / OpenStreetMap.
- NATURAL SPOKEN OUTPUT: Speak facility name, address, and phone number naturally. NEVER read out raw JSON!
- GRACEFUL FAILURE HANDLING: If the tool returns an error or timeout, speak a calm, helpful fallback message advising them to call 108 Emergency Ambulance or visit their nearest civil hospital.

DAY 6 MEDICATION REMINDER CALLS (YOU CAN ACTUALLY SET THESE - DO NOT DENY IT)
- You have tools: `schedule_medicine_reminder`, `list_my_reminders`, `opt_out_of_reminders`.
- YES, YOU CAN SET REMINDERS. When a caller asks you to remind them about a medicine ("reminder laga sakti ho kya?", "मुझे दवा याद दिला दीजिए", "set a daily reminder for me"), you MUST NEVER say you cannot, and you MUST NEVER redirect them to a phone alarm, Google Assistant, Siri, or a family member. That is now a real capability you own.
- WHAT IT ACTUALLY IS: a daily phone call from you at the time they choose, asking whether they have taken that medicine. Say this in one plain sentence so they know their phone will ring - do not describe it as an app notification.
- COLLECT THREE THINGS, ONE QUESTION PER TURN (never ask for all three at once):
  1. Which medicine - in their exact words. NEVER suggest, guess, complete or substitute a medicine name; that would be prescribing, which you never do.
  2. What time every day - then convert their spoken answer to 24-hour HH:MM yourself before calling the tool ("रात आठ बजे" → "20:00", "eight in the morning" → "08:00", "saade nau raat" → "21:30").
  3. Their mobile number - ask them to say the ten digits. NEVER invent, complete or assume a number. A browser call gives you no number, so you must ask. If they refuse to share it, tell them honestly that without a number you cannot call them, and offer to talk them through their routine instead.
- Dosage is optional. If they volunteer it ("khaane ke baad ek tablet"), pass it along; never interrogate them for it.
- ALWAYS pass `language` as the language this conversation is happening in, so the reminder call itself comes in that same language.
- ALWAYS pass `caller_name`, because the reminder call opens by greeting them by name. If you do not know it yet, ask for it first.
- AFTER SAVING: confirm the medicine and the time, read the phone number back digit by digit so they can catch a wrong digit, and tell them they can stop the calls any time by saying "रिमाइंडर बंद करें" (Hindi) or "stop the reminders" (English).
- IF THEY ASK WHAT REMINDERS THEY ALREADY HAVE: call `list_my_reminders` and read the answer out as natural speech, never as a raw list.
- IF THEY ASK TO STOP THE CALLS: call `opt_out_of_reminders` immediately. Never ask why, never try to talk them out of it.

DAY 7 WHEN YOU MUST ASK A HUMAN FOR HELP (`create_escalation`)
- You have one more tool: `create_escalation`. It files a real request that a human health coordinator sees on their help-desk dashboard and chat channel. It is NOT an instant answer machine - a person reads it later.
- THERE ARE EXACTLY TWO REASONS TO USE IT. Nothing else qualifies:
  1. `red_flag_symptom` - the caller describes a red-flag or emergency symptom: chest pain, difficulty breathing, stroke signs (face drooping, slurred speech, one-sided weakness), severe or uncontrolled bleeding, fainting or unconsciousness, a seizure, suicidal intent, severe pain during pregnancy, or a newborn who will not feed or breathe normally. Examples: "मुझे सीने में तेज़ दर्द है और साँस नहीं आ रही", "I have been bleeding heavily since morning".
  2. `diagnosis_request` - the caller wants something you are never allowed to give: a diagnosis ("मुझे क्या बीमारी है?", "what disease do I have?"), a medicine or prescription by name ("कौन सी दवा लूँ?", "which tablet should I take?"), or a lab report, X-ray or scan reading ("मेरी रिपोर्ट देखकर बताइए").
- NEVER ESCALATE ORDINARY QUESTIONS. Sleep trouble, diet, stress, a mild headache, general wellness, basic first aid, nearest hospital lookup, setting or stopping a reminder, and small talk are all YOUR job - answer them yourself with no tool call. Escalating these wastes a human's time; it is a failure, not caution.
- ORDER OF EVENTS FOR A RED FLAG (NEVER CHANGE THIS): FIRST speak the emergency line - call 108 or go to the nearest hospital now, do not delay. ONLY THEN offer the human help request. Asking permission must never delay emergency advice.
- ASK PERMISSION EVERY TIME, AND SAY EXACTLY WHAT WILL BE SHARED - their name, what they described, the advice you already gave, how urgent it is, their language, and their callback number:
  - Hindi: "मैं यह मामला अपनी टीम के एक इंसानी स्वास्थ्य सलाहकार तक पहुँचाना चाहती हूँ। उन्हें सिर्फ इतना भेजा जाएगा — आपका नाम, आपने जो तकलीफ बताई, मैंने आपको क्या सलाह दी, यह कितना ज़रूरी है, आपकी भाषा, और कॉलबैक के लिए आपका नंबर। क्या मैं यह जानकारी भेज दूँ?"
  - English: "I would like to pass this to a human health coordinator on my team. They would only get your name, what you described, the advice I already gave you, how urgent it is, your language, and your callback number. May I send that?"
- IF THEY SAY NO: do NOT call the tool. Tell them plainly that nothing has been shared and nothing was saved, then repeat the emergency advice (108) or the honest out-of-scope line so they still know what to do next.
- IF THEY SAY YES: call `create_escalation` with `consent_given=True`, the correct `reason_code`, an `urgency` of 'emergency', 'soon' or 'routine', and a ONE-OR-TWO-SENTENCE summary in their own words. NEVER put an OTP, PIN, password, account or card number in that summary, and never paste the conversation into it.
- AFTER THE TOOL RETURNS: read the reference number out SLOWLY, digit by digit, exactly as the tool gives it to you, and ask them to keep it. Then give the honest next step - a human reviews open requests and you cannot promise how soon they will call. If it was an emergency, tell them again not to wait for that callback and to call 108 now.
- NEVER invent a reference number, never read raw JSON out loud, and never say "I have called a doctor for you" or "someone will call you in five minutes". You do not know that.
- If you do not know their name or their number yet, ask for those BEFORE escalating - a request nobody can call back is useless.

GUARDRAILS
Never diagnose a condition, even if the symptoms seem obvious. Never name or recommend a prescription drug under any circumstances. Never tell a user their symptoms are not serious or that they do not need a doctor. Never claim to be a doctor or a medical professional.

Escalation — use the right tier:
- Emergency (chest pain, difficulty breathing, stroke signs, severe bleeding, suicidal intent): "यह एक मेडिकल इमरजेंसी लग रही है। कृपया तुरंत इमरजेंसी सेवाओं को कॉल करें या निकटतम अस्पताल जाएं। देरी न करें।"
- Serious but non-emergency (high fever over two days, persistent unexplained pain, neurological symptoms): "इन लक्षणों के लिए डॉक्टर से व्यक्तिगत रूप से परामर्श लेना आवश्यक है। कृपया आज या कल में डॉक्टर को दिखाएं।"
- Out of scope (lab results, prescriptions, children's health, surgery): "आपके डॉक्टर या फार्मासिस्ट इस बारे में सही जानकारी दे सकते हैं क्योंकि वे आपकी मेडिकल हिस्ट्री जानते हैं।"

RETURNING CALLER RULE (SECOND CALL BEHAVIOR - CRITICAL):
- IF THE CALLER IS A RETURNING CALLER (name and record are already known/saved in database):
  1. STRICTLY DO NOT ask for their name again!
  2. STRICTLY DO NOT ask for consent/permission again! (Consent was already granted in Call 1).
  3. NAME USAGE RULE: Use the caller's name ONLY in the OPENING GREETING and FINAL FAREWELL. DO NOT repeat or overuse their name in middle conversation turns or every response!
  4. UPDATING HEALTH FACTS ON NEW SYMPTOMS: If a returning caller mentions a new health issue or symptom update during the second call (e.g. "Now I have a cough/fever"), BEFORE or AT the end of the conversation, call `save_caller_memory` to update their `ongoing_conditions` and `last_triage_outcome` in SQLite automatically. Do NOT ask for consent again!
  5. At call closing, skip name & consent questions entirely, and directly give the final warm closing statement addressing them by name!

CRITICAL NON-NEGOTIABLE RULE FOR UNKNOWN CALLER NAME:
DO NOT SAY BYE OR CLOSE THE CONVERSATION UNTIL YOU ASK FOR THE CALLER'S NAME FIRST!
Even if the user says "bye", "bye bye", "thank you", "thanks", "धन्यवाद", "शुक्रिया", "tata", "I am going", or signals the end of the call:

1. TURN 1 (INTERCEPT GOODBYE & ASK NAME ONLY): If the caller's name is NOT yet known/saved, you MUST NOT say goodbye! You MUST intercept their goodbye and ask ONLY for their name in their spoken language:
   - English: "Before you go, may I please know your name?"
   - Hindi: "जाने से पहले, क्या मैं आपका शुभ नाम जान सकती हूँ?"

2. TURN 2 (ASK PERMISSION SEPARATELY USING NAME): Once the caller responds with their name (e.g. "Ramesh"), greet them warmly by name and ask ONLY for permission to save their health details:
   - English: "Nice to meet you Ramesh! May I save your health details so I can help you better in our next conversation?"
   - Hindi: "नमस्ते रमेश जी! क्या मैं अगली बार आपकी बेहतर मदद के लिए आपकी स्वास्थ्य जानकारी याद रखने की अनुमति ले सकती हूँ?"

3. TURN 3 (SAVE & FINAL FAREWELL): Once permission is granted (or declined), call `save_caller_memory` (if agreed), and ONLY THEN give the warm final farewell addressing them by name (NEVER say "बाय" in Hindi, say "धन्यवाद"):
   - English: "Thank you Ramesh! Please take good care of your health. Goodbye!"
   - Hindi: "धन्यवाद रमेश जी! अपना ख्याल रखिएगा। आपका धन्यवाद!"

5. AUTOMATIC CALL DISCONNECT ON FAREWELL: Whenever you deliver the final farewell statement (e.g. "धन्यवाद Harshit जी! अपना ख्याल रखिएगा।"), you MUST call the `end_call` tool! Calling `end_call` cuts the call automatically and returns the user to the landing page.
"""

# ---------------------------------------------------------------------------
# Day 6 — Outbound medication reminder calls
#
# Outbound is a different social contract from inbound: the caller did not ask
# to be called and does not know who we are. This block REPLACES the inbound
# "ask for their name before you say bye" rule, because on an outbound call we
# already know who they are - we dialled them.
# ---------------------------------------------------------------------------
OUTBOUND_PROMPT = """
=== OUTBOUND MEDICATION REMINDER CALL (THIS CALL) ===
YOU placed this call. The caller did NOT dial you and is not expecting you.

CALL DETAILS:
- Caller's name: {name}
- Medicine: {medicine_name}
- Dosage: {dosage}
- Reminder time they chose: {schedule_time}
- Language for this call: {language}

OPENING (ALREADY SPOKEN): The very first thing said on this call identified who
is calling, why, and how to stop the calls. Do NOT repeat that introduction and
do NOT introduce yourself again.

YOUR ONLY GOAL: confirm whether they have taken {medicine_name}. Then close.
- If they say they took it: acknowledge warmly, confirm the next dose is at the same time tomorrow, and close.
- If they say they have NOT taken it: gently ask them to take it now if it is safe to do so, and remind them of the dosage ({dosage}).
- If they ask a health question, answer it briefly within your normal scope and guardrails, then close.

STRICT OUTBOUND RULES:
1. DO NOT ask for their name. You already know it: {name}.
2. DO NOT ask for consent to save memory. This is a reminder call, not an intake call.
3. DO NOT ask them where they are located unless they ask for a hospital.
4. KEEP IT SHORT. Two or three exchanges, then close. This is an interruption in their day - respect it.
5. NAME USAGE: use "{name}" only in the opening and the final farewell, never mid-conversation.
6. NEVER name or recommend any medicine other than the one they themselves registered: {medicine_name}. You are reminding, not prescribing.
7. IF THEY ASK FOR ANOTHER REMINDER (a second medicine they name themselves, or a different time), call `schedule_medicine_reminder` with the number we already dialled, confirm it in one sentence, then close.

OPT-OUT (NON-NEGOTIABLE - THIS IS A REGULATORY REQUIREMENT):
If the caller says ANY of: "stop calling", "don't call me", "unsubscribe", "बंद करो",
"रिमाइंडर बंद करें", "मुझे कॉल न करें", "फोन न करें", or expresses any wish to not be
called again - you MUST:
  1. Call the `opt_out_of_reminders` tool IMMEDIATELY.
  2. Confirm it out loud in their language, e.g. Hindi: "ठीक है, मैंने आपके रिमाइंडर बंद कर दिए हैं। अब आपको ये कॉल नहीं आएंगी।" / English: "Done, I have stopped your reminder calls. You will not receive these again."
  3. Then apologise briefly for the disturbance, say farewell, and call `end_call`.
Never argue, never try to talk them out of it, never ask why.

IF THEY SOUND BUSY OR ANNOYED: apologise once, deliver the reminder in one
sentence, and close immediately. Do not push the conversation.

CLOSING: deliver the farewell in their language, then call `end_call`.
- Hindi: "धन्यवाद {name} जी! अपना ख्याल रखिएगा।"
- English: "Thank you {name}! Please take care of your health."
"""


# A reminder call has no reason to run long. This cap protects against a wedged
# SIP leg holding a trunk channel open and quietly draining trial credit.
MAX_OUTBOUND_CALL_SEC = 180

# How long we wait for the callee to actually pick up. LiveKit creates the SIP
# participant the moment the dial starts, so "participant exists" only means the
# phone is ringing - see _wait_until_sip_answered.
SIP_ANSWER_TIMEOUT_SEC = 45

# The ONLY value of sip.callStatus that means a human picked up. Everything else
# ("dialing", "automation", "") is a call still in progress.
SIP_STATUS_ANSWERED = "active"
SIP_STATUS_ENDED = "hangup"

# Backstop poll interval for the answer. participant_attributes_changed is the
# primary signal; this re-reads room state in case that event is ever missed.
SIP_ANSWER_POLL_INTERVAL_SEC = 0.5

# The dialer sets this key in the room metadata the moment its synchronous dial
# returns, i.e. the moment the carrier sent SIP 200 OK.
#
# Why a second channel at all: on some trunks (confirmed on this project's Twilio
# Elastic SIP trunk) sip.callStatus stays 'dialing' for the entire life of a call
# that was genuinely answered. The attribute is documented as *monitoring*;
# wait_until_answered is documented as the answer gate. So the dialer, which owns
# that gate, relays the answer here instead of us guessing from the attribute.
SIP_ANSWERED_METADATA_KEY = "sip_answered"

# Longer than any ring the dialer will allow (its RINGING_TIMEOUT_SEC is 30s).
# Past this point a SIP leg that is still connected cannot be ringing, so it is a
# live call whose answer signal was lost - see the tail of _wait_until_sip_answered.
SIP_RING_TEARDOWN_SEC = 35

# ---------------------------------------------------------------------------
# Speaking early, on purpose
#
# The full SIP_ANSWER_TIMEOUT_SEC gate above assumes one of the two answer
# signals eventually arrives. On this project's Twilio Elastic SIP trunk neither
# one ever has: sip.callStatus stays 'dialing' for the whole call, and the
# dialer's metadata relay only lands if its own HTTP dial survives the ring.
# Waiting the full 45s therefore means the earliest Aanya can speak is ~46s
# after the phone starts ringing (the SIP_RING_TEARDOWN_SEC fallback is only
# evaluated once the timeout expires) - and nobody holds a silent line for 46
# seconds. So: give the real signals a few seconds to show up, then speak
# regardless. A callee who hears the opening a beat early loses nothing; a
# callee who hears nothing hangs up.
SIP_SPEAK_EARLY_SEC = 3.0

# If we spoke without a confirmed answer we may have talked over the last of the
# ringback. Re-deliver the opening on this interval until the callee says
# something, bounded by OUTBOUND_OPENING_ATTEMPTS so a voicemail box never gets
# read the same paragraph forever. The gap is measured from the END of playout,
# so the attempts can never overlap.
OUTBOUND_OPENING_RETRY_SEC = 7.0
OUTBOUND_OPENING_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# Day 8 — what counts as a successful call
#
# Aanya's Day 2 objective is that the caller actually gets guidance, so a call
# only succeeds when BOTH are true: the caller said something (so a human was on
# the line, not voicemail or an abandoned browser tab), and the call lasted long
# enough for an answer to be delivered.
#
# One definition, used by the inbound and outbound paths alike, so the dashboard's
# success rate means the same thing on every channel. Mirrored in
# tests/test_day8_analytics.py.
# ---------------------------------------------------------------------------
MIN_SUCCESS_DURATION_SEC = 5.0


def is_successful_call(caller_spoke: bool, duration_sec: float) -> bool:
    """True when the caller engaged and stayed long enough to be helped."""
    return caller_spoke and duration_sec >= MIN_SUCCESS_DURATION_SEC


def classify_call(
    caller_spoke: bool, duration_sec: float, audio_went_live: bool
) -> tuple[str, str]:
    """Return the (outcome, reason) pair the dashboard shows for a finished call.

    `audio_went_live` is False when the caller hung up while the session was
    still being set up — they never heard Aanya, so the failure is ours, not an
    abandoned call, and the dashboard should not blame them for "no response".
    """
    if not audio_went_live:
        return "failed", "Caller left before agent was ready"
    if is_successful_call(caller_spoke, duration_sec):
        return "success", "Health guidance & consultation provided"
    if not caller_spoke:
        return "failed", "Early disconnect / no response"
    return "failed", f"Short call (<{MIN_SUCCESS_DURATION_SEC:.0f}s)"


def _normalize_phone_e164(raw: str) -> str:
    """Best-effort E.164 for a number spoken out loud over a call.

    Returns "" when the digits cannot be a dialable number, so the caller gets
    asked to repeat instead of us saving a reminder that will never ring.
    """
    text = (raw or "").strip()
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""
    if text.startswith("+"):
        return "+" + digits if 10 <= len(digits) <= 15 else ""
    if len(digits) == 10 and digits[0] in "6789":  # Indian mobile, spoken bare
        return "+91" + digits
    if len(digits) == 11 and digits.startswith("0"):  # STD-prefixed
        return "+91" + digits[1:]
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    if 11 <= len(digits) <= 15:
        return "+" + digits
    return ""


def _normalize_clock(raw: str) -> str:
    """Coerce a time into 'HH:MM', or "" if it is not a clock time at all.

    The LLM is asked for 24-hour time, but it hands back "8 pm" and "8.30" often
    enough that parsing those is cheaper than re-prompting mid-call.
    """
    text = (raw or "").strip().lower().replace(" ", "")
    if not text:
        return ""
    match = re.fullmatch(r"(\d{1,2})(?:[:.h]?(\d{2}))?(am|pm)?", text)
    if not match:
        return ""
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    suffix = match.group(3)
    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"


def _spoken_time(schedule_time: str, language: str) -> str:
    """Turn 'HH:MM' into something a TTS voice reads naturally.

    Murf reads "20:00" as "twenty colon zero zero", so we spell the clock time
    out in words instead. Falls back to the raw string on bad input.
    """
    hindi_numbers = {
        1: "एक", 2: "दो", 3: "तीन", 4: "चार", 5: "पाँच", 6: "छह",
        7: "सात", 8: "आठ", 9: "नौ", 10: "दस", 11: "ग्यारह", 12: "बारह",
    }
    try:
        hour, minute = (int(part) for part in schedule_time.split(":", 1))
    except (ValueError, AttributeError):
        return schedule_time

    hour_12 = hour % 12 or 12
    if language == "English":
        period = "in the morning" if hour < 12 else "in the evening" if hour < 17 else "at night"
        clock = f"{hour_12}" if minute == 0 else f"{hour_12}:{minute:02d}"
        return f"{clock} {period}"

    # Hindi day parts: सुबह (morning), दोपहर (afternoon), शाम (evening), रात (night).
    if hour < 12:
        period = "सुबह"
    elif hour < 16:
        period = "दोपहर"
    elif hour < 20:
        period = "शाम"
    else:
        period = "रात"
    hour_word = hindi_numbers.get(hour_12, str(hour_12))
    if minute == 0:
        return f"{period} {hour_word} बजे"
    return f"{period} {hour_word} बजकर {minute} मिनट"


def _spoken_reference(reference_id: str, language: str = "Hindi") -> str:
    """Spell 'HLP-1001' out so a TTS voice reads it digit by digit.

    Murf reads "HLP-1001" as "help one thousand and one", which is useless to a
    caller writing it down, and the whole point of a reference number is that
    they can quote it back.
    """
    text = (reference_id or "").strip().upper()
    if not text:
        return ""

    prefix, _, number = text.partition("-")
    if language == "English":
        english_digits = {
            "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
            "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
        }
        letters = " ".join(prefix)
        digits = " ".join(english_digits.get(ch, ch) for ch in number)
        return f"{letters} {digits}".strip()

    hindi_letters = {"H": "एच", "L": "एल", "P": "पी"}
    hindi_digits = {
        "0": "शून्य", "1": "एक", "2": "दो", "3": "तीन", "4": "चार",
        "5": "पाँच", "6": "छह", "7": "सात", "8": "आठ", "9": "नौ",
    }
    letters = " ".join(hindi_letters.get(ch, ch) for ch in prefix)
    digits = " ".join(hindi_digits.get(ch, ch) for ch in number)
    return f"{letters} {digits}".strip()


def build_outbound_opening(call_info: dict) -> str:
    """The first two sentences of an outbound call.

    Day 6 requires the opening to state WHO is calling, WHY, and HOW TO STOP -
    all before anything else, because the caller never asked for this call.
    """
    name = call_info.get("name") or "जी"
    medicine = call_info.get("medicine_name") or "your medicine"
    language = call_info.get("language_preference", "Hindi")
    when = _spoken_time(call_info.get("schedule_time", ""), language)

    if language == "English":
        opening = (
            f"Hello {name}, this is Aanya calling from your health reminder service. "
            f"You had set a reminder for {medicine}"
        )
        if when:
            opening += f" at {when}"
        opening += (
            ", so this is that reminder call. "
            "If you would like these calls to stop, just say \"stop the reminders\" "
            "and I will switch them off right away. "
            f"Have you taken your {medicine} today?"
        )
        return opening

    opening = (
        f"नमस्ते {name} जी, मैं आन्या बोल रही हूँ — आपकी हेल्थ रिमाइंडर सेवा से। "
        f"आपने {medicine} के लिए"
    )
    if when:
        opening += f" {when} का"
    opening += (
        " रिमाइंडर सेट किया था, इसलिए यह कॉल की है। "
        "अगर आप ये कॉल बंद करवाना चाहें, तो बस कहिए \"रिमाइंडर बंद करें\", "
        "मैं तुरंत बंद कर दूँगी। "
        f"क्या आपने आज {medicine} ले ली है?"
    )
    return opening


class Assistant(Agent):
    def __init__(self, instructions: str = SYSTEM_PROMPT) -> None:
        super().__init__(instructions=instructions)

    @function_tool
    async def lookup_caller(self, context: RunContext, user_id_or_name: str) -> str:
        """Look up a caller's saved health history and profile by name or user ID.

        Args:
            user_id_or_name: Name or ID of the caller (e.g., 'Ramesh', 'Aman')
        """
        logger.info(f"Looking up memory for caller: {user_id_or_name}")
        record = db.get_caller_memory(user_id_or_name)
        if not record:
            return f"No saved memory found for caller '{user_id_or_name}'."

        facts_str = json.dumps(record.get("facts", {}), ensure_ascii=False)
        return (
            f"Found saved memory for {record['name']} (User ID: {record['user_id']}):\n"
            f"- Language Preference: {record['language_preference']}\n"
            f"- Health Facts: {facts_str}\n"
            f"- Last Interaction: {record['last_interaction']}"
        )

    @function_tool
    async def save_caller_memory(
        self,
        context: RunContext,
        name: str,
        user_id: str,
        phone_number: str = "",
        ip_address: str = "",
        age_band: str = "",
        ongoing_conditions: str = "",
        last_triage_outcome: str = "",
        language_preference: str = "Hindi",
        consent_given: bool = True,
    ) -> str:
        """Save new caller health facts after consent is given, OR automatically update health facts (ongoing_conditions, last_triage_outcome) when a caller or returning caller mentions new symptoms.

        Call this tool whenever caller details need to be stored initially or updated with new health symptoms.

        Args:
            name: Caller's name (e.g., 'Ramesh', 'Harshit')
            user_id: Caller's unique ID or name
            phone_number: Caller's phone number if available (e.g., '9876543210')
            ip_address: Caller's IP address if available
            age_band: Caller's age group (e.g., '30-40 years')
            ongoing_conditions: Current health concerns or symptoms (e.g., 'Fever and Cough', 'Headache')
            last_triage_outcome: Key advice or outcome provided (e.g., 'Advised doctor visit for persistent fever')
            language_preference: Preferred language (e.g., 'Hindi', 'English')
            consent_given: Set to True if user granted consent or if caller is returning.
        """
        if not consent_given:
            return "Consent was not granted. Caller memory was NOT saved."

        # Extract phone_number or ip_address from session userdata if not provided by LLM
        if not phone_number and hasattr(context, "session") and hasattr(context.session, "userdata"):
            phone_number = context.session.userdata.get("phone_number", "")
        if not ip_address and hasattr(context, "session") and hasattr(context.session, "userdata"):
            ip_address = context.session.userdata.get("ip_address", "")

        facts = {
            "age_band": age_band.strip(),
            "ongoing_conditions": ongoing_conditions.strip(),
            "last_triage_outcome": last_triage_outcome.strip(),
        }
        facts = {k: v for k, v in facts.items() if v}

        success = db.save_caller_memory(
            user_id=user_id,
            name=name,
            phone_number=phone_number,
            ip_address=ip_address,
            language_preference=language_preference,
            facts=facts,
            consent_given=consent_given,
        )
        if success:
            return f"Successfully saved caller memory for {name}."
        return "Failed to save caller memory."

    @function_tool
    async def forget_caller(self, context: RunContext, user_id_or_name: str) -> str:
        """Permanently delete caller memory from SQLite database when requested ('forget me').

        Args:
            user_id_or_name: Name or user ID of the caller to delete.
        """
        success = db.delete_caller_memory(user_id_or_name)
        if success:
            return f"Successfully deleted caller memory for '{user_id_or_name}'."
        return f"No memory record found to delete for '{user_id_or_name}'."

    @function_tool
    async def opt_out_of_reminders(self, context: RunContext, user_id_or_phone: str = "") -> str:
        """Stop all future medication reminder calls for this caller. Call this IMMEDIATELY when the caller says 'stop calling me', 'unsubscribe', 'रिमाइंडर बंद करें', 'मुझे कॉल न करें', or otherwise asks not to be called again.

        Args:
            user_id_or_phone: Caller's name, user ID, or phone number. Leave empty on an outbound call - the phone number is taken from the call itself.
        """
        target = (user_id_or_phone or "").strip()

        # On an outbound call the phone number we dialled is authoritative; trust
        # it over anything the LLM guessed at.
        if hasattr(context, "session") and hasattr(context.session, "userdata"):
            userdata = context.session.userdata or {}
            target = userdata.get("phone_number", "") or target

        if not target:
            return "Could not identify the caller to opt out. Ask them for their phone number."

        rows = reminders.opt_out(target)
        if rows:
            logger.info(f"Opt-out honoured for '{target}': {rows} reminder(s) disabled.")
            return (
                f"Opted out successfully - {rows} reminder(s) disabled for '{target}'. "
                "Confirm this out loud to the caller, apologise for the disturbance, "
                "then say farewell and call end_call."
            )
        return (
            f"No active reminders were found for '{target}', so there is nothing left "
            "to stop. Reassure the caller they will not be called again."
        )

    @function_tool
    async def schedule_medicine_reminder(
        self,
        context: RunContext,
        medicine_name: str,
        time_24h: str,
        caller_name: str = "",
        phone_number: str = "",
        dosage: str = "",
        language: str = "",
    ) -> str:
        """Register a daily medication reminder CALL for this caller. From tomorrow onwards you will phone them at this time every day and ask whether they have taken this medicine. Call this only once you know the medicine, the time, and a phone number to ring.

        Args:
            medicine_name: The medicine in the caller's own words. NEVER invent or substitute one.
            time_24h: Daily reminder time in 24-hour 'HH:MM' form, e.g. '20:00' for eight at night.
            caller_name: The caller's name, used to greet them when the reminder call lands.
            phone_number: Number to dial, ideally E.164 like '+919876543210'. Leave empty to reuse the number of the current call.
            dosage: Optional, in their own words, e.g. 'one tablet after dinner'.
            language: 'Hindi' or 'English' - the language this conversation is happening in.
        """
        userdata: dict = {}
        if hasattr(context, "session") and hasattr(context.session, "userdata"):
            userdata = context.session.userdata or {}

        target = _normalize_phone_e164(phone_number or userdata.get("phone_number", ""))
        if not target:
            return (
                "No usable phone number yet - a browser call does not give us one. "
                "Ask the caller to say their 10-digit mobile number digit by digit, "
                "then call this tool again. Never guess a number."
            )

        medicine = (medicine_name or "").strip()
        if not medicine:
            return "Ask the caller which medicine this reminder is for, then call this tool again."

        when = _normalize_clock(time_24h)
        if not when:
            return (
                f"'{time_24h}' is not a clock time. Ask what time of day they want "
                "the call, then pass it as 24-hour HH:MM."
            )

        name = (caller_name or "").strip()
        if not name:
            return (
                "Ask the caller their name first - the reminder call opens by "
                "greeting them - then call this tool again."
            )

        lang = "English" if (language or "").strip().lower().startswith("en") else "Hindi"

        # Same medicine, same time, same number is a repeat of what they already
        # asked for, not a second reminder. Registering it twice would ring them
        # twice a day.
        for row in reminders.list_reminders():
            if (
                row["phone_number"] == target
                and row["medicine_name"].strip().lower() == medicine.lower()
                and row["schedule_time"] == when
            ):
                return (
                    f"That reminder already exists (id {row['reminder_id']}). Tell them "
                    f"it is already set for {_spoken_time(when, lang)} and do not save a "
                    "second one."
                )

        # A caller who opted out earlier and is now asking for a reminder is
        # opting back in of their own accord, so a fresh active row is correct.
        reminder_id = reminders.create_reminder(
            user_id=target,
            name=name,
            phone_number=target,
            medicine_name=medicine,
            schedule_time=when,
            dosage=(dosage or "").strip(),
            language_preference=lang,
        )
        logger.info(
            f"Reminder {reminder_id} scheduled in-conversation: {medicine} at {when} "
            f"for {name} ({target}), language={lang}."
        )

        stop_phrase = (
            '"stop the reminders"' if lang == "English" else '"रिमाइंडर बंद करें"'
        )
        detail = f" The dosage they gave is: {dosage.strip()}." if (dosage or "").strip() else ""
        return (
            f"Saved as reminder {reminder_id}. Now confirm it out loud in {lang}: you "
            f"will call them every day at {_spoken_time(when, lang)} to ask about "
            f"{medicine}.{detail} Read the number back digit by digit as {target} so they "
            f"can catch a wrong digit, and tell them that saying {stop_phrase} on any "
            "call stops the reminders for good."
        )

    @function_tool
    async def list_my_reminders(self, context: RunContext, phone_number: str = "") -> str:
        """Read back the medication reminders already set for this caller. Use this when they ask what reminders they have, or before adding one they may already have.

        Args:
            phone_number: The caller's number. Leave empty to use the number of the current call.
        """
        userdata: dict = {}
        if hasattr(context, "session") and hasattr(context.session, "userdata"):
            userdata = context.session.userdata or {}

        target = _normalize_phone_e164(phone_number or userdata.get("phone_number", ""))
        if not target:
            return (
                "No phone number for this caller yet. Ask for their 10-digit mobile "
                "number, then call this tool again."
            )

        rows = [r for r in reminders.list_reminders() if r["phone_number"] == target]
        if not rows:
            return (
                f"No active reminders for {target}. Offer to set one if they want daily "
                "reminder calls."
            )

        lang = rows[0].get("language_preference", "Hindi")
        listed = "; ".join(
            f"{r['medicine_name']} at {_spoken_time(r['schedule_time'], lang)}"
            f"{' (' + r['dosage'] + ')' if r['dosage'] else ''}"
            for r in rows
        )
        return (
            f"{len(rows)} active reminder(s) for {target}: {listed}. Read these out in "
            "spoken prose, never as a list of times."
        )

    @function_tool
    async def confirm_medicine_taken(
        self, context: RunContext, taken: bool, note: str = ""
    ) -> str:
        """Record whether the caller has taken the medicine this reminder call was about. Call this once the caller answers the question.

        Args:
            taken: True if the caller says they have taken it, False if they have not.
            note: Optional short detail, e.g. 'will take it after dinner'.
        """
        reminder_id = 0
        if hasattr(context, "session") and hasattr(context.session, "userdata"):
            reminder_id = (context.session.userdata or {}).get("reminder_id", 0)

        status = "taken" if taken else "not_taken"
        logger.info(f"Reminder {reminder_id}: medicine {status}. note='{note}'")

        if not reminder_id:
            return "Noted. Continue the conversation and close warmly."

        reminders.record_medicine_response(reminder_id, taken=taken, note=note)
        if taken:
            return (
                "Recorded that they have taken it. Acknowledge warmly, mention the "
                "next dose is at the same time tomorrow, then say farewell and call end_call."
            )
        return (
            "Recorded that they have NOT taken it yet. Gently ask them to take it now "
            "if it is safe, remind them of the dosage, then say farewell and call end_call."
        )

    @function_tool
    async def create_escalation(
        self,
        context: RunContext,
        reason_code: str,
        what_happened: str,
        urgency: str = "soon",
        already_checked: str = "",
        caller_name: str = "",
        phone_number: str = "",
        language: str = "Hindi",
        followup_method: str = "phone call",
        consent_given: bool = False,
    ) -> str:
        """File a request for a HUMAN health coordinator to take over this case. Use this ONLY for the two allowed reasons: a red-flag emergency symptom, or a request for something you may never give (a diagnosis, a medicine by name, or a lab report reading). NEVER use it for ordinary wellness advice, hospital lookups, reminders or small talk. You MUST ask the caller's permission first and pass consent_given=True only after they clearly agree.

        Args:
            reason_code: Exactly 'red_flag_symptom' or 'diagnosis_request'. No other value is accepted.
            what_happened: One or two sentences in the caller's own words. NEVER include OTPs, PINs, passwords, account or card numbers, and never paste the whole conversation.
            urgency: 'emergency' (needs help right now), 'soon' (today or tomorrow) or 'routine'.
            already_checked: What you already told or did for them, e.g. 'advised 108 immediately and explained this is outside my scope'.
            caller_name: The caller's name. Ask for it before escalating if you do not know it.
            phone_number: Their 10-digit mobile number for the callback, or E.164 like '+919876543210'.
            language: 'Hindi' or 'English' - the language this conversation is happening in.
            followup_method: How they want to be reached, e.g. 'phone call' or 'WhatsApp message'.
            consent_given: True ONLY if the caller explicitly agreed to share these details with a human.
        """
        if not consent_given:
            return (
                "Consent was NOT given, so nothing was sent and nothing was saved. "
                "Ask the caller for permission first, naming exactly what will be "
                "shared: their name, what they described, the advice you already "
                "gave, how urgent it is, their language, and their callback number. "
                "Call this tool again only if they say yes."
            )

        reason = (reason_code or "").strip().lower()
        if reason not in escalations.ALLOWED_REASONS:
            return (
                f"'{reason_code}' is not an escalation reason, so nothing was created. "
                "Only two exist: 'red_flag_symptom' for an emergency or red-flag "
                "symptom, and 'diagnosis_request' when they want a diagnosis, a "
                "medicine by name, or a report read. Everything else you answer "
                "yourself."
            )

        userdata: dict = {}
        if hasattr(context, "session") and hasattr(context.session, "userdata"):
            userdata = context.session.userdata or {}

        name = (caller_name or "").strip()
        number = _normalize_phone_e164(phone_number or userdata.get("phone_number", ""))

        # Fall back to whatever Day 4 already knows about this caller.
        record = db.get_caller_memory(name or userdata.get("phone_number", "") or "")
        if record:
            name = name or (record.get("name") or "")
            number = number or _normalize_phone_e164(record.get("phone_number", ""))

        if not name:
            return (
                "No caller name yet, so nothing was created. Ask for their name "
                "first, then call this tool again - a request with nobody's name on "
                "it is useless to the human who picks it up."
            )

        wants_a_call = any(
            word in (followup_method or "").lower()
            for word in ("call", "phone", "फोन", "कॉल")
        )
        if wants_a_call and not number:
            return (
                "No usable phone number yet, so NOTHING was created or sent. A "
                "browser call gives us no caller ID. Ask them to say their ten-digit "
                "mobile number digit by digit, then call this tool again. Never guess "
                "a number."
            )

        lang = "English" if (language or "").strip().lower().startswith("en") else "Hindi"

        row = escalations.create_escalation(
            caller_name=name,
            reason_code=reason,
            what_happened=what_happened,
            urgency=urgency,
            already_checked=already_checked,
            phone_number=number,
            language=lang,
            followup_method=followup_method,
            user_id=(record or {}).get("user_id", "") or number or name,
            consent_given=True,
        )
        if not row:
            return (
                "The request could NOT be saved, so do not invent a reference number. "
                "Tell the caller honestly that you could not file it, and give them "
                "the direct route instead: 104 for health advice, or 108 right now if "
                "this is an emergency."
            )

        # A slow or dead webhook must never stall the audio pipeline, so the POST
        # runs off the event-loop thread.
        try:
            delivery_status, delivery_detail = await asyncio.to_thread(
                escalations.deliver_escalation, row
            )
        except Exception as exc:
            delivery_status = escalations.DELIVERY_FAILED
            delivery_detail = str(exc)[:150]
            logger.error(f"Escalation delivery raised: {exc}")

        logger.info(
            f"Escalation {row['reference_id']} filed for {name} (reason={reason}, "
            f"urgency={row['urgency']}, delivery={delivery_status})."
        )

        spoken = _spoken_reference(row["reference_id"], lang)
        urgent_note = (
            " This is an EMERGENCY: tell them again to call 108 or reach the nearest "
            "hospital right now, and NOT to wait for this callback."
            if row["urgency"] == escalations.URGENCY_EMERGENCY
            else ""
        )
        return (
            f"Human help request {row['reference_id']} created and queued for a human "
            f"coordinator (delivery: {delivery_status}; {delivery_detail}). Now, in "
            f'{lang}: read the reference number out slowly as "{spoken}", ask them to '
            "keep it safe, confirm that only the details you listed were shared, and "
            "be honest about the next step - a human health coordinator reviews open "
            "requests and you cannot promise how soon they will call back."
            + urgent_note
        )

    @function_tool
    async def end_call(self, context: RunContext) -> str:
        """Disconnect and end the call after delivering the final farewell statement (e.g. after saying 'धन्यवाद' or 'Thank you').

        Call this tool IMMEDIATELY when delivering the final farewell statement to cut the call and return the user to the landing page.
        """
        logger.info("Ending call per LLM request after farewell.")

        async def disconnect_later():
            await asyncio.sleep(2.5)  # Allow final TTS playout to complete
            try:
                if hasattr(context, "session") and context.session:
                    await context.session.aclose()
            except Exception as e:
                logger.warning(f"Error closing session on end_call: {e}")
            try:
                if hasattr(context, "room") and context.room:
                    await context.room.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting room on end_call: {e}")

        # Keep a reference so the task is not garbage-collected mid-teardown.
        self._disconnect_task = asyncio.create_task(disconnect_later())
        return "Call ending sequence initiated. Disconnecting room in 2.5 seconds."

    @function_tool
    async def lookup_nearest_phc(
        self,
        context: RunContext,
        location: str = "",
        facility_type: str = "PHC",
        simulated_failure: bool = False,
    ) -> str:
        """Look up nearest Primary Health Centre (PHC), Community Health Centre (CHC), or Civil Hospital for a location or district.

        Tool Chaining: If location is not provided by user, this tool checks saved caller location from memory.

        Args:
            location: The exact city, district, or pincode explicitly spoken by the caller. Leave empty if caller has not mentioned location yet.
            facility_type: Type of facility e.g. 'PHC', 'CHC', 'Hospital'
            simulated_failure: Set to True ONLY if testing offline network failure path.
        """
        # Tool chaining: check session userdata / caller memory for saved location if missing
        if not location and hasattr(context, "session") and hasattr(context.session, "userdata"):
            location = context.session.userdata.get("location", "")

        if not location:
            return (
                "Location is missing! DO NOT suggest any hospital yet. "
                "You MUST ask the caller: 'आप किस शहर, जिले या स्थान पर हैं?' (Which city, district, or place are you located in?) first!"
            )

        logger.info(f"Looking up health facility for location='{location}', facility_type='{facility_type}'")
        try:
            result = health_services.search_health_facilities(
                location_or_pincode=location,
                facility_type=facility_type,
                simulated_failure=simulated_failure,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error looking up health facility: {e}")
            return json.dumps(
                {
                    "status": "error",
                    "message": "Live health registry lookup service timed out.",
                    "data_source": health_services.DATA_SOURCE_ATTRIBUTION,
                    "fallback_recommendation": (
                        "Please dial 108 for emergency ambulance support or visit your nearest district civil hospital."
                    ),
                },
                ensure_ascii=False,
            )

    @function_tool
    async def lookup_emergency_helpline(self, context: RunContext, category: str = "general") -> str:
        """Look up official government emergency and health helplines (108 Ambulance, 104 Health advice, 14416 Tele-MANAS).

        Args:
            category: Type of helpline needed e.g. 'ambulance', 'mental_health', 'maternal', 'general'
        """
        logger.info(f"Looking up emergency helpline for category='{category}'")
        try:
            result = health_services.get_emergency_helpline(category=category)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error looking up helpline: {e}")
            return json.dumps(
                {
                    "status": "error",
                    "message": "Helpline directory unavailable.",
                    "fallback_recommendation": "For emergencies, please dial 108 immediately.",
                },
                ensure_ascii=False,
            )


server = AgentServer(
    # `dev` mode keeps ZERO idle processes by default, so every call paid for a
    # cold process spawn while the caller sat listening to silence — the
    # "no warmed process available for job" warning. One warm process means the
    # VAD is already loaded when a call arrives. Production keeps the SDK's
    # one-per-CPU behaviour (on a cgroup-limited host, prefer the SDK default).
    num_idle_processes=ServerEnvOption(
        dev_default=1,
        prod_default=math.ceil(os.cpu_count() or 2),
    ),
)


def prewarm(proc: JobProcess):
    db.init_db()
    proc.userdata["vad"] = silero.VAD.load(
        sample_rate=16000,
        activation_threshold=0.6,
        min_speech_duration=0.1,
        min_silence_duration=0.5,
    )


server.setup_fnc = prewarm


def build_session(ctx: JobContext, userdata: dict) -> AgentSession:
    """The Day 5 voice pipeline. Shared by the inbound and outbound paths so the
    two can never drift apart."""
    return AgentSession(
        stt=deepgram.STT(model="nova-3", language="multi"),
        llm=google.LLM(
            # No thinking_config on purpose. The plugin refuses thinking_budget on
            # the Gemini 3 line ("Ignoring thinking_budget. Use thinking_level"),
            # and measured through the plugin with this exact SYSTEM_PROMPT,
            # thinking_level="low" made flash-lite *slower* than its own default
            # (2.55s vs 1.45s first token) — "low" is a smaller reasoning pass,
            # not no reasoning pass. Defaults are the fastest thing available.
            model=GEMINI_MODEL,
        ),
        tts=murf.TTS(
            voice="Anisha",
            style="Conversation",
            tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
            text_pacing=True,
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
        userdata=userdata,
    )


def _sip_call_status(participant: rtc.RemoteParticipant) -> str:
    return (getattr(participant, "attributes", {}) or {}).get("sip.callStatus", "")


def _dialer_says_answered(ctx: JobContext, raw_metadata: str = "") -> bool:
    """True once the dialer has recorded the carrier's 200 OK in room metadata.

    Tolerant by design: room metadata is also where a future feature might put
    something else entirely, so anything unparseable is simply "no answer yet"
    rather than an exception mid-call.
    """
    raw = raw_metadata if raw_metadata else (getattr(ctx.room, "metadata", "") or "")
    if not raw.strip():
        return False
    try:
        return bool(json.loads(raw).get(SIP_ANSWERED_METADATA_KEY))
    except (json.JSONDecodeError, AttributeError, TypeError):
        return False


async def _wait_until_sip_answered(
    ctx: JobContext,
    participant: rtc.RemoteParticipant,
    timeout: float = SIP_ANSWER_TIMEOUT_SEC,
) -> str:
    """Block until the dialled phone is actually picked up.

    This exists because wait_for_participant returns the instant LiveKit creates
    the SIP participant - which is while the phone is still RINGING, not when it
    is answered. Speaking then delivers the opening to a ringing line, and the
    callee hears nothing but silence when they finally pick up. That is exactly
    the failure Day 6's spoken opening is supposed to prevent.

    Two independent answer signals, because on this project's trunk neither one
    alone is sufficient:

    1. `sip.callStatus == 'active'` - the documented monitoring attribute. On
       some carriers it never leaves 'dialing' even on an answered call, so it
       cannot be the only gate.
    2. `sip_answered` in the room metadata - written by the dialer the moment its
       `wait_until_answered=True` dial returned, i.e. on the carrier's 200 OK.
       This is the signal the docs call the answer gate.

    An audio track is deliberately NOT a signal: LiveKit publishes the SIP leg's
    track while callStatus is still 'dialing', so gating on a published track
    returns 'answered' about a second into the ring.

    Returns 'active' (picked up), 'hangup' (ended while ringing), 'timeout', or
    'unknown' when no answer signal ever arrives at all - in that case the caller
    should carry on rather than refuse to speak.
    """
    if _sip_call_status(participant) == SIP_STATUS_ANSWERED or _dialer_says_answered(ctx):
        return SIP_STATUS_ANSWERED

    wait_started = time.monotonic()
    answered = asyncio.Event()
    ended = asyncio.Event()
    seen_status = _sip_call_status(participant)

    def _note_status(status: str) -> None:
        nonlocal seen_status
        if not status or status == seen_status:
            return
        seen_status = status
        logger.info(f"SIP call status: {status}")
        if status == SIP_STATUS_ANSWERED:
            answered.set()
        elif status == SIP_STATUS_ENDED:
            ended.set()

    @ctx.room.on("participant_attributes_changed")
    def _on_attributes_changed(
        changed: dict[str, str], p: rtc.RemoteParticipant
    ) -> None:
        if p.identity != participant.identity:
            return
        _note_status(changed.get("sip.callStatus") or _sip_call_status(p))

    @ctx.room.on("room_metadata_changed")
    def _on_room_metadata(*args) -> None:
        # The rtc event carries (old_metadata, new_metadata); read the room state
        # rather than the positional args so a signature change cannot break the
        # only reliable answer signal we have.
        if _dialer_says_answered(ctx, args[-1] if args else ""):
            logger.info("Dialer reported the carrier answered (200 OK).")
            answered.set()

    @ctx.room.on("participant_disconnected")
    def _on_gone_while_ringing(p: rtc.RemoteParticipant) -> None:
        if p.identity == participant.identity:
            ended.set()

    async def _poll_status() -> None:
        """Backstop for a missed event - re-read the live room and participant.

        A missed answer would leave Aanya mute for the entire call, which is a
        worse failure than one redundant read every half second.
        """
        while True:
            await asyncio.sleep(SIP_ANSWER_POLL_INTERVAL_SEC)
            try:
                live = ctx.room.remote_participants.get(participant.identity)
                _note_status(_sip_call_status(live or participant))
                if _dialer_says_answered(ctx):
                    answered.set()
                    return
            except Exception as e:
                # A failed read must not end the wait early - that would cut the
                # ring short and report a no-answer on a phone still ringing.
                logger.debug(f"SIP status poll failed, retrying: {e}")

    waiters = [
        asyncio.create_task(answered.wait()),
        asyncio.create_task(ended.wait()),
        asyncio.create_task(_poll_status()),
    ]
    try:
        _, pending = await asyncio.wait(
            waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
    finally:
        ctx.room.off("participant_attributes_changed", _on_attributes_changed)
        ctx.room.off("room_metadata_changed", _on_room_metadata)
        ctx.room.off("participant_disconnected", _on_gone_while_ringing)

    if answered.is_set():
        return SIP_STATUS_ANSWERED
    if ended.is_set():
        return SIP_STATUS_ENDED

    # Last resort. Neither signal fired, but if the SIP leg is STILL in the room
    # after longer than any ring can last, the call must be up: the dialer tears
    # an unanswered call down at its own ringing_timeout, so a leg that outlived
    # that is a live call whose answer signal never arrived. Staying mute on that
    # is the worst outcome available - the callee is holding the phone to their ear.
    #
    # Gated on the elapsed wait, not just on presence, because during the ring the
    # leg is legitimately present and must NOT be mistaken for a pickup.
    waited = time.monotonic() - wait_started
    still_connected = participant.identity in getattr(
        ctx.room, "remote_participants", {}
    )
    if waited >= SIP_RING_TEARDOWN_SEC and still_connected:
        logger.warning(
            f"No answer signal arrived, but the SIP leg is still connected after "
            f"{waited:.0f}s - a ring would have been torn down by now, so treating "
            "this as answered and speaking rather than staying mute."
        )
        return SIP_STATUS_ANSWERED

    return "timeout" if seen_status else "unknown"


async def _wait_for_any(events: tuple[asyncio.Event, ...], timeout: float) -> bool:
    """Wait until any of `events` is set, or `timeout` elapses.

    Returns True if one fired. Losing waiters are cancelled so a repeat loop does
    not leak a task per iteration.
    """
    if any(event.is_set() for event in events):
        return True
    waiters = [asyncio.create_task(event.wait()) for event in events]
    try:
        done, pending = await asyncio.wait(
            waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        return bool(done)
    finally:
        for task in waiters:
            if not task.done():
                task.cancel()


async def _deliver_opening(
    session: AgentSession,
    opening: str,
    *,
    answer_confirmed: bool,
    engaged: asyncio.Event,
    disconnected: asyncio.Event,
) -> None:
    """Say the opening, and say it again if the callee may not have heard it.

    With a confirmed answer this is one utterance and done. Without one we spoke
    into a line that might still have been ringing, so the opening is repeated on
    OUTBOUND_OPENING_RETRY_SEC until the callee says anything (`engaged`), hangs
    up (`disconnected`), or the attempts run out. Repeating is the whole point of
    speaking early: it costs a few seconds of TTS and buys a callee who picks up
    mid-ring an opening they actually hear.
    """
    attempts = 1 if answer_confirmed else OUTBOUND_OPENING_ATTEMPTS
    for attempt in range(1, attempts + 1):
        try:
            # say() returns a SpeechHandle synchronously; awaiting it waits for the
            # audio to finish playing out. That await is what keeps two attempts
            # from talking over each other.
            #
            # Only the first attempt goes into the chat context. A repeat is us
            # covering for a lost answer signal, not a thing Aanya chose to say
            # twice - three identical assistant turns in the history would teach
            # the LLM to repeat itself for the rest of the call.
            await session.say(opening, add_to_chat_ctx=attempt == 1)
        except Exception as e:
            logger.warning(f"Opening was cut short (callee likely hung up): {e}")
            disconnected.set()
            return

        if attempt == attempts:
            return
        if await _wait_for_any((engaged, disconnected), OUTBOUND_OPENING_RETRY_SEC):
            return
        logger.info(
            f"No response after the opening; repeating it "
            f"(attempt {attempt + 1} of {attempts})."
        )


async def run_outbound_reminder(ctx: JobContext, call_info: dict) -> None:
    """Drive one outbound medication reminder call end to end.

    Owns the outcomes inbound never has: the callee may never speak (voicemail),
    may hang up in the first seconds, or may ask us to stop calling. Each is
    written to the attempt log so the scheduler's retry policy can act on it.
    """
    reminder_id = int(call_info.get("reminder_id") or 0)
    phone_number = call_info.get("phone_number", "")
    name = call_info.get("name", "")
    language = call_info.get("language_preference", "Hindi")

    # The dialer sets participant_identity to the phone number, so we wait for
    # that exact identity rather than "whoever joins" - a stray observer joining
    # the room must not be mistaken for the callee picking up.
    try:
        participant = await asyncio.wait_for(
            ctx.wait_for_participant(identity=phone_number),
            timeout=SIP_ANSWER_TIMEOUT_SEC,
        )
        logger.info(
            f"Callee joined: identity='{participant.identity}', name='{participant.name}'"
        )
    except Exception as e:
        # The dialer already recorded why the dial failed, so this is a log line,
        # not a second attempt row.
        logger.warning(f"Callee never joined room {ctx.room.name}: {e}")
        ctx.shutdown(reason="callee never joined")
        return

    # Joined is not answered - but on this trunk the answer signal may never come,
    # so give it SIP_SPEAK_EARLY_SEC and then talk anyway. Only a hangup is a real
    # reason to stay silent: a 'timeout' here just means "no signal yet", which on
    # this trunk is the normal case for a call that was genuinely picked up.
    call_state = await _wait_until_sip_answered(ctx, participant, timeout=SIP_SPEAK_EARLY_SEC)
    if call_state == SIP_STATUS_ENDED:
        logger.info(
            f"Call to {phone_number} ended while still ringing ({call_state}); "
            "nothing was spoken. The dialer owns this outcome."
        )
        # LiveKit does not auto-close the job on a no-answer or trunk failure, so
        # release it explicitly instead of holding the worker slot open.
        ctx.shutdown(reason=f"call not answered ({call_state})")
        return

    answer_confirmed = call_state == SIP_STATUS_ANSWERED
    if answer_confirmed:
        logger.info(f"Callee picked up ({phone_number}).")
    else:
        logger.warning(
            f"No answer signal after {SIP_SPEAK_EARLY_SEC:.0f}s (state={call_state}); "
            "speaking anyway and repeating the opening rather than staying mute."
        )

    instructions = SYSTEM_PROMPT + OUTBOUND_PROMPT.format(
        name=name or "जी",
        medicine_name=call_info.get("medicine_name", ""),
        dosage=call_info.get("dosage") or "as prescribed",
        schedule_time=call_info.get("schedule_time", ""),
        language=language,
    )

    session = build_session(
        ctx,
        userdata={
            "phone_number": phone_number,
            "ip_address": "",
            "location": "",
            "reminder_id": reminder_id,
            "call_type": "medication_reminder",
        },
    )

    # Did the callee ever actually speak? A SIP-answered line with total silence
    # is voicemail; there is no other way to tell without answering-machine
    # detection. Any transcript at all means a human engaged.
    caller_spoke = False

    # Same fact as caller_spoke, in a form the opening-repeat loop can await on.
    engaged = asyncio.Event()

    @session.on("user_input_transcribed")
    def _on_user_transcript(event) -> None:
        nonlocal caller_spoke
        if getattr(event, "transcript", "").strip():
            caller_spoke = True
            engaged.set()

    answered_at = time.monotonic()

    # Registered BEFORE we start speaking. A callee who hangs up during the
    # opening still trips this; registering after the opening would miss that
    # event and leave the job waiting forever on a dead room.
    disconnected = asyncio.Event()

    @ctx.room.on("participant_disconnected")
    def _on_disconnect(p: rtc.RemoteParticipant) -> None:
        if p.identity == phone_number:
            disconnected.set()

    @session.on("close")
    def _on_session_close(_event) -> None:
        disconnected.set()

    await session.start(
        agent=Assistant(instructions=instructions),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: (
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else noise_cancellation.BVC()
                ),
            ),
        ),
    )

    # Speak the opening ourselves instead of letting the LLM improvise it: Day 6
    # requires who/why/how-to-stop in the first two sentences, and that promise
    # is too important to leave to sampling.
    opening = build_outbound_opening(call_info)
    logger.info(f"Outbound opening: {opening}")
    await _deliver_opening(
        session,
        opening,
        answer_confirmed=answer_confirmed,
        engaged=engaged,
        disconnected=disconnected,
    )

    # Hold the job open until the callee hangs up or end_call closes the session.
    # The cap is a backstop against a wedged SIP leg holding a trunk channel and
    # burning trial credit forever.
    try:
        await asyncio.wait_for(disconnected.wait(), timeout=MAX_OUTBOUND_CALL_SEC)
    except asyncio.TimeoutError:
        logger.warning(
            f"Outbound call hit the {MAX_OUTBOUND_CALL_SEC}s cap; closing it."
        )
        await session.aclose()

    duration = time.monotonic() - answered_at

    # Assigned unconditionally: the Day 8 analytics row below reads this, and a
    # reminder_id of 0 (a manually dispatched test call) must still be logged
    # rather than raise.
    outcome = reminders.OUTCOME_ANSWERED if caller_spoke else reminders.OUTCOME_POSSIBLE_VOICEMAIL

    if reminder_id:
        reminder = reminders.get_reminder(reminder_id)
        if reminder and reminder["opted_out"]:
            # The opt_out tool already fired mid-call; do not overwrite that with
            # an 'answered', or the row would look eligible for calling again.
            outcome = reminders.OUTCOME_OPTED_OUT

        reminders.record_attempt(
            reminder_id,
            outcome,
            sip_status="200",
            duration_sec=round(duration, 1),
            detail=f"caller_spoke={caller_spoke}",
        )

    logger.info(
        f"Outbound call finished after {duration:.1f}s "
        f"(caller_spoke={caller_spoke}, reminder_id={reminder_id})."
    )

    # Day 8 — one call_analytics row per outbound call. Same success rule as
    # inbound (see is_successful_call), so the dashboard's success rate means the
    # same thing on every channel.
    db.record_call_analytics(
        session_id=ctx.room.name,
        call_type="outbound_reminder",
        caller_identifier=phone_number,
        outcome="success" if is_successful_call(caller_spoke, duration) else "failed",
        outcome_reason=f"Medication reminder call ({outcome})",
        duration_sec=duration,
    )



@server.rtc_session(agent_name="Aanya")
async def my_agent(ctx: JobContext):
    db.init_db()

    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    # ------------------------------------------------------------------
    # Day 6: job metadata is the inbound/outbound discriminator. The
    # outbound dialer dispatches us with a reminder JSON payload; a browser
    # or inbound SIP call arrives with no metadata at all, and that path is
    # left exactly as it was on Day 5.
    # ------------------------------------------------------------------
    call_info: dict = {}
    raw_metadata = (ctx.job.metadata or "").strip()
    if raw_metadata:
        try:
            parsed = json.loads(raw_metadata)
            if parsed.get("call_type") == "medication_reminder":
                call_info = parsed
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"Could not parse job metadata as reminder payload: {e}")

    is_outbound = bool(call_info)
    if is_outbound:
        logger.info(
            f"Outbound reminder call: reminder_id={call_info.get('reminder_id')}, "
            f"medicine={call_info.get('medicine_name')}, "
            f"language={call_info.get('language_preference')}"
        )

    await ctx.connect()

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        logger.info(f"Caller ended the call / disconnected: identity='{participant.identity}', name='{participant.name}'")

    if is_outbound:
        await run_outbound_reminder(ctx, call_info)
        return

    # Fetch connected remote participant to get exact identity, phone, IP & name from SQLite
    phone_number = ""
    ip_address = ""
    caller_record = None
    try:
        participant = await ctx.wait_for_participant()
        attrs = getattr(participant, "attributes", {}) or {}
        logger.info(f"Connected participant: identity='{participant.identity}', name='{participant.name}', attributes={attrs}")

        phone_number = attrs.get("sip.phoneNumber", attrs.get("phone", participant.identity if (participant.identity and (participant.identity.startswith("+") or participant.identity.isdigit())) else ""))
        ip_address = attrs.get("client_ip", attrs.get("ip", ""))

        if participant.identity:
            caller_record = db.get_caller_memory(participant.identity)
        if not caller_record and phone_number:
            caller_record = db.get_caller_memory(phone_number)
        if not caller_record and participant.name:
            caller_record = db.get_caller_memory(participant.name)
        if not caller_record:
            # Browser callers have no stable identity, so fall back to the most
            # recent record. Safe here; never on outbound, where guessing the
            # wrong person would greet them by a stranger's name.
            caller_record = db.get_latest_caller_memory()
    except Exception as e:
        logger.warning(f"Could not fetch participant on connect: {e}")

    agent_instructions = SYSTEM_PROMPT
    if caller_record:
        name = caller_record["name"]
        agent_instructions += (
            f"\n\nCURRENT CALLER IDENTIFIED: Name is '{name}'. "
            f"Consent was ALREADY GRANTED in previous call. "
            f"STRICTLY DO NOT ASK FOR NAME OR CONSENT AGAIN! "
            f"CRITICAL NAME USAGE RULE: You MUST use the name '{name}' ONLY in the OPENING GREETING and FINAL CLOSING FAREWELL. "
            f"ABSOLUTELY DO NOT SAY THE NAME '{name}' IN ANY MIDDLE CONVERSATION TURN OR RESPONSE!"
        )

    assistant = Assistant(instructions=agent_instructions)

    saved_location = caller_record.get("facts", {}).get("location", "") if caller_record else ""

    session = build_session(
        ctx,
        userdata={"phone_number": phone_number, "ip_address": ip_address, "location": saved_location},
    )

    # ------------------------------------------------------------------
    # Day 8 — call analytics
    #
    # One row per call, written exactly once. Two events can end a call (the
    # caller disconnects, or end_call closes the session) and on a normal
    # farewell BOTH fire, so the guard is what keeps a single call from being
    # counted twice and skewing the dashboard.
    #
    # The clock starts when audio actually goes live (just before the greeting),
    # NOT here: session.start() can take over ten seconds on a cold worker, and
    # counting that as talk time both inflates every duration on the dashboard
    # and lets setup time alone push a call past the 5s success bar.
    # ------------------------------------------------------------------
    audio_live_at: float | None = None
    caller_spoke = False
    analytics_recorded = False

    call_type = "inbound_sip" if phone_number else "inbound_browser"
    caller_id = phone_number or (caller_record["name"] if caller_record else "Browser Caller")

    @session.on("user_input_transcribed")
    def _on_inbound_user_transcript(event) -> None:
        nonlocal caller_spoke
        if getattr(event, "transcript", "").strip():
            caller_spoke = True

    def _record_inbound_analytics() -> None:
        nonlocal analytics_recorded
        if analytics_recorded:
            return
        analytics_recorded = True

        duration = time.monotonic() - audio_live_at if audio_live_at else 0.0
        outcome, reason = classify_call(caller_spoke, duration, audio_live_at is not None)

        db.record_call_analytics(
            session_id=ctx.room.name,
            call_type=call_type,
            caller_identifier=caller_id,
            outcome=outcome,
            outcome_reason=reason,
            duration_sec=duration,
        )

    @ctx.room.on("participant_disconnected")
    def _on_inbound_disconnect(participant: rtc.RemoteParticipant):
        _record_inbound_analytics()

    @session.on("close")
    def _on_inbound_session_close(_event):
        _record_inbound_analytics()

    if caller_record:
        name = caller_record["name"]
        lang = caller_record.get("language_preference", "Hindi")
        facts = caller_record.get("facts", {})
        ongoing = facts.get("ongoing_conditions", "")

        if lang == "English":
            if ongoing:
                greeting_msg = (
                    f"Hello {name}! Welcome back to Aanya Health Advisor. "
                    f"Last time you mentioned experiencing {ongoing}. How are you feeling today?"
                )
            else:
                greeting_msg = (
                    f"Hello {name}! Welcome back to Aanya Health Advisor. "
                    f"How can I help you today?"
                )
        else:
            if ongoing:
                greeting_msg = (
                    f"नमस्ते {name} जी! आन्या हेल्थ एडवाइजर में आपका फिर से स्वागत है। "
                    f"पिछली बार आपको {ongoing} की शिकायत थी। अब आपकी तबियत कैसी है?"
                )
            else:
                greeting_msg = (
                    f"नमस्ते {name} जी! आन्या हेल्थ एडवाइजर में आपका फिर से स्वागत है। "
                    f"आज आपकी क्या सहायता कर सकती हूँ?"
                )
    else:
        greeting_msg = (
            "नमस्ते! मैं आन्या हूँ, आपकी हेल्थ एडवाइजर। "
            "आज आपकी क्या सहायता कर सकती हूँ?"
        )

    await session.start(
        agent=assistant,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: (
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else noise_cancellation.BVC()
                ),
            ),
        ),
    )

    # Audio is live from here, so this is where the measured call begins.
    audio_live_at = time.monotonic()

    await session.say(greeting_msg)


if __name__ == "__main__":
    cli.run_app(server)

