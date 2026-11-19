# 3.12: pysubs2 1.9.1 requires Python >= 3.12, and the native venvs (bench, tests) run 3.12.
FROM python:3.12-slim

# 1. Environment configuration
# PYTHONUNBUFFERED: Essential for real-time progress bars in Docker logs
# OMP_NUM_THREADS / ASR_THREADS: half of the 8-thread host (D-0001); transcriber.py passes ASR_THREADS to CTranslate2
# HF_HOME: faster-whisper downloads through Hugging Face, by default to /root/.cache/huggingface, which no volume
#   covers, so every container recreation re-downloaded the model. Point it at the whisper_model_cache volume.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=4 \
    ASR_THREADS=4 \
    HF_HOME=/root/.cache/whisper

# 2. Install system dependencies
# Added libgomp1 for faster-whisper/CTranslate2 compatibility
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    curl \
    build-essential \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 3. Install Python dependencies
# Using --upgrade pip to ensure the latest wheel support for faster-whisper
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 4. Execution
# Using --reload for development; remove --reload for production deployments
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]