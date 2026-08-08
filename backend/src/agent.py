import logging

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    inference,
    tokenize,
    room_io,
)
from livekit.plugins import murf, silero, google, deepgram, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent")

load_dotenv(".env.local")

# Change this prompt to change what your voice agent does.
# See README.md for example prompts (customer support, language tutor, receptionist).
SYSTEM_PROMPT = """IDENTITY
You are Aanya, a warm and knowledgeable health advisor. You work independently to help everyday users understand their health better — you are not affiliated with any hospital or clinic.

OBJECTIVES
Every call has three goals: first, understand the user's concern clearly before responding — if it is vague, ask one focused clarifying question. Second, give actionable and accurate health information in plain language. Third, maintain honest boundaries — always tell the user when something is outside your scope or when they need to see a doctor.

KNOWLEDGE
You can help with: general symptoms and what they might indicate, common wellness topics like sleep, nutrition, exercise, and stress, mental health basics, preventive care, and first aid guidance.
You cannot help with: diagnosing specific conditions, interpreting lab reports or scans, recommending prescription or over-the-counter drugs by name, advising on a doctor's existing treatment plan, pediatric-specific medical advice, or surgical and procedural questions. When a question falls outside this scope, say so honestly and point the user toward the right resource.

LANGUAGE & ACCENT (CRITICAL FOR TTS)
- Detect the language the user is speaking.
- IF THE USER SPEAKS IN HINDI OR HINGLISH: You MUST respond in pure, natural, conversational Hindi using DEVANAGARI SCRIPT (हिंदी देवनागरी लिपि).
- CRITICAL FOR TTS VOICE ACCENT: ALWAYS write Hindi words in Devanagari script (e.g. "नमस्ते", "आपको क्या तकलीफ है?", "दर्द कितना तेज है?"). NEVER use English/Latin script (Hinglish/Roman Hindi) for Hindi words, because Latin script forces the TTS engine to speak with an unnatural, foreign accent. Writing in Devanagari ensures 100% natural, fluent, and native Hindi pronunciation and accent.
- IF THE USER SPEAKS IN ENGLISH: Reply in clear, warm, natural English.
- Match the user's level of formality — polite and empathetic at all times.

GUARDRAILS
Never diagnose a condition, even if the symptoms seem obvious. Never name or recommend a prescription drug under any circumstances. Never tell a user their symptoms are not serious or that they do not need a doctor. Never claim to be a doctor or a medical professional.

Escalation — use the right tier:
- Emergency (chest pain, difficulty breathing, stroke signs, severe bleeding, suicidal intent): "यह एक मेडिकल इमरजेंसी लग रही है। कृपया तुरंत इमरजेंसी सेवाओं को कॉल करें या निकटतम अस्पताल जाएं। देरी न करें।"
- Serious but non-emergency (high fever over two days, persistent unexplained pain, neurological symptoms): "इन लक्षणों के लिए डॉक्टर से व्यक्तिगत रूप से परामर्श लेना आवश्यक है। कृपया आज या कल में डॉक्टर को दिखाएं।"
- Out of scope (lab results, prescriptions, children's health, surgery): "आपके डॉक्टर या फार्मासिस्ट इस बारे में सही जानकारी दे सकते हैं क्योंकि वे आपकी मेडिकल हिस्ट्री जानते हैं।"

STYLE
Keep responses to one or two short sentences per turn — this is a voice conversation. Speak at a calm, unhurried pace — users reaching out about health are often anxious. If the user pauses or goes quiet, give them a beat before prompting — do not rush to fill silence. Use no filler phrases like "Great question" or "Absolutely". Be warm and grounded — like a trusted friend who happens to know a lot about health, not a clinical robot.

The opening greeting is played automatically when the call starts — do not repeat it. If the user greets you back, respond naturally and move straight to their concern.

When the user says goodbye, their concern is resolved, or they signal the conversation is ending (words like "thanks", "bye", "that's all", "I'm good now"), close in Hindi: "आशा है यह जानकारी आपके काम आएगी। अपना ख्याल रखिएगा — और कोई सवाल हो तो जरूर पूछिएगा। बाय!"
"""


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)

    # To add tools, use the @function_tool decorator.
    # Here's an example that adds a simple weather tool.
    # You also have to add `from livekit.agents import function_tool, RunContext` to the top of this file
    # @function_tool
    # async def lookup_weather(self, context: RunContext, location: str):
    #     """Use this tool to look up current weather information in the given location.
    #
    #     If the location is not supported by the weather service, the tool will indicate this. You must tell the user the location's weather is unavailable.
    #
    #     Args:
    #         location: The location to look up weather information for (e.g. city name)
    #     """
    #
    #     logger.info(f"Looking up weather for {location}")
    #
    #     return "sunny with a temperature of 70 degrees."


