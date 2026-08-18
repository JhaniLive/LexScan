# LexScan — one container, everything inside it.
#
# The OCR and speech models are baked into the image at build time rather than
# downloaded on first use. That costs ~400MB of image size and buys a cold start
# that answers immediately instead of stalling for a minute while it fetches
# models — which matters on hosts that stop the container when idle.

FROM python:3.13-slim

# opencv (via rapidocr) wants these; the headless build below avoids the rest.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/home/app/.cache/huggingface

RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY requirements.txt .

# rapidocr depends on opencv-python by name, so pip installs the full build no
# matter what is installed first — and that one needs libGL, which a slim server
# image does not have. Install normally, then swap in the headless build, which
# is the same cv2 without the GUI bindings.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y opencv-python \
    && pip install --no-cache-dir opencv-python-headless

COPY --chown=app:app . .

USER app

# Warm the models into the image. Whisper sizes follow STT_MODEL/STT_LIVE_MODEL,
# so a build with STT_MODEL=small bakes small.
ARG STT_MODEL=base
ARG STT_LIVE_MODEL=tiny
ENV STT_MODEL=${STT_MODEL} \
    STT_LIVE_MODEL=${STT_LIVE_MODEL}

RUN python -c "\
from faster_whisper import WhisperModel; \
import os; \
WhisperModel(os.environ['STT_LIVE_MODEL'], device='cpu', compute_type='int8'); \
WhisperModel(os.environ['STT_MODEL'], device='cpu', compute_type='int8'); \
from rapidocr_onnxruntime import RapidOCR; RapidOCR(); \
print('models baked in')"

# Hosts hand the port over in $PORT; 8000 is the local default.
ENV PORT=8000
EXPOSE 8000

# --host 0.0.0.0 is required in a container, and -h keeps it from trying to
# open a browser that does not exist.
CMD ["sh", "-c", "chainlit run chainlit_app.py --host 0.0.0.0 --port ${PORT} -h"]
