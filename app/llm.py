"""Thin async LLM client — no vendor SDK, just HTTP.

Everything the app needs from a model lives behind two calls: `run_agent` (one
shot) and `stream_agent` (token deltas). Which endpoint those hit is decided by
an adapter, selected with LLM_PROVIDER in .env:

    LLM_PROVIDER=local    → the organisation's own LLM (LOCAL_LLM_URL +
                            LOCAL_LLM_SECRET); this is the default whenever
                            LOCAL_LLM_URL is set
    LLM_PROVIDER=openai   → any OpenAI-compatible /chat/completions endpoint
    LLM_PROVIDER=custom   → an endpoint with its own shape; see CustomAdapter

Adding a provider means writing one adapter class, nothing else.
"""

import os
import re
import json
import base64
import asyncio

import httpx
from dotenv import load_dotenv

from .agent_core import Agent

load_dotenv()

# ── the organisation's own LLM ────────────────────────────────────────
# LOCAL_LLM_URL is the FULL endpoint, path included, exactly as handed out:
#   https://llm.your-org.example/v1/chat/completions
LOCAL_URL = os.getenv("LOCAL_LLM_URL", "").strip().rstrip("/")
LOCAL_SECRET = os.getenv("LOCAL_LLM_SECRET", "").strip()

# The API root, for /models discovery: the URL minus its /chat/completions tail.
LOCAL_ROOT = re.sub(r"/chat/completions/?$", "", LOCAL_URL)

# With LOCAL_LLM_URL set, the local LLM is the default provider — those two
# variables are all the configuration this app needs.
PROVIDER = os.getenv(
    "LLM_PROVIDER", "local" if LOCAL_URL else "openai"
).strip().lower()

BASE_URL = (os.getenv("LLM_BASE_URL", "").strip() or LOCAL_ROOT
            or "https://openrouter.ai/api/v1").rstrip("/")
API_KEY = (
    os.getenv("LLM_API_KEY")
    or LOCAL_SECRET
    or os.getenv("OPENROUTER_API_KEY")
    or ""
)
# Generous by default: this endpoint can take ~2 minutes to produce its first
# token on a cold or busy model, and the read timeout has to outlast that.
TIMEOUT = float(os.getenv("LLM_TIMEOUT", "600"))

# Reasoning models (qwen3.5, deepseek-r1) think before they answer, and on a
# long contract the thinking can consume the entire generation budget — leaving
# an empty answer after minutes of work. The hidden reasoning is never shown to
# the user anyway, so it is off by default. Set LLM_REASONING_EFFORT to
# low/medium/high to turn it back on, or "" to leave the field out entirely.
REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "none").strip()

# Hard ceiling on one answer. Without it a 9B model that loses the thread keeps
# generating word salad until the context runs out — minutes of streaming
# garbage. Every section of the scan fits comfortably inside this.
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "3000"))

# The server holds one model in VRAM, so switching models terminates the one
# already loaded and the request that triggered the swap fails. That is a cold
# start, not a real error — retry it.
RETRIES = int(os.getenv("LLM_RETRIES", "2"))

# Models the endpoint serves but that can't hold a conversation.
_EMBEDDING_HINTS = ("embed", "minilm", "rerank")


def discover_models(timeout=10.0) -> list:
    """Ask the endpoint what it serves. Returns [] if it won't say."""
    if not BASE_URL or not API_KEY:
        return []
    try:
        response = httpx.get(
            f"{BASE_URL}/models",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=timeout,
        )
        response.raise_for_status()
        served = [m.get("id", "") for m in (response.json().get("data") or [])]
    except Exception:
        return []
    return [
        m for m in served
        if m and not any(hint in m.lower() for hint in _EMBEDDING_HINTS)
    ]


# Models offered in the settings dropdown. Explicit config wins; otherwise ask
# the endpoint, so a new model on the server shows up without an .env edit.
_configured = [m.strip() for m in os.getenv("LLM_MODELS", "").split(",") if m.strip()]
_env_model = (os.getenv("LLM_MODEL", "").strip()
              or os.getenv("LOCAL_LLM_MODEL", "").strip())

