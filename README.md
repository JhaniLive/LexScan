# LexScan

Legal document review and situation help for India.

Runs on **your own LLM** — no vendor SDK, no agent framework. One HTTP client,
ten prompt-defined agents, a Chainlit chat UI.

Two ways in: upload a document, or describe what happened to you.

## Describe a situation

Type the problem in your own words and LexScan works out what kind of legal
matter it is, which state's law bears on it, and what to do about it:

**What this is** · **The law that applies** (a table of statutes, and whether
each is central or state) · **Do these now** (numbered steps naming the actual
office or portal, with deadlines) · **Deadlines** · **Evidence to gather now** ·
**What I still need to know** (the questions that would change the advice).

Answer those questions and it refines rather than repeating itself. Two things
make this better than asking a chatbot:

- **State awareness.** Rent, police procedure, land records, stamp duty and
  shops-and-establishments rules are state subjects. Name a city and LexScan
  maps it to the state (`app/india.py`); where the state matters and isn't
  known, it asks before naming a statute.
- **A grounded reference map.** Each issue area carries the statutes, the forum
  and the helpline that actually apply, handed to the model as reference notes
  rather than left to recall. It also knows India recodified its criminal law on
  1 July 2024 — IPC/CrPC/Evidence Act became BNS/BNSS/BSA, and offences before
  that date are still tried under the old codes.

### Then get the paperwork

```
/draft police-complaint      /draft legal-notice        /draft rti
/draft sp-complaint          /draft statement           /draft tribunal-application
/draft consumer-complaint
```

Drafts come out in a code block with a copy button — correct addressee, subject
line, numbered facts with dates, the specific relief asked for, signature block
and enclosures. Missing facts become labelled blanks (`[DATE OF INCIDENT]`,
`[POLICE STATION]`) rather than invented details.

### Speak instead of typing

Press the mic and your words appear **as you speak** — the caption builds live
in the chat, roughly a second behind you. **faster-whisper runs on this machine**,
so no audio leaves the box, which matters when someone is describing a police
matter or a family dispute out loud.

Two models do the work. A small one re-transcribes a rolling window every ~1.6
seconds to draw the caption, which is why words appear while you are still
talking; it costs about half of real time on CPU, so it keeps up. When you stop,
the accurate model makes one clean pass over the whole recording, and *that* is
the text LexScan acts on — the caption settles into it. Live guesses never drive
the answer, and a mishearing is visible before anything happens.

Transcribed text goes through exactly the same routing as anything typed, so
voice reaches document questions, situation mode, drafting and news alike.

Whisper is multilingual and auto-detects by default — set `STT_LANGUAGE=hi` (or
`kn`, `ta`, `te`, `mr`, `bn`) in `.env` to pin it, and `STT_MODEL` to trade speed
for accuracy (`tiny` → `medium`).

### Check what's current

`/news <topic>` — or just ask *"what's happening in the courts right now"* —
searches DuckDuckGo and answers from what comes back, with a source number
after every claim and the links listed underneath. A model's training is always
older than the news; this closes that gap.

## Review a document

You drop in a contract, NDA, lease, employment agreement, policy or notice as
`.pdf`, `.docx`, `.txt`/`.md`, or a photo/scan of the pages. LexScan reads it and
streams back, in order:

| Pass | Output |
| --- | --- |
| Triage | Document type, title, parties |
| Executive Summary | One-liner, the deal in plain English, key-facts table |
| Key Clauses | Clause-by-clause table with who each one favours |
| Obligations & Dates | Who must do what by when, plus a diary list of deadlines |
| Risks & Red Flags | Severity-ranked risk register, plus missing protections |
| Negotiation Brief | Prioritised asks with suggested wording, questions to ask |

Then you just talk to it — *"can they terminate early?"*, *"what am I liable
for?"* — and it answers from the document with the clause quoted back. Rerun any
single pass with `/summary` `/clauses` `/dates` `/risks` `/negotiate`.

Scans and phone photos are OCR'd locally — no vision model needed. Documents too
long for one context window are chunked, condensed clause-by-clause in parallel,
then analysed as a digest, so a 200-page agreement still works.

## Setup

```bash
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env      # then fill it in
```

Point `.env` at your organisation's LLM — two variables is the whole setup:

```ini
LOCAL_LLM_URL=https://llm.your-org.example/v1/chat/completions
LOCAL_LLM_SECRET=...
```

`LOCAL_LLM_URL` is the full endpoint, path included; the secret goes in as a
bearer token. LexScan asks that endpoint's `/models` list what it serves, drops
the embedding-only entries, and offers the rest in the model dropdown — so a new
model on the server needs no `.env` edit. Pin one with `LLM_MODEL=qwen3.5:9b` if
you'd rather not let it choose.

