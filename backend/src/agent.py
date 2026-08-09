import json
import logging
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
    inference,
    room_io,
    tokenize,
)
from livekit.plugins import deepgram, google, murf, noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

import db

logger = logging.getLogger("agent")

load_dotenv(".env.local")

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
- If the user asks to erase their data ("मेरी जानकारी हटा दो" / "forget me"), call `forget_caller`.

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

4. IF CALLER NAME IS ALREADY KNOWN/SAVED FROM START: Skip steps 1 & 2 and directly give the warm farewell addressing them by name (DO NOT ASK FOR NAME OR CONSENT AGAIN!).
"""


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


server = AgentServer()


def prewarm(proc: JobProcess):
    db.init_db()
    proc.userdata["vad"] = silero.VAD.load(
        sample_rate=16000,
        activation_threshold=0.6,
        min_speech_duration=0.1,
        min_silence_duration=0.5,
    )


server.setup_fnc = prewarm


@server.rtc_session(agent_name="Saamiksha")
async def my_agent(ctx: JobContext):
    db.init_db()

    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    await ctx.connect()

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        logger.info(f"Caller ended the call / disconnected: identity='{participant.identity}', name='{participant.name}'")

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

    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="multi"),
        llm=google.LLM(
            model="gemini-3.5-flash-lite",
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
        userdata={"phone_number": phone_number, "ip_address": ip_address},
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
            "मैं आपकी सेहत से जुड़े किसी भी सवाल में मदद करने के लिए यहाँ हूँ। "
            "आज आपकी क्या सहायता कर सकती हूँ?"
        )

    await session.say(greeting_msg)


if __name__ == "__main__":
    cli.run_app(server)
