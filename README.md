# OpenTelemetry tracing между двумя FastAPI-сервисами

Минимальный воспроизводимый стенд показывает ручные spans, W3C Trace Context и путь OTLP/HTTP через OpenTelemetry Collector в Grafana Tempo.

Один запрос создаёт цепочку:

```text
gateway.request
└── gateway.call-worker
    └── worker.reserve
```

## Запуск

Нужны Docker Engine и Compose V2.

```bash
docker compose config -q
docker compose up --build --detach
python inspect_stack.py
```

Успешная E2E-проверка печатает:

```text
stack: gateway=ready grafana_tempo=ready
normal_trace: spans=3 parent_links_valid=true
slow_trace: spans=3 worker_delay_observed=true
grafana_tempo_query: ok
```

Gateway доступен на `http://127.0.0.1:8000`, Grafana — на `http://127.0.0.1:3000`. Войдите в Grafana как `admin` с локальным учебным паролем `grafana-demo`, откройте **Explore**, выберите источник **Tempo** и найдите трассировку по `trace_id` из ответа:

```bash
curl -fsS 'http://127.0.0.1:8000/checkout?delay_ms=350'
```

## Отрицательная проверка propagation

Пересоздайте только gateway без передачи `traceparent`:

```bash
PROPAGATION_ENABLED=false docker compose up --detach --force-recreate gateway
python inspect_stack.py --expect-unlinked
```

В trace gateway останутся два span, а `worker.reserve` начнёт независимый trace. Верните штатное состояние и проверьте восстановление:

```bash
PROPAGATION_ENABLED=true docker compose up --detach --force-recreate gateway
python inspect_stack.py
```

## Локальные проверки Python

```bash
python -m pip install -r requirements-dev.txt
python -m ruff format --check .
python -m ruff check .
python check_traces.py
python -m pytest -q
```

`check_traces.py` использует реальные FastAPI-приложения и HTTPX ASGI transport, но заменяет внешний OTLP exporter на in-memory exporter. Поэтому команда детерминированно проверяет связи spans без Docker и сети. `inspect_stack.py` отдельно подтверждает настоящий маршрут Collector → Tempo → Grafana.

## Структура

- `tracing_demo/apps.py` — фабрики gateway и worker и их HTTP-маршруты;
- `tracing_demo/tracing.py` — изолированные `TracerProvider`, processors и exporters;
- `tracing_demo/config.py` — типизированная конфигурация окружения;
- `check_traces.py` — безопасный локальный контракт propagation и задержки;
- `inspect_stack.py` — Compose E2E через Gateway и Grafana API;
- `collector.yaml`, `tempo.yaml`, `grafana/provisioning/` — маршрут и хранение трассировок;
- `tests/` — pytest-регрессии.

Все контейнерные образы и Python-зависимости закреплены версиями; внешние образы дополнительно закреплены digest. Порты опубликованы только на `127.0.0.1`.

## Ограничения

Стенд создаёт три явных span и не подключает автоинструментализацию FastAPI или HTTPX. Пауза `delay_ms` ограничена диапазоном 0–800 мс и нужна только для демонстрации. Локальное хранилище Tempo и публично известный учебный пароль Grafana не являются production-конфигурацией.

Остановить стенд и удалить его volumes:

```bash
docker compose down --volumes
```

Исходники распространяются по Apache-2.0; условия находятся в `LICENSE`.