AVAILABLE_MODELS = _configured or (
    [_env_model] if _env_model and PROVIDER != "local" else discover_models() or (
        [_env_model] if _env_model else []
    )
)

# Preferred default when the server lists several and .env names none.
_PREFERRED = ("qwen3.5", "qwen2.5", "llama", "mistral")


def _pick_default(models) -> str:
    for hint in _PREFERRED:
        for model in models:
            if hint in model.lower():
                return model
    return models[0] if models else ""


DEFAULT_MODEL = _env_model or _pick_default(AVAILABLE_MODELS)
if DEFAULT_MODEL and DEFAULT_MODEL not in AVAILABLE_MODELS:
    AVAILABLE_MODELS.insert(0, DEFAULT_MODEL)

# OCR needs a model that can see. The local endpoint serves text-only models, so
# vision stays off unless LLM_VISION_MODEL explicitly names one.
VISION_MODEL = os.getenv("LLM_VISION_MODEL", "").strip() or (
    "" if PROVIDER == "local" else DEFAULT_MODEL
)

# Models that have started returning rate-limit errors this session.
exhausted_models = set()


class LLMError(RuntimeError):
    pass


def is_rate_limit_error(error: Exception) -> bool:
    """Check if an exception is a rate limit error."""
    message = str(error).lower()
    return "rate limit" in message or "429" in message or "quota" in message


def get_available_models() -> dict:
    """Return {model_id: label}, with exhausted models marked."""
    return {
        model: (f"{model} [LIMIT REACHED]" if model in exhausted_models else model)
        for model in AVAILABLE_MODELS
    }


# ────────────────────────── adapters ──────────────────────────


_THINK_TAGS = re.compile(r"<(think|thinking|reasoning)>.*?</>", re.S | re.I)


def strip_reasoning(text: str) -> str:
    """Drop inline chain-of-thought some local models emit before the answer.

    Reasoning models served through Ollama usually return their thinking in a
    separate `reasoning` field, which we simply never read — but a few inline it
    in <think> tags instead, and that must not reach the user.
    """
    if "<" not in text:
        return text
    return _THINK_TAGS.sub("", text).lstrip()


class OpenAIChatAdapter:
    """Any endpoint speaking OpenAI's POST /chat/completions."""

    def url(self) -> str:
        return f"{BASE_URL}/chat/completions"

    def headers(self) -> dict:
        return {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }

    def body(self, messages, model, stream) -> dict:
        body = {"model": model, "messages": messages, "stream": stream}
        if REASONING_EFFORT:
            body["reasoning_effort"] = REASONING_EFFORT
        if MAX_TOKENS:
            body["max_tokens"] = MAX_TOKENS
        return body

    def text_from_response(self, data) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        content = (choices[0].get("message") or {}).get("content") or ""
        return strip_reasoning(content)

    def delta_from_event(self, event) -> str:
        choices = event.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("delta") or {}).get("content") or ""

    def image_message(self, prompt, data_url) -> list:
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]


class LocalLLMAdapter(OpenAIChatAdapter):
    """The organisation's own LLM, configured with just two variables.

    LOCAL_LLM_URL is used verbatim — it already carries the full path — and
    LOCAL_LLM_SECRET goes in as a bearer token. The wire format is OpenAI's, so
    everything else is inherited.
    """

    def url(self) -> str:
        return LOCAL_URL or f"{BASE_URL}/chat/completions"

    def headers(self) -> dict:
        return {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }


