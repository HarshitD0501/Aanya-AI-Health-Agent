# Backend — Voice Agent with Murf Falcon TTS

The Python backend for the Voice Agent Starter. It runs a real-time voice AI pipeline using [LiveKit Agents](https://docs.livekit.io/agents), connecting Murf Falcon TTS, Deepgram STT, and Google Gemini into a single conversational agent.

## How It Works

```
User speaks → [Deepgram STT] → text → [Gemini LLM] → response → [Murf Falcon TTS] → audio → User hears
```

LiveKit handles the real-time audio transport. The agent connects to LiveKit as a participant, listens for user speech, and responds with synthesized audio.

The same pipeline also runs **outbound**, where the agent dials a real phone over a Twilio SIP trunk instead of waiting to be called — see [Day 6](#day-6--outbound-medication-reminder-calls).

## Setup

### 1. Install dependencies

```bash
cd backend
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env.local
```

Fill in your keys in `.env.local`:

| Variable               | Where to get it                                          |
| ---------------------- | -------------------------------------------------------- |
| `LIVEKIT_URL`        | [LiveKit Cloud](https://cloud.livekit.io/) → Settings    |
| `LIVEKIT_API_KEY`    | [LiveKit Cloud](https://cloud.livekit.io/) → Settings    |
| `LIVEKIT_API_SECRET` | [LiveKit Cloud](https://cloud.livekit.io/) → Settings    |
| `MURF_API_KEY`       | [murf.ai/api/dashboard](https://murf.ai/api/dashboard)    |
| `DEEPGRAM_API_KEY`   | [deepgram.com](https://console.deepgram.com/)             |
| `GOOGLE_API_KEY`     | [aistudio.google.com](https://aistudio.google.com/apikey) |

For LiveKit Cloud users, you can auto-populate LiveKit credentials:

```bash
lk cloud auth
lk app env -w -d .env.local
```

### 3. Download models

```bash
uv run python src/agent.py download-files
```

This downloads Silero VAD and the LiveKit turn detector models.

### 4. Run the agent

```bash
# Development mode (auto-reload)
uv run python src/agent.py dev

# Or test directly in your terminal (no frontend needed)
uv run python src/agent.py console

# Production
uv run python src/agent.py start
```

## Configuration

All configuration lives in [`src/agent.py`](src/agent.py).

### System prompt

The `SYSTEM_PROMPT` constant at the top of `agent.py` controls what your agent does. Change it to build any voice-powered use case.

#### Example prompts

**Customer Support (default):**

```
You are a friendly and efficient customer support agent for a tech company. Help users with account issues, billing questions, and product troubleshooting. Be concise, empathetic, and solution-oriented. If you don't know something, say so honestly and offer to escalate.
```

**Language Tutor:**

```
You are a patient and encouraging language tutor helping the user practice conversational Spanish. Speak primarily in Spanish but switch to English to explain grammar or vocabulary when needed. Correct mistakes gently and suggest better phrasing. Keep conversations natural and fun.
```

**AI Receptionist:**

```
You are a professional receptionist for a medical clinic. Help callers schedule appointments, answer questions about office hours and services, and take messages for doctors. Be warm but efficient. Ask for the caller's name and reason for calling upfront.
```

**Interview Coach:**

```
You are an experienced interview coach. Conduct mock interviews with the user for software engineering roles. Ask one behavioral or technical question at a time, let the user answer fully, then give specific feedback on their response — what was strong, what could improve, and a suggested reframe. Keep the tone encouraging but honest.
```

**Sales Assistant:**

```
You are a knowledgeable sales assistant for an electronics store. Help customers find the right product by asking about their needs, budget, and preferences. Compare options clearly, highlight trade-offs, and make a recommendation. Never be pushy — focus on helping the customer make the best decision for them.
```

**Fitness Coach:**

```
You are an upbeat personal fitness coach. Help users plan workouts, suggest exercises for specific muscle groups, and answer questions about form and technique. Ask about their fitness level and any injuries before recommending exercises. Keep instructions clear and motivating.
```

**Storyteller / Bedtime Narrator:**

```
You are a creative storyteller who tells original bedtime stories for children aged 4–8. Ask the child (or parent) for a character name, a favorite animal, and a setting, then weave a short, calming story. Use vivid but simple language. End each story on a peaceful, sleepy note.
```

**Meeting Summarizer:**

```
You are a meeting assistant. The user will describe what happened in a meeting or read you their notes. Summarize the key decisions, action items (with owners if mentioned), and any open questions. Be concise and structured. Ask clarifying questions if something is ambiguous.
```

**Trivia Game Host:**

```
You are an enthusiastic trivia game host. Ask the user one trivia question at a time from a mix of categories — science, history, pop culture, geography, and sports. Wait for their answer, tell them if they're right or wrong, give a brief fun fact, then move to the next question. Keep score and announce it every 5 questions.
```

**Mental Health Check-in Companion:**

```
You are a gentle, non-clinical wellness companion. Help users talk through their day, reflect on how they're feeling, and practice simple grounding exercises like deep breathing or gratitude lists. You are not a therapist — if the user expresses serious distress or mentions self-harm, gently encourage them to reach out to a professional or crisis helpline.
```

### Voice

Set the `voice` argument in the `murf.TTS(...)` call:

```python
tts=murf.TTS(
    voice="en-US-matthew",    # Change this
    style="Conversation",
    tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
    text_pacing=True
)
```

Some voice options:

| Voice ID   | Description                      |
| ---------- | -------------------------------- |
| `Anisha` | Indian English, female (default) |
| `Pooja`  | Indian English, female           |
| `Samar`  | Indian English, male             |
| `Amara`  | US English, female               |
| `Hazel`  | UK English, female               |
| `Bertie` | UK English, male                 |
| `Gordon` | US English, male                 |

Browse all 150+ voices: [Murf Voice Library](https://murf.ai/api/docs/voices-styles/voice-library).

### STT (Speech-to-Text)

Default is Deepgram Nova-3. Change in the `AgentSession(stt=...)` call:

```python
stt=deepgram.STT(model="nova-3")
```

### LLM

Default is Google Gemini. To switch:

- **Gemini (default):** Set `GOOGLE_API_KEY` in `.env.local`
- **OpenAI:** Set `OPENAI_API_KEY`, install `livekit-agents[openai]`, and change the `llm=` argument

## Day 6 — Outbound medication reminder calls

Days 1–5 waited for the user to call Aanya. Day 6 makes Aanya do the calling: she dials a real phone over Twilio, reminds the person about their medicine in their own language, records whether they took it, and stops calling forever the moment they ask her to.

Nothing about the inbound path changed. The two flows share one voice pipeline (`build_session`) and are told apart by a single fact — whether the LiveKit job arrived with metadata:

```
outbound.py  →  dispatch agent (job metadata)  →  dial SIP into the same room  →  agent.py: run_outbound_reminder()
inbound      →  no metadata                    →                                agent.py: existing Day 5 flow
```

The agent is dispatched **before** the number is dialled. Dispatching after the pickup would clip the opening line, and the opening is the one part of an outbound call that is not allowed to be missed.

### The opening line

An outbound callee never asked to be called, so Day 6 requires the first two sentences to state **who** is calling, **why**, and **how to stop**. That opening is built in code (`build_outbound_opening`) and spoken with `session.say()` rather than generated by the LLM — a sampled promise is not a promise.

```
नमस्ते Ramesh जी, मैं आन्या बोल रही हूँ — आपकी हेल्थ रिमाइंडर सेवा से। आपने Metformin के लिए
रात आठ बजे का रिमाइंडर सेट किया था, इसलिए यह कॉल की है। अगर आप ये कॉल बंद करवाना चाहें, तो बस
कहिए "रिमाइंडर बंद करें", मैं तुरंत बंद कर दूँगी। क्या आपने आज Metformin ले ली है?
```

Clock times are spelled out in words (`_spoken_time`) because Murf reads `20:00` as "twenty colon zero zero".

If the callee says any opt-out phrase — `"stop calling"`, `"बंद करो"`, `"रिमाइंडर बंद करें"`, `"मुझे कॉल न करें"` — the `opt_out_of_reminders` tool fires, the row is disabled, Aanya confirms it aloud and hangs up. She never argues and never asks why. The tool ignores whatever number the LLM passes and uses the number we actually dialled, because that one is authoritative.

### Twilio setup

Aanya talks SIP directly to Twilio through LiveKit, so there is **no Twilio SDK, Account SID or Auth Token** in this repo. What you need is an Elastic SIP Trunk.

1. Twilio Console → **Elastic SIP Trunking → Trunks** → create a trunk.
2. **Termination** tab → set a SIP URI: `<name>.pstn.twilio.com`.
3. **Termination → Credential Lists** → create one. You invent the username and password.
4. **Numbers** tab → attach your Twilio number to the trunk.
5. On a **trial account**, verify the destination mobile under **Phone Numbers → Verified Caller IDs**. Trials can only dial verified numbers (Twilio error `32100`).
6. Register the trunk with LiveKit:

```bash
cp outbound-trunk.example.json outbound-trunk.json   # then fill it in
lk sip outbound create outbound-trunk.json           # prints the ST_... trunk ID
```

`outbound-trunk.json` holds the credential-list password and is gitignored. Only the `REPLACE-ME` example is committed. Keep `destination_country: "in"` for Indian numbers — TRAI requires India voice traffic to anchor on Indian servers.

### Environment

| Variable                 | What it is                                                                     |
| ------------------------ | ------------------------------------------------------------------------------ |
| `SIP_OUTBOUND_TRUNK_ID`  | The `ST_...` ID printed by `lk sip outbound create`                            |
| `SIP_FROM_NUMBER`        | Caller ID shown to the callee — your Twilio number, E.164                      |
| `LIVEKIT_AGENT_NAME`     | Must match `agent_name` in `agent.py`'s `@server.rtc_session`, or the dispatch is never picked up |
| `REMINDER_TEST_NUMBER`   | Your own mobile, E.164. On a trial this must be a Verified Caller ID           |

### Setting a reminder by voice

Reminders do not have to come from the CLI. Asked "mujhe roz dawa yaad dila dijiye" on any call, Aanya collects three things one at a time — the medicine in the caller's own words, the daily time, and a mobile number — and calls `schedule_medicine_reminder`, which writes the same row `--register` would.

Three guards keep a bad row out of the store, because a reminder that cannot ring is worse than none:

- The spoken number is normalized to E.164 (`9454535137` → `+919454535137`); anything that cannot be dialled is refused and the caller is asked to repeat it.
- The time is coerced to 24-hour `HH:MM`, so `"8pm"` and `"8.30"` both land correctly.
- The same medicine at the same time on the same number is treated as a repeat of what they already asked for, not a second reminder.

`list_my_reminders` reads back what is already set. The medicine name is always the caller's own words — Aanya never suggests or completes one, since that would be prescribing.

### Usage

The agent worker has to be running in another terminal — the dialer only dispatches jobs, it does not host the session.

```bash
uv run python src/agent.py dev
```

Then:

```bash
# Register a daily reminder
uv run python src/outbound.py --register --to +919999999999 --name Ramesh \
    --medicine Metformin --dosage "1 tablet after dinner" --at 20:00 --language Hindi

# See what is registered
uv run python src/outbound.py --list

# Resolve and log a call without dialling — works with zero telephony configured
uv run python src/outbound.py --reminder-id 1 --dry-run

# Actually call
uv run python src/outbound.py --reminder-id 1

# Ad-hoc call, nothing saved to the database
uv run python src/outbound.py --to +919999999999 --name Ramesh --medicine Metformin
```

The scheduler owns only the clock. Every "should we dial this?" decision lives in `reminders.py`.

```bash
uv run python src/scheduler.py --once            # one pass over everything due now
uv run python src/scheduler.py --watch           # loop, default every 300s
uv run python src/scheduler.py --watch --interval 60 --window 5
uv run python src/scheduler.py --once --dry-run  # rehearse without dialling
```

Concurrency is capped at one call (`MAX_CONCURRENT_CALLS`): trial trunks allow very little, and two Aanyas talking over each other on one line is worse than a reminder arriving a minute late. A single call is capped at 180 seconds (`MAX_OUTBOUND_CALL_SEC`) so a wedged SIP leg cannot hold a channel open and drain trial credit.

### Call outcomes and retries

Every dial ends in exactly one outcome, and every outcome has a retry rule — a test asserts `RETRY_POLICY` covers all seven, so none can be silently dropped.

| Outcome              | How it is detected                                       | Retries       | Why                                        |
| -------------------- | -------------------------------------------------------- | ------------- | ------------------------------------------ |
| `answered`           | Callee joined and produced at least one transcript        | none          | Reminder delivered                         |
| `no_answer`          | SIP `408` / `480` / `487`, or ringing timeout (30s)       | 3× / 15 min   | They may simply be away from the phone     |
| `quick_hangup`       | Answered then dropped under 5s                           | 2× / 10 min   | Never engaged, so worth one more try       |
| `possible_voicemail` | Line answered but no transcript ever arrived             | none          | The message already landed on the machine  |
| `rejected`           | SIP `486` Busy Here / `603` Decline                      | none          | They declined; re-dialling is harassment   |
| `trunk_failure`      | Any other dial error                                     | 2× / 5 min    | Our problem, not theirs — back off and retry |
| `opted_out`          | `opt_out_of_reminders` fired mid-call                     | never again   | Overrides everything else                  |

Voicemail is detected without answering-machine detection: a SIP-answered line that never produces a transcript is a machine. Any transcript at all means a human engaged.

Outcome precedence matters. If the opt-out tool fired during the call, the outcome is `opted_out` even though the call was answered — recording `answered` would leave the row looking eligible to call again.

### Data that travels

Reminder job metadata carries only what the opening needs: name, medicine, dosage, time, language, phone, reminder ID. No health facts, no triage history, no transcripts. A test asserts this, because metadata is the one field that leaves the process.

Adherence is stored separately from the dial outcome — a delivered call where the person says "I forgot" still succeeded as a call. `confirm_medicine_taken` writes `taken` / `not_taken` plus an optional note to `last_medicine_response`.

## Testing

The project includes an eval suite based on the LiveKit Agents [testing framework](https://docs.livekit.io/agents/build/testing/):

```bash
uv run pytest
```

Tests are in [`tests/test_agent.py`](tests/test_agent.py) and use LLM-as-judge evaluations to verify the agent behaves correctly (friendly greetings, grounding, refusing harmful requests).

The Day 6 suite in [`tests/test_day6_outbound.py`](tests/test_day6_outbound.py) needs **no Twilio trunk and no LiveKit connection**: dial failures are simulated by feeding `classify_dial_error` the exception text LiveKit produces, and the opening line is asserted directly.

```bash
uv run pytest tests/test_day6_outbound.py -q
```

To run tests in CI, you'll need to add `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` as repository secrets.

## Deployment

### Railway

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/tIVCF1?referralCode=cNjn2P&utm_medium=integration&utm_source=template&utm_campaign=generic)

Set these environment variables in Railway:

- `MURF_API_KEY`
- `DEEPGRAM_API_KEY`
- `GOOGLE_API_KEY`
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`

### Docker

A production-ready [Dockerfile](Dockerfile) is included:

```bash
docker build -t murf-voice-agent .
docker run --env-file .env.local murf-voice-agent
```

## Project Structure

```
backend/
├── src/
│   ├── agent.py           # Agent entrypoint — pipeline, prompts, inbound + outbound sessions
│   ├── db.py              # SQLite store + idempotent schema migrations
│   ├── health_services.py # Day 5 tools — facilities, helplines
│   ├── reminders.py        # Reminder CRUD, outcomes, retry policy, due-window logic
│   ├── outbound.py         # Day 6 dialer — dispatch + SIP call, CLI
│   └── scheduler.py        # Day 6 clock — finds due reminders, dials them one at a time
├── tests/
│   ├── test_agent.py           # LLM-judged eval suite
│   ├── test_day4_memory.py     # Caller memory
│   ├── test_day5_tools.py      # Health service tools
│   └── test_day6_outbound.py   # Reminders, retries, dial classification, opening line
├── .env.example                 # Environment variable template
├── outbound-trunk.example.json  # Twilio SIP trunk template (real one is gitignored)
├── pyproject.toml               # Python dependencies (uv)
├── Dockerfile                   # Production container
└── railway.toml                 # Railway deploy config
```

## Links

- [Murf Falcon TTS Docs](https://murf.ai/api/docs/text-to-speech/streaming)
- [Murf Voice Library](https://murf.ai/api/docs/voices-styles/voice-library)
- [LiveKit Agents Docs](https://docs.livekit.io/agents)
- [LiveKit SIP — outbound calls](https://docs.livekit.io/sip/outbound-calls/)
- [Twilio Elastic SIP Trunking](https://www.twilio.com/docs/sip-trunking)
- [Deepgram Nova-3 Docs](https://developers.deepgram.com)

## License

MIT — see [LICENSE](LICENSE).
