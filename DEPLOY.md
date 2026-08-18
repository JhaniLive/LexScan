# LexScan — Deployment Guide

> **Free-tier reality check.** Platform free tiers move constantly and mostly in
> one direction. Hugging Face made Docker Spaces PRO-only; Render's free tier is
> 512 MB, which this app exceeds mid-scan. Anything here quoted as free is worth
> re-checking before you commit to it. Option D depends on no platform's pricing
> at all.

## What this app actually needs

Measured on a full load, all models resident:

| Requirement | Figure | Why it constrains the choice |
| --- | --- | --- |
| Memory | **388 MB** idle, ~700 MB under a scan | 512 MB tiers will OOM once a PDF is being rendered |
| Disk | ~400 MB of models | Ephemeral disks re-download them on every cold start |
| WebSockets | **Required** | Chainlit streams over them — static hosts cannot run this |
| Request length | Up to ~10 min per document | Proxies that cap at 30-60s will cut scans off |
| Outbound HTTPS | To your LLM, and DuckDuckGo | The host must be able to reach `LOCAL_LLM_URL` |

**Netlify cannot host this.** AtlasIQ's frontend went there because it is static
React; Chainlit is a live Python server with WebSockets. Only the Render half of
that setup has an equivalent here.

---

## Before you deploy: the privacy trade

Right now uploads, scans and voice never leave the machine LexScan runs on —
OCR and speech-to-text are local, and only the extracted *text* goes to your
LLM. Move the app to a cloud host and that changes: documents and audio now land
on someone else's server first.

For a demo or portfolio piece that is fine. For real matters — someone's police
complaint, a family dispute — think about whether that is acceptable before
picking a host. **Option D keeps the guarantee.**

---

## Option A — Hugging Face Spaces (PRO subscription required)

**Not free any more.** Hugging Face now restricts Gradio and Docker Spaces to
paid PRO accounts; only Static Spaces remain free, and a Static Space serves
files — it cannot run Python, so it cannot run this.

With PRO (~$9/month) the steps below work as written, and the Space config is
already in this README's frontmatter (`sdk: docker`, `app_port: 8000`).
Otherwise skip to **Option D**, which costs nothing.

**1. Log in to Hugging Face from the terminal**

Create a token at https://huggingface.co/settings/tokens with **write** access,
then:

```bash
venv\Scripts\hf auth login
```

**2. Create the Space**

At https://huggingface.co/new-space:

| Setting | Value |
| --- | --- |
| Owner | your account |
| Space name | `lexscan` |
| License | `mit` |
| SDK | **Docker** → *Blank* |
| Hardware | **CPU basic** (free) |
| Visibility | **Private** — see the warning below |

**3. Push the code**

```bash
git remote add space https://huggingface.co/spaces/<your-username>/lexscan
git push space main
```

The build takes several minutes the first time — it installs the dependencies
and bakes in the OCR and Whisper models. Watch the **Logs** tab.

**4. Add the secrets**

Space **Settings** → **Variables and secrets** → **New secret**:

| Name | Value |
| --- | --- |
| `LOCAL_LLM_URL` | your full endpoint, path included |
| `LOCAL_LLM_SECRET` | the bearer token |

Add them as **secrets**, not variables — variables are visible to anyone who can
see the Space. The Space restarts automatically once they are saved.

**Make it private, or add auth.** A public Space with no authentication means
anyone can spend your LLM quota, and strangers' documents pass through your
endpoint. Private is one click; authentication is the better answer if you want
others to use it.

Good for: showing the project, internal testing.
Watch for: free Spaces sleep after ~48h idle, and your LLM endpoint must accept
connections from Hugging Face's servers — not just from your network. That is
the most likely reason a working local build fails once deployed.

---

## Option B — Render.com (the platform you already know)

Same dashboard as AtlasIQ's backend, but this needs a **paid instance** —
the free tier's 512 MB will be killed mid-scan.

1. **New** → **Web Service** → connect `JhaniLive/LexScan`
2. Configure:

   | Setting | Value |
   | --- | --- |
   | Runtime | **Docker** |
   | Instance type | **Standard** (2 GB) — not Free, not Starter |
   | Health check path | `/` |

3. Environment variables: `LOCAL_LLM_URL`, `LOCAL_LLM_SECRET`
4. Add a **persistent disk** mounted at `/home/app/.cache/huggingface`, 1 GB,
   so models survive restarts.

Good for: continuity with what you already run.
Watch for: cost, and that Render's request timeout suits a 10-minute scan —
WebSocket traffic keeps the connection alive, but test a full document early.

---

## Option C — A small VPS (most control, cheapest at scale)

Hetzner, DigitalOcean, Linode — a 2-4 GB box is a few dollars a month and runs
this comfortably with room for several users.

```bash
# on the server
git clone https://github.com/JhaniLive/LexScan.git && cd LexScan
cp .env.example .env && nano .env          # fill in LOCAL_LLM_URL + secret
docker build -t lexscan .
docker run -d --name lexscan \
  --restart unless-stopped \
  -p 8000:8000 \
  --env-file .env \
  -v lexscan-models:/home/app/.cache/huggingface \
  -v lexscan-history:/app/.chat_history \
  lexscan
```

Put Caddy or nginx in front for HTTPS — **required**, because browsers only
grant microphone access on `localhost` or a secure origin. Without TLS the voice
feature will not work for anyone but you.

```
# Caddyfile — Caddy gets certificates automatically
lexscan.yourdomain.com {
    reverse_proxy localhost:8000
}
```

Good for: real use, multiple users, full control of the data.

---

## Option D — Keep it local, expose a tunnel (free, keeps the privacy promise)

The app stays on your machine; a tunnel gives it a public HTTPS address. Nothing
is stored on anyone else's server, and the mic works because the tunnel provides
TLS.

```bash
# one-off, no account needed
cloudflared tunnel --url http://localhost:8000
```

That prints a `https://<random>.trycloudflare.com` URL. For a stable address,
create a named tunnel on a Cloudflare account (free).

Good for: demos, letting a colleague try it, anything sensitive.
Watch for: it only works while your machine is on, and it is your CPU serving
every request.

---

## Whichever you choose

**Set these as host environment variables — never in the image:**

| Key | Value |
| --- | --- |
| `LOCAL_LLM_URL` | your full endpoint, path included |
| `LOCAL_LLM_SECRET` | the bearer token |
| `STT_MODEL` | `base`, or `small` for better Indian-accent accuracy |
| `PORT` | whatever the host requires |

**Add authentication before anyone else can reach it.** Chainlit ships password
and OAuth providers; without one, a public URL means a public LLM endpoint and
anyone's documents on your server. See
[Chainlit auth docs](https://docs.chainlit.io/authentication/overview).

**Check the LLM is reachable from the host** — `llm.cologix.ai` must accept
connections from the deployed machine, not just your office network. Test with
`python test_llm.py` inside the container before blaming the app.

**Build and test the image locally first:**

```bash
docker build -t lexscan .
docker run --rm -p 8000:8000 --env-file .env lexscan
```