class CustomAdapter(OpenAIChatAdapter):
    """Your own LLM endpoint.

    Only the pieces that differ from the OpenAI shape need overriding — delete a
    method here and the OpenAI-compatible version above is used instead.

    Set in .env:  LLM_PROVIDER=custom, LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
    """

    # --- 1. where the request goes -------------------------------------
    def url(self) -> str:
        # e.g. return f"{BASE_URL}/v1/generate"
        return f"{BASE_URL}/chat/completions"

    # --- 2. how it authenticates ---------------------------------------
    def headers(self) -> dict:
        # e.g. return {"x-api-key": API_KEY, "Content-Type": "application/json"}
        return {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }

    # --- 3. the request body -------------------------------------------
    def body(self, messages, model, stream) -> dict:
        # `messages` is [{"role": "system"|"user", "content": "..."}].
        # Reshape here if your API wants prompt/system fields instead.
        return {"model": model, "messages": messages, "stream": stream}

    # --- 4. pulling text out of a non-streamed response ----------------
    def text_from_response(self, data) -> str:
        # e.g. return data["output"]["text"]
        return super().text_from_response(data)

    # --- 5. pulling text out of one streamed SSE event -----------------
    def delta_from_event(self, event) -> str:
        # e.g. return event.get("token", "")
        return super().delta_from_event(event)


ADAPTERS = {
    "local": LocalLLMAdapter,
    "openai": OpenAIChatAdapter,
    "custom": CustomAdapter,
}

adapter = ADAPTERS.get(PROVIDER, OpenAIChatAdapter)()


# ────────────────────────── transport ──────────────────────────


def _check_config():
    if not API_KEY:
        raise LLMError(
            "No API key configured. Set LOCAL_LLM_SECRET in 03_LexScan/.env "
            "(alongside LOCAL_LLM_URL)."
        )
    if PROVIDER == "local" and not LOCAL_URL:
        raise LLMError("LOCAL_LLM_URL is empty — set it in 03_LexScan/.env.")
    if not DEFAULT_MODEL:
        raise LLMError(
            "No model available. The endpoint listed none, so name one "
            "explicitly with LLM_MODEL in 03_LexScan/.env."
        )


def _raise_for_status(response, payload):
    if response.status_code < 400:
        return
    detail = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        detail = (error or {}).get("message") if isinstance(error, dict) else str(error or "")
    raise LLMError(f"{response.status_code} from the LLM: {detail or payload}")


# Cold-start symptoms seen when the server swaps the model held in VRAM.
_TRANSIENT = ("llama-server", "process has terminated", "model is loading",
              "connection reset", "timed out", "502", "503", "504")


def is_transient_error(error: Exception) -> bool:
    """A failure worth retrying rather than reporting."""
    message = str(error).lower()
    return any(hint in message for hint in _TRANSIENT)


async def _post_once(messages, model, timeout):
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            adapter.url(),
            headers=adapter.headers(),
            json=adapter.body(messages, model, stream=False),
        )
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        _raise_for_status(response, payload)
    return payload


async def complete(messages, model=None, timeout=TIMEOUT) -> str:
    """One-shot completion. Returns the full text."""
    _check_config()
    model = model or DEFAULT_MODEL

    for attempt in range(RETRIES + 1):
        try:
            payload = await _post_once(messages, model, timeout)
            break
        except (LLMError, httpx.TransportError) as e:
            if attempt >= RETRIES or not is_transient_error(e):
                raise
            await asyncio.sleep(2 * (attempt + 1))

    text = adapter.text_from_response(payload)
    if not text:
        raise LLMError(f"Model '{model}' returned an empty response.")
    return text


