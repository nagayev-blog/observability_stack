FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8000

# 1 worker — чтобы метрики жили в одном процессе (без multiprocess-режима).
# Для реального highload позже: несколько workers + PROMETHEUS_MULTIPROC_DIR.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
