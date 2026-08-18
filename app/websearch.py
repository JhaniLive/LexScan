"""Live web lookup through DuckDuckGo.

Used for two things the model cannot know: what happened recently, and what a
particular state's rules currently are. Results are passed to the model as
quoted sources with URLs so answers can be attributed rather than asserted.
"""

import asyncio

DEFAULT_REGION = "in-en"


class SearchUnavailable(RuntimeError):
    pass


def _search_sync(query, max_results, kind, region, timelimit):
    try:
        from ddgs import DDGS
    except ImportError:
        raise SearchUnavailable(
            "Web search needs the `ddgs` package — install it with "
            "`pip install ddgs`."
        )

    try:
        with DDGS() as engine:
            if kind == "news":
                rows = engine.news(
                    query, region=region, max_results=max_results, timelimit=timelimit
                )
            else:
                rows = engine.text(query, region=region, max_results=max_results)
    except Exception as e:
        raise SearchUnavailable(f"DuckDuckGo did not answer: {e}")

    results = []
    for row in rows or []:
        results.append(
            {
                "title": (row.get("title") or "").strip(),
                "url": (row.get("href") or row.get("url") or "").strip(),
                "body": (row.get("body") or row.get("excerpt") or "").strip(),
                "date": (row.get("date") or "").strip(),
                "source": (row.get("source") or "").strip(),
            }
        )
    return [r for r in results if r["url"]]


async def search(query, max_results=6, kind="text", region=DEFAULT_REGION, timelimit=None):
    """Search the web. `kind` is "text" or "news". Raises SearchUnavailable."""
    return await asyncio.to_thread(
        _search_sync, query, max_results, kind, region, timelimit
    )


def as_sources(results):
    """Format results as a numbered source block for the model to quote from."""
    if not results:
        return "No search results were returned."

    blocks = []
    for index, row in enumerate(results, 1):
        stamp = f" ({row['date'][:10]})" if row["date"] else ""
        origin = f" — {row['source']}" if row["source"] else ""
        body = row["body"][:600]
        blocks.append(
            f"[{index}] {row['title']}{origin}{stamp}\n{row['url']}\n{body}"
        )
    return "\n\n".join(blocks)


def as_links(results):
    """Format results as a markdown list for the user to click."""
    lines = []
    for index, row in enumerate(results, 1):
        stamp = f" · {row['date'][:10]}" if row["date"] else ""
        origin = f" · {row['source']}" if row["source"] else ""
        lines.append(f"{index}. [{row['title'] or row['url']}]({row['url']}){origin}{stamp}")
    return "\n".join(lines)