async def _stream_once(messages, model, timeout):
    """One streaming attempt. Raises before the first yield if the call fails."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            adapter.url(),
            headers=adapter.headers(),
            json=adapter.body(messages, model, stream=True),
        ) as response:
            if response.status_code >= 400:
                raw = await response.aread()
                try:
                    payload = json.loads(raw)
                except ValueError:
                    payload = raw.decode("utf-8", "replace")
                _raise_for_status(response, payload)

            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                delta = adapter.delta_from_event(event)
                if delta:
                    yield delta


async def stream(messages, model=None, timeout=TIMEOUT):
    """Streamed completion. Yields text deltas as they arrive."""
    _check_config()
    model = model or DEFAULT_MODEL
    produced = False

    for attempt in range(RETRIES + 1):
        try:
            async for delta in _stream_once(messages, model, timeout):
                produced = True
                yield delta
            break
        except (LLMError, httpx.TransportError) as e:
            # Once tokens are on screen a retry would duplicate them — only a
            # failure before the first token can be retried.
            if produced or attempt >= RETRIES or not is_transient_error(e):
                raise
            await asyncio.sleep(2 * (attempt + 1))

    if not produced:
        raise LLMError(
            f"Model '{model}' streamed no text. If it is a reasoning model it may "
            f"have spent the whole response thinking — set LLM_REASONING_EFFORT=none "
            f"in .env, or pick another model in settings."
        )


# ────────────────────────── agent helpers ──────────────────────────


async def run_agent(agent: Agent, user_input: str, model=None) -> str:
    """Run an agent to completion and return its text."""
    return await complete(agent.messages(user_input), model=model)


async def stream_agent(agent: Agent, user_input: str, model=None):
    """Run an agent and yield its output as it is generated."""
    async for delta in stream(agent.messages(user_input), model=model):
        yield delta


# ────────────────────────── OCR for scans ──────────────────────────

OCR_PROMPT = (
    "Transcribe ALL text visible in this document image, verbatim. Preserve clause "
    "numbers, section headings, party names, dates and amounts exactly as written. "
    "Do not summarise, do not comment, do not add markdown fences. Return only the "
    "transcribed text."
)

MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


async def ocr_image(image_path) -> str:
    """Transcribe a scanned page with a vision-capable model."""
    if not VISION_MODEL:
        raise LLMError(
            "No vision model is configured, so scans and photos can't be read. "
            "The local LLM serves text-only models — upload a text PDF, DOCX or "
            "TXT instead, or set LLM_VISION_MODEL in .env to a model that can see."
        )
    if not hasattr(adapter, "image_message"):
        raise LLMError(
            "This LLM adapter has no image support — upload a text PDF or DOCX instead."
        )

    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    ext = os.path.splitext(image_path)[1].lower()
    data_url = f"data:{MIME_TYPES.get(ext, 'image/png')};base64,{encoded}"

    messages = adapter.image_message(OCR_PROMPT, data_url)
    try:
        return await complete(messages, model=VISION_MODEL)
    except LLMError as e:
        raise LLMError(
            f"Could not read the scan with '{VISION_MODEL}'. Set LLM_VISION_MODEL in "
            f".env to a vision-capable model. ({e})"
        )


# ────────────────────────── long documents ──────────────────────────

CONDENSE_PROMPT = (
    "You are compressing part of a legal document so a lawyer can analyse it later.\n"
    "Rewrite the excerpt below as a dense digest that KEEPS, verbatim where possible:\n"
    "- every clause/section number and heading\n"
    "- party names, defined terms, dates, deadlines, notice periods\n"
    "- money amounts, percentages, interest, penalties, caps\n"
    "- governing law, jurisdiction, termination, liability, indemnity, confidentiality "
    "and auto-renewal language\n"
    "Drop only boilerplate recitals and repetition. Never invent anything. "
    "Return the digest only."
)


async def condense_chunk(chunk, model, index, total) -> str:
    """Compress one chunk of a long document down to its legally material content."""
    messages = [
        {"role": "system", "content": CONDENSE_PROMPT},
        {"role": "user", "content": f"[Excerpt {index}/{total}]\n\n{chunk}"},
    ]
    try:
        digest = await complete(messages, model=model)
    except LLMError:
        # A failed excerpt shouldn't sink the whole document — keep the head of it.
        digest = chunk[:2000]
    return f"[Excerpt {index}/{total}]\n{digest}"


async def condense_document(chunks, model=None, on_progress=None, concurrency=3) -> str:
    """Map-reduce a long document into a digest small enough to analyse in one pass."""
    total = len(chunks)
    semaphore = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    done = 0

    async def run(index, chunk):
        nonlocal done
        async with semaphore:
            digest = await condense_chunk(chunk, model, index, total)
        async with lock:
            done += 1
            if on_progress:
                await on_progress(done, total)
        return digest

    digests = await asyncio.gather(*(run(i, c) for i, c in enumerate(chunks, 1)))
    return "\n\n".join(digests)