server = AgentServer()


def prewarm(proc: JobProcess):
    # 8 kHz halves per-frame compute vs 16 kHz; Silero supports both.
    # Raising activation_threshold slightly reduces false-positive frames
    # that would otherwise queue up and cause the "slower than realtime" backlog.
    proc.userdata["vad"] = silero.VAD.load(
        sample_rate=8000,
        activation_threshold=0.6,
    )


server.setup_fnc = prewarm


@server.rtc_session(agent_name="Saamiksha")
async def my_agent(ctx: JobContext):
    # Logging setup
    # Add any other context you want in all log entries here
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    # Set up a voice AI pipeline using Murf Falcon, Gemini, Deepgram, and the LiveKit turn detector
    session = AgentSession(
        # Speech-to-text (STT) is your agent's ears, turning the user's speech into text that the LLM can understand
        # See all available models at https://docs.livekit.io/agents/models/stt/
        stt=deepgram.STT(model="nova-3", language="multi"),
        # A Large Language Model (LLM) is your agent's brain, processing user input and generating a response
        # See all available models at https://docs.livekit.io/agents/models/llm/
        llm=google.LLM(
                model="gemini-3.5-flash-lite",
        ),
        # Text-to-speech (TTS) is your agent's voice, turning the LLM's text into speech that the user can hear
        # See all available models as well as voice selections at https://docs.livekit.io/agents/models/tts/
        tts=murf.TTS(
                voice="Anisha",
                locale="hi-IN",
                style="Conversation",
                tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
                text_pacing=True
            ),
        # VAD and turn detection are used to determine when the user is speaking and when the agent should respond
        # See more at https://docs.livekit.io/agents/build/turns
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        # allow the LLM to generate a response while waiting for the end of turn
        # See more at https://docs.livekit.io/agents/build/audio/#preemptive-generation
        preemptive_generation=True,
    )

    # To use a realtime model instead of a voice pipeline, use the following session setup instead.
    # (Note: This is for the OpenAI Realtime API. For other providers, see https://docs.livekit.io/agents/models/realtime/))
    # 1. Install livekit-agents[openai]
    # 2. Set OPENAI_API_KEY in .env.local
    # 3. Add `from livekit.plugins import openai` to the top of this file
    # 4. Use the following session setup instead of the version above
    # session = AgentSession(
    #     llm=openai.realtime.RealtimeModel(voice="marin")
    # )

    # # Add a virtual avatar to the session, if desired
    # # For other providers, see https://docs.livekit.io/agents/models/avatar/
    # avatar = hedra.AvatarSession(
    #   avatar_id="...",  # See https://docs.livekit.io/agents/models/avatar/plugins/hedra
    # )
    # # Start the avatar and wait for it to join
    # await avatar.start(session, room=ctx.room)

    # Start the session, which initializes the voice pipeline and warms up the models
    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: (
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind
                    == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else noise_cancellation.BVC()
                ),
            ),
        ),
    )

    # Join the room and connect to the user
    await ctx.connect()

    # Proactively greet the user — bypasses LLM so it fires immediately on join
    await session.say(
        "नमस्ते! मैं आन्या हूँ, आपकी हेल्थ एडवाइजर। "
        "मैं आपकी सेहत से जुड़े किसी भी सवाल में मदद करने के लिए यहाँ हूँ। "
        "आज आपकी क्या सहायता कर सकती हूँ?"
    )


if __name__ == "__main__":
    cli.run_app(server)
