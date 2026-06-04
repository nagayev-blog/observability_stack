#!/usr/bin/env python3
"""
Нагрузчик для metrics-demo на чистом Python (stdlib + requests).
Аналог k6-сценария, но на Python — бьёт по всем эндпоинтам в цикле
несколькими параллельными потоками, чтобы метрики ожили.

Установка единственной зависимости:
    pip install requests

Запуск:
    python load.py --target http://example.com:3377
    python load.py --target http://example.com:3377 --workers 30 --duration 300

Параметры:
    --target    базовый URL сервера (обязательно)
    --workers   число параллельных потоков (по умолчанию 20)
    --duration  сколько секунд лить нагрузку (по умолчанию 180; 0 = бесконечно)

Остановить досрочно: Ctrl+C.
"""

import argparse
import random
import threading
import time

import requests

# Счётчики для итоговой статистики (общие на все потоки).
_stats_lock = threading.Lock()
_stats = {"ok": 0, "err": 0, "5xx": 0}
_stop = threading.Event()


def _bump(key: str) -> None:
    with _stats_lock:
        _stats[key] += 1


def worker(target: str) -> None:
    """Один поток нагрузки: бьёт по эндпоинтам в разной пропорции."""
    session = requests.Session()
    while not _stop.is_set():
        try:
            # Основной поток — лёгкая работа со случайной задержкой.
            r = session.get(f"{target}/work", timeout=10)
            _bump("ok" if r.status_code == 200 else "err")

            # Иногда тяжёлый CPU-запрос — растит латентность и in-flight.
            if random.random() < 0.2:
                session.get(f"{target}/cpu", params={"n": 500000}, timeout=10)

            # Эмуляция очереди.
            if random.random() < 0.3:
                session.post(f"{target}/enqueue", timeout=10)
            if random.random() < 0.25:
                session.post(f"{target}/dequeue", timeout=10)

            # Запросы, часть которых отвечает 500 — наполняет метрику ошибок.
            if random.random() < 0.4:
                er = session.get(f"{target}/maybe-error", timeout=10)
                if er.status_code >= 500:
                    _bump("5xx")

            # Пауза между итерациями (имитация "думающего" клиента).
            time.sleep(random.random() * 0.5)
        except requests.RequestException:
            _bump("err")
            time.sleep(0.5)


def reporter() -> None:
    """Печатает статистику раз в 5 секунд."""
    last = dict(_stats)
    while not _stop.is_set():
        time.sleep(5)
        with _stats_lock:
            cur = dict(_stats)
        delta_ok = cur["ok"] - last["ok"]
        print(
            f"[{time.strftime('%H:%M:%S')}] "
            f"всего ok={cur['ok']} err={cur['err']} 5xx={cur['5xx']} "
            f"| ~{delta_ok / 5:.1f} req/s"
        )
        last = cur


def main() -> None:
    p = argparse.ArgumentParser(description="Python load generator for metrics-demo")
    p.add_argument("--target", required=True, help="базовый URL, напр. http://host:3377")
    p.add_argument("--workers", type=int, default=20, help="число потоков")
    p.add_argument("--duration", type=int, default=180, help="секунд (0 = бесконечно)")
    args = p.parse_args()

    target = args.target.rstrip("/")
    print(f"Цель: {target} | потоков: {args.workers} | длительность: {args.duration or '∞'}с")
    print("Ctrl+C для остановки.\n")

    threads = [threading.Thread(target=worker, args=(target,), daemon=True)
               for _ in range(args.workers)]
    for t in threads:
        t.start()

    rep = threading.Thread(target=reporter, daemon=True)
    rep.start()

    try:
        if args.duration > 0:
            time.sleep(args.duration)
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nОстановка...")
    finally:
        _stop.set()
        time.sleep(0.5)
        with _stats_lock:
            print(f"\nИтого: ok={_stats['ok']} err={_stats['err']} 5xx={_stats['5xx']}")


if __name__ == "__main__":
    main()
