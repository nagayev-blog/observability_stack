"""
Демо FastAPI-сервер с метриками для VictoriaMetrics + Grafana.

Метрики двух категорий:
1. АВТО (prometheus-fastapi-instrumentator) — HTTP-метрики по всем эндпоинтам
   без ручного кода: http_requests_total, http_request_duration_seconds и т.д.
2. КАСТОМНЫЕ — показывают все рабочие типы метрик Prometheus вручную:
   Counter, Gauge, Histogram.

Эндпоинты для нагрузки:
  GET  /            — health, быстрый
  GET  /work        — имитация работы со случайной задержкой (для гистограммы латентности)
  GET  /cpu?n=...   — CPU-нагрузка (займёт worker, виден рост in-flight/latency)
  POST /enqueue     — кладёт "задачу" в очередь (растёт gauge очереди)
  POST /dequeue     — забирает задачу (падает gauge, растёт counter обработанных)
  GET  /maybe-error — ~20% ответов 500 (для метрики ошибок и кодов ответа)
"""

import asyncio
import random
import time

from fastapi import FastAPI, HTTPException, Response
from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(title="metrics-demo")

# ---------------------------------------------------------------------------
# КАСТОМНЫЕ МЕТРИКИ — по одному примеру каждого рабочего типа.
# ---------------------------------------------------------------------------

# COUNTER — только растёт. Считаем обработанные "задачи" по типу.
# Лейбл task_type — ограниченное множество (low cardinality), это правильно.
tasks_processed = Counter(
    "demo_tasks_processed_total",
    "Сколько задач обработано",
    ["task_type"],
)

# COUNTER — ошибки по типу. Смотреть через rate(), не абсолютное значение.
errors_total = Counter(
    "demo_errors_total",
    "Ошибки приложения",
    ["type"],
)

# GAUGE — текущий размер очереди. Растёт и падает.
queue_size = Gauge(
    "demo_queue_size",
    "Задач в очереди прямо сейчас",
)

# GAUGE — запросов в обработке прямо сейчас (in-flight).
in_flight = Gauge(
    "demo_in_flight_requests",
    "Запросов обрабатывается в данный момент",
)

# HISTOGRAM — распределение времени работы. Границы корзин (buckets) задаём
# в коде — их нельзя восстановить задним числом. Покрываем диапазон 5мс..2с.
work_duration = Histogram(
    "demo_work_duration_seconds",
    "Время выполнения работы в /work",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0],
)

# Простая "очередь" в памяти (для демонстрации gauge).
_queue: list[str] = []

# ---------------------------------------------------------------------------
# ЭНДПОИНТЫ
# ---------------------------------------------------------------------------


@app.get("/")
async def root():
    return {"status": "ok"}


@app.get("/work")
async def work():
    """Имитация работы со случайной задержкой — наполняет гистограмму латентности."""
    in_flight.inc()
    try:
        # засекаем время выполнения блока — попадёт в гистограмму
        with work_duration.time():
            # случайная задержка: чаще быстро, иногда медленно (длинный хвост)
            delay = random.choices(
                [0.01, 0.05, 0.2, 0.8],
                weights=[60, 25, 10, 5],
            )[0]
            await asyncio.sleep(delay)
        tasks_processed.labels(task_type="work").inc()
        return {"slept": delay}
    finally:
        in_flight.dec()


@app.get("/cpu")
async def cpu(n: int = 200000):
    """CPU-нагрузка: синхронный счёт, занимает worker. n управляет тяжестью."""
    in_flight.inc()
    try:
        with work_duration.time():
            total = 0
            for i in range(n):
                total += i * i
        tasks_processed.labels(task_type="cpu").inc()
        return {"n": n, "result_mod": total % 1000}
    finally:
        in_flight.dec()


@app.post("/enqueue")
async def enqueue():
    """Кладёт задачу в очередь — gauge очереди растёт."""
    _queue.append(f"task-{time.time()}")
    queue_size.set(len(_queue))
    return {"queued": len(_queue)}


@app.post("/dequeue")
async def dequeue():
    """Забирает задачу — gauge падает, counter обработанных растёт."""
    if not _queue:
        return {"queued": 0, "dequeued": None}
    item = _queue.pop(0)
    queue_size.set(len(_queue))
    tasks_processed.labels(task_type="dequeued").inc()
    return {"queued": len(_queue), "dequeued": item}


@app.get("/maybe-error")
async def maybe_error():
    """~20% запросов отвечают 500 — для метрики ошибок и распределения кодов."""
    if random.random() < 0.2:
        errors_total.labels(type="random").inc()
        raise HTTPException(status_code=500, detail="random failure")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# АВТО-МЕТРИКИ + /metrics
# ---------------------------------------------------------------------------
# Instrumentator вешает middleware на все эндпоинты и сам отдаёт /metrics.
# Туда попадают И авто-метрики, И наши кастомные (общий регистр prometheus_client).
Instrumentator(
    should_group_status_codes=True,   # коды группируются в 2xx, 5xx и т.д.
    excluded_handlers=["/metrics"],   # сам /metrics не инструментируем
).instrument(app).expose(app, endpoint="/metrics")
