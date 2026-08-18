"""Live check against your LLM. Run this before starting the app.

    python test_llm.py           # config -> connectivity -> streaming -> agents
    python test_llm.py --raw     # also dump the raw response body
    python test_llm.py --probe   # try common endpoint paths to find the right one

If a check fails, the error tells you which piece of app/llm.py to adjust.
"""

import sys
import json
import time
import asyncio

import httpx

from app import llm
from app.agents.triage import triage_agent
from app.agents.summarizer import summarizer_agent

SAMPLE = """SERVICES AGREEMENT

This Agreement is made on 3 March 2025 between Northwind Ltd ("Client") and
Orbit Studio Pvt Ltd ("Supplier").

1. Services. Supplier shall provide website design and maintenance services.
2. Term. This Agreement runs for 12 months and renews automatically for further
   12-month terms unless either party gives written notice at least 90 days
   before the end of the then-current term.
3. Fees. Client shall pay INR 2,00,000 per month within 15 days of invoice.
   Late payments carry interest at 18% per annum.
4. Termination. Client may terminate for convenience on 30 days notice.
   Supplier may terminate only for material breach not cured within 60 days.
5. Liability. Supplier's liability is capped at one month's fees. Client's
   liability is not capped.
6. Indemnity. Supplier shall indemnify Client against all claims arising from
   the Services.
7. Governing law. This Agreement is governed by the laws of India and the
   courts at Mumbai have exclusive jurisdiction.
"""

PROBE_PATHS = [
    "/chat/completions",
    "/v1/chat/completions",
    "/completions",
    "/generate",
    "/v1/generate",
    "/api/chat",
    "/v1/messages",
]

# Windows consoles default to cp1252; model output is not.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OK = "[ok]"
FAIL = "[fail]"


def show_config():
    key = llm.API_KEY
    masked = f"{key[:6]}...{key[-4:]}" if len(key) > 12 else ("set" if key else "MISSING")
    print("Configuration")
    print(f"  provider     {llm.PROVIDER}")
    print(f"  endpoint     {llm.adapter.url() or 'MISSING'}")
    print(f"  api root     {llm.BASE_URL or 'MISSING'}")
    print(f"  secret       {masked}")
    print(f"  model        {llm.DEFAULT_MODEL or 'MISSING'}")
    print(f"  offered      {', '.join(llm.AVAILABLE_MODELS) or '(none discovered)'}")
    print(f"  vision model {llm.VISION_MODEL or '(none — scans/photos disabled)'}")
    print()

    url_var = "LOCAL_LLM_URL" if llm.PROVIDER == "local" else "LLM_BASE_URL"
    key_var = "LOCAL_LLM_SECRET" if llm.PROVIDER == "local" else "LLM_API_KEY"
    missing = [
        name
        for name, value in (
            (url_var, llm.adapter.url()),
            (key_var, llm.API_KEY),
        )
        if not value
    ]
    if missing:
        print(f"{FAIL} Fill these in 03_LexScan/.env: {', '.join(missing)}")
        return False
    if not llm.DEFAULT_MODEL:
        print(f"{FAIL} The endpoint listed no usable model and .env names none.")
        print("   -> set LLM_MODEL in 03_LexScan/.env")
        return False
    return True


async def probe_paths():
    """Try the usual endpoint paths and report what each returns."""
    print("Probing endpoint paths")
    body = llm.adapter.body(
        [{"role": "user", "content": "ping"}], llm.DEFAULT_MODEL, stream=False
    )
    async with httpx.AsyncClient(timeout=30) as client:
        for path in PROBE_PATHS:
            url = f"{llm.BASE_URL}{path}"
            try:
                response = await client.post(url, headers=llm.adapter.headers(), json=body)
                snippet = response.text[:160].replace("\n", " ")
                mark = OK if response.status_code < 400 else "    "
                print(f"  {mark} {response.status_code}  {url}\n        {snippet}")
            except Exception as e:
                print(f"       ---  {url}\n        {type(e).__name__}: {e}")
    print()


async def check_complete(show_raw):
    print("1. Non-streaming call")
    messages = [
        {"role": "system", "content": "You answer in exactly one word."},
        {"role": "user", "content": "Reply with the word READY."},
    ]
    started = time.time()
    try:
        if show_raw:
            async with httpx.AsyncClient(timeout=llm.TIMEOUT) as client:
                response = await client.post(
                    llm.adapter.url(),
                    headers=llm.adapter.headers(),
                    json=llm.adapter.body(messages, llm.DEFAULT_MODEL, stream=False),
                )
                print(f"   HTTP {response.status_code}")
                try:
                    print("   raw:", json.dumps(response.json(), indent=2)[:1500])
                except ValueError:
                    print("   raw:", response.text[:1500])

        text = await llm.complete(messages)
        print(f"   {OK} {time.time() - started:.1f}s -> {text.strip()[:80]}")
        return True
    except Exception as e:
        print(f"   {FAIL} {type(e).__name__}: {e}")
        print("   -> check LLM_BASE_URL / LLM_API_KEY, or fix url()/headers()/body()")
        print("     in CustomAdapter (app/llm.py). Run with --probe to find the path.")
        return False


async def check_stream():
    print("2. Streaming call")
    messages = [{"role": "user", "content": "Count from 1 to 5, separated by spaces."}]
    started = time.time()
    chunks = 0
    text = ""
    try:
        async for delta in llm.stream(messages):
            chunks += 1
            text += delta
        if not chunks:
            print(f"   {FAIL} connected but no deltas parsed")
            print("   -> your stream isn't OpenAI SSE shape; fix delta_from_event()")
            print("     in CustomAdapter (app/llm.py)")
            return False
        print(f"   {OK} {chunks} chunks in {time.time() - started:.1f}s -> {text.strip()[:80]}")
        return True
    except Exception as e:
        print(f"   {FAIL} {type(e).__name__}: {e}")
        return False


async def check_agents():
    print("3. Agents on a sample contract")
    try:
        started = time.time()
        triage = await llm.run_agent(triage_agent, SAMPLE)
        print(f"   {OK} triage ({time.time() - started:.1f}s)")
        for line in triage.strip().splitlines()[:3]:
            print(f"      {line}")

        started = time.time()
        summary = ""
        async for delta in llm.stream_agent(summarizer_agent, SAMPLE):
            summary += delta
        print(f"   {OK} summary ({time.time() - started:.1f}s, {len(summary)} chars)")
        print("      " + "\n      ".join(summary.strip().splitlines()[:6]))

        # The sample has an uncapped-liability trap; a working model should see it.
        if "cap" not in summary.lower() and "liab" not in summary.lower():
            print("   note: summary missed the liability terms -- the model may be weak")
        return True
    except Exception as e:
        print(f"   {FAIL} {type(e).__name__}: {e}")
        return False


async def main():
    show_raw = "--raw" in sys.argv
    probe = "--probe" in sys.argv

    print("LexScan -- LLM check\n")
    if not show_config():
        return 1

    if probe:
        await probe_paths()

    if not await check_complete(show_raw):
        return 1
    if not await check_stream():
        return 1
    if not await check_agents():
        return 1

    print("\nAll checks passed. Start the app with:")
    print("  venv\\Scripts\\chainlit run chainlit_app.py -w")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