Photos and scans are handled locally. LexScan checks a PDF for a text layer
and, finding none, renders each page and reads it with a bundled OCR engine
(RapidOCR, ONNX) — no vision model and no model server involved, so it works
with your text-only endpoint. Photos and screenshots go the same way. If a
vision model does become available, name it in `LLM_VISION_MODEL` and it is
used as a fallback when local OCR is not installed.

Image formats: **JPG, PNG, HEIC** (iPhone), **WEBP, TIFF, BMP, GIF, JFIF, AVIF**.
The ones OpenCV can't open are converted first; very large phone photos are
capped at 4000px. Small screenshots are deliberately left at their own size —
upscaling was measured to make recognition worse, not better.

To point somewhere else instead, set `LLM_PROVIDER=openai` with `LLM_BASE_URL`
and `LLM_API_KEY` for any other OpenAI-compatible endpoint, or
`LLM_PROVIDER=custom` and edit the five marked methods in `CustomAdapter`
(`app/llm.py`) — the request URL, the auth header, the request body, and how to
pull text out of a normal and a streamed response. Nothing else in the project
knows or cares which LLM you use.

## Guardrails

An uploaded document is untrusted input — anyone who can put a PDF in front of
this app can put text in it addressed to the model rather than to the reader.
`app/guardrails.py` sits on both sides of every agent call.

**Going in**

- **Injection detection.** Ten patterns for the shapes that matter: instruction
  overrides, role reassignment, suppression directives (*"do not mention the
  liability cap"*), planted conclusions (*"say this contract is fair"*), fake
  role tags. A hit doesn't block the scan — it warns you, quotes the offending
  line, and the analysis continues with the document treated as suspect.
- **Fencing.** Document text is wrapped in explicit markers and prefixed with an
  instruction that it is material under review, not orders — and that any line
  inside it addressed to the model should be quoted and flagged as a red flag,
  never obeyed. Reinforced in every agent's system prompt.
- **Size and emptiness.** Documents too short to analyse or too large to process
  are rejected with a reason rather than sent to the model.

**Coming back**

- **Citation verification.** Every `(Clause 7.2)` and `(Page 3)` in an answer is
  checked against the actual document. Invented references are called out under
  the section that made them. This is the failure that matters most — a
  confident citation to a clause that doesn't exist survives casual checking.
- **Advice detection.** *"You should sign"*, *"you will win"*, *"I guarantee"*,
  *"no need for a lawyer"* — flagged as decisions that belong to you and your
  lawyer, not to a document reviewer.
- **Leakage detection.** Reasoning tags, echoed instructions, echoed fences, and
  near-empty responses.
- **Question handling.** *"Should I sign?"* and *"will I win?"* are answered as
  questions about the text, with the line between reading and advising drawn
  explicitly.

Every check is deterministic — regex and set arithmetic, no model calls. That is
deliberate: a guard costing another two-minute round trip would get switched
off, and a guard made of prompts can be talked out of it by a crafted document.

## Test it before running

```bash
venv\Scripts\python test_llm.py           # config → connect → stream → agents
venv\Scripts\python test_llm.py --raw     # dump the raw response body
venv\Scripts\python test_llm.py --probe   # find the right endpoint path
venv\Scripts\python test_guardrails.py    # 40 guardrail + routing checks, no LLM
venv\Scripts\python test_situation.py     # live: situation, drafting, news search
```

Each `test_llm.py` failure names the method in `CustomAdapter` to fix.

## Run

```bash
venv\Scripts\chainlit run chainlit_app.py -w
```

Then upload `samples/services-agreement.txt` — it's a deliberately one-sided
contract (uncapped client liability, a 90-day renewal trap, a one-way indemnity)
so you can see whether the risk pass actually earns its keep.

## Layout

```
app/
  agent_core.py       Agent = a name + a system prompt. That's the whole framework.
  llm.py              HTTP client + adapters + model discovery + OCR +
                      long-document condensing
  guardrails.py       injection detection, fencing, citation verification
  india.py            states, cities, issue-area statute map, draft types
  speech.py           local speech-to-text for the mic button
  websearch.py        DuckDuckGo lookup for anything current
  doc_loader.py       PDF / DOCX / text / image → plain text, chunking, stats
  agents/             one file per pass: triage, summarizer, clauses,
                      obligations, risk, advisor, qa
  local_data_layer.py file-based chat history
chainlit_app.py       upload → prepare → scan → follow-up Q&A
test_llm.py           live check against your LLM
samples/              a contract to test with
```

## Note

LexScan is a document reviewer, not a lawyer. It reports what a document says
and flags what looks dangerous; it does not give legal advice, and every output
carries that disclaimer.
