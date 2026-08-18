"""Live check of situation mode, drafting and news search.

    python test_situation.py

Hits the real LLM and the real web, so it takes a few minutes on a slow model.
"""

import sys
import time
import asyncio

from app import llm
from app.agents.situation import situation_agent
from app.agents.drafter import drafter_agent
from app.agents.news import news_agent
from app.guardrails import fence, inspect_legal_answer, format_problems
from app.india import detect_state, detect_areas, brief, KNOWN_STATUTES, DRAFT_TYPES
from app.websearch import search, as_sources, SearchUnavailable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SITUATION = (
    "I am 27 and I work in Bengaluru. I vacated my rented flat on 30 June 2026 "
    "after giving one month's written notice as the agreement required. My "
    "landlord has still not returned my security deposit of Rs 80,000. He keeps "
    "saying he will pay next month. I have the rent agreement, the bank "
    "statements showing every rent payment, and WhatsApp messages where he "
    "admits he owes me the deposit. What are my options?"
)


async def collect(agent, prompt, model=None):
    parts = []
    async for delta in llm.stream_agent(agent, prompt, model=model):
        parts.append(delta)
    return "".join(parts)


async def main():
    print("LexScan -- situation mode check\n")

    state = detect_state(SITUATION)
    areas = detect_areas(SITUATION)
    print(f"routing      state={state} areas={areas}")
    assert state == "Karnataka", state
    assert "tenancy" in areas, areas

    reference = brief(areas, state)
    print(f"reference    {len(reference)} chars of grounding notes\n")

    # ── 1. the situation pass ──
    print("1. Situation analysis")
    started = time.time()
    prompt = f"{reference}\n\nTHE PERSON'S SITUATION:\n{fence(SITUATION)}"
    analysis = await collect(situation_agent, prompt)
    print(f"   [ok] {time.time() - started:.0f}s, {len(analysis)} chars")

    headings = [h for h in ("The law that applies", "Do these now", "Deadlines",
                            "Evidence", "still need to know") if h.lower() in analysis.lower()]
    print(f"   sections present: {len(headings)}/5 -> {headings}")
    for line in analysis.strip().splitlines()[:8]:
        print(f"      {line[:100]}")

    problems = inspect_legal_answer(analysis, KNOWN_STATUTES)
    print(f"   guardrails: {problems or 'clean'}")

    # ── 2. the draft ──
    print("\n2. Drafting a legal notice")
    started = time.time()
    prompt = (
        f"{reference}\n\nDOCUMENT TO DRAFT: {DRAFT_TYPES['legal-notice']}\n"
        f"STATE: {state}\n\nTHE FACTS:\n{fence(SITUATION)}\n\n"
        f"YOUR EARLIER ANALYSIS:\n{analysis[:4000]}"
    )
    draft = await collect(drafter_agent, prompt)
    print(f"   [ok] {time.time() - started:.0f}s, {len(draft)} chars, "
          f"{draft.count('[')} bracketed blanks")
    for line in draft.strip().splitlines()[:10]:
        print(f"      {line[:100]}")
    if "80,000" in draft or "80000" in draft:
        print("   [ok] carried the actual amount into the draft")
    else:
        print("   note: the deposit amount did not make it into the draft")

    # ── 3. news, from the live web ──
    print("\n3. Live news search")
    started = time.time()
    query = "Supreme Court of India latest news"
    try:
        results = await search(query, max_results=5, kind="news", timelimit="m")
        if len(results) < 3:
            results += await search(query, max_results=5, kind="text")
        print(f"   [ok] {len(results)} results in {time.time() - started:.0f}s")
        for r in results[:3]:
            print(f"      - {r['title'][:70]} | {r['url'][:55]}")

        started = time.time()
        answer = await collect(news_agent, f"QUESTION: {query}\n\nSOURCES:\n{as_sources(results)}")
        print(f"   [ok] summarised in {time.time() - started:.0f}s, {len(answer)} chars")
        cited = sum(1 for i in range(1, len(results) + 1) if f"[{i}]" in answer)
        print(f"   cites {cited}/{len(results)} sources")
        for line in answer.strip().splitlines()[:6]:
            print(f"      {line[:100]}")
    except SearchUnavailable as e:
        print(f"   [fail] {e}")
        return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
