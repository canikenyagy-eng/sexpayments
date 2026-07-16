# PrimePay Observability

Drop-in observability stack for the PrimePay backend. Brings up Prometheus,
Alertmanager, Grafana and exporters next to the existing PrimePay services
as a **docker-compose overlay** — same `.env`, same project name, same
GitLab CI job.

Covers **Week 1 (Foundation)** and **Week 2 (Application metrics)** of the
observability rollout plan. Tracing, logs and business metrics ship later.

---

## What's in the box

| Component | Purpose | Host port (defaults — override per env) |
|---|---|---|
| Prometheus | Metric store + alert engine | `127.0.0.1:${PORT_PROMETHEUS}` |
| Alertmanager | Alert routing + Telegram delivery | `127.0.0.1:${PORT_ALERTMANAGER}` |
| Grafana | Dashboards | `127.0.0.1:${PORT_GRAFANA}` |
| node-exporter | Host CPU/RAM/disk/network | (network-only) |
| cAdvisor | Per-container CPU/RAM/IO | (network-only) |
| postgres-exporter | Connections, locks, table size | (network-only) |
| redis-exporter | Memory, evictions, ops/sec, queue length | (network-only) |
| celery-exporter | Workers, tasks, queue depth | (network-only) |
| Backend `/metrics` | FastAPI RED metrics (Week 2) | exposed in-network |

All host ports bind to `127.0.0.1` — put Grafana behind your VPN / SSH
tunnel / private nginx if you want browser access.

---

## How it deploys

The observability stack is an **overlay** on top of the main `docker-compose.yml`.
Both files share the same compose project name (`primepay_${ENV_PREFIX}`),
which puts all services on the same default Docker network — Prometheus
can scrape `backend`, `db`, `redis` by their compose service name out of
the box, no external network plumbing required.

The existing GitLab CI job (`.gitlab-ci.yml`) already brings both files up
in a single command on every push:

```bash
docker-compose -p primepay_${ENV_PREFIX} \
  -f docker-compose.yml \
  -f observability/docker-compose.observability.yml \
  up -d --build
```

This runs automatically on:

- **dev** → push to `dev` branch (auto)
- **stage** → push to `stage` branch (manual)
- **prod** → push to `main` branch (manual)

There is no separate observability deploy. If the main stack ships, monitoring
ships with it.

---

## Per-environment configuration

PrimePay uses three GitLab file-type CI variables — `$ENV_DEV`, `$ENV_STAGE`,
`$ENV_PROD` — each holding the full `.env` for that environment. The runner
copies the relevant one to `.env`, exports it, and runs compose.

The repo includes [`.env.example`](../.env.example) as the canonical shape.
**For Week 1+2 to work**, each of the three GitLab env files needs the
observability section appended:

```dotenv
# Observability host ports (pick non-clashing values per environment)
PORT_PROMETHEUS=9091         # dev: 9091, stage: 9092, prod: 9090
PORT_ALERTMANAGER=9094       # dev: 9094, stage: 9095, prod: 9093
PORT_GRAFANA=3001            # dev: 3001, stage: 3002, prod: 3000

PROMETHEUS_RETENTION=15d
PROMETHEUS_EXTERNAL_URL=https://prometheus.dev.prime-pay.org
ALERTMANAGER_EXTERNAL_URL=https://alertmanager.dev.prime-pay.org

# Use SEPARATE Telegram chat IDs per environment so dev noise doesn't drown
# out prod pages. Channels are negative ints (-100…).
ALERT_TELEGRAM_BOT_TOKEN=...
ALERT_TELEGRAM_CHAT_ID=...

GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=...
GRAFANA_ANONYMOUS=false
GRAFANA_ROOT_URL=https://grafana.dev.prime-pay.org
```

Then update each of the three GitLab variables (`$ENV_DEV`, `$ENV_STAGE`,
`$ENV_PROD`) with these lines appended. The next push to the matching
branch will roll out monitoring automatically.

---

## After deploy — verify

The smoke-test script checks targets are UP and key metrics flow:

```bash
# On the deploy host, from the repo root:
./observability/scripts/smoke_test.sh

# Or against a specific Prometheus:
PROMETHEUS=http://localhost:9091 ALERTMANAGER=http://localhost:9094 \
  ./observability/scripts/smoke_test.sh
```

Grafana auto-loads three dashboards under the **PrimePay** folder:

- **PrimePay — System Health** (host, containers, Postgres, Redis)
- **PrimePay — API Health** (RED: rate / errors / duration by surface)
- **PrimePay — Worker Health** (Celery queue, tasks, callbacks)

---

## Alerting

Three severity tiers (see [`alertmanager/alertmanager.yml`](alertmanager/alertmanager.yml)):

- **p0** — pages immediately, repeats every 30 minutes until resolved
- **p1** — Telegram notification, 4 hour repeat
- **p2** — silent, surfaced via dashboards / weekly review

P0/P1 rules live in:

- [`prometheus/rules/p0_infra.yml`](prometheus/rules/p0_infra.yml) — host, containers, Postgres, Redis
- [`prometheus/rules/p1_app.yml`](prometheus/rules/p1_app.yml) — backend HTTP + Celery (active after Week 2)

To smoke-test the Telegram pipe **without waiting for a real outage**:

```bash
# Replace PORT with your env's PORT_ALERTMANAGER value.
curl -X POST localhost:9094/api/v2/alerts \
  -H 'Content-Type: application/json' \
  -d '[{
    "labels": {"alertname":"SmokeTest","severity":"p1","service":"observability","env":"dev"},
    "annotations":{"summary":"This is a smoke test","description":"If you see this, the Telegram receiver works."},
    "startsAt":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"
  }]'
```

The alert should hit your Telegram chat within ~30 seconds.

---

## Backend instrumentation

The backend exposes Prometheus metrics at `GET /metrics`
([`app/core/observability.py`](../backend-py/app/core/observability.py), wired
in [`app/main.py`](../backend-py/app/main.py)). Mounted unauthenticated — meant
to be scraped only from inside the Docker network. **Do not proxy `/metrics`
through the public nginx.**

Standard metrics from `prometheus-fastapi-instrumentator`:

- `http_requests_total{handler, method, status}` — rate + errors
- `http_request_duration_seconds_bucket{handler, method, le}` — latency
- `http_requests_inprogress{handler, method}` — in-flight

PrimePay-specific:

- `http_requests_by_surface_total{api_surface, method, status}` — request rate
  sliced by `merchant` / `bot` / `cascade` / `internal` / `health`. Lets
  dashboards isolate merchant-API regressions from admin/trader noise.
- `http_unmatched_route_total{method, status}` — requests that did not match
  any FastAPI route. Bot scanners hammer 404s; keep them out of the main
  error-rate chart.

Metric labels use the FastAPI route template, never raw paths with IDs —
cardinality stays bounded.

---

## Troubleshooting

**Prometheus shows targets DOWN**
- Confirm both compose files were brought up under the same project name. Run on the host: `docker network inspect primepay_${ENV_PREFIX}_default` — `prometheus`, `cadvisor`, `postgres-exporter`, `redis-exporter`, `backend` should all be listed.
- For `backend` job: confirm the backend image was rebuilt after `prometheus-fastapi-instrumentator` was added to `requirements.txt`. The CI does `--build`, so a redeploy is enough.
- For `postgres` job: check `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` in the env file match the main service.

**Grafana dashboards empty**
- Prometheus needs ~30s after start to scrape data.
- For Celery/backend dashboards: those metrics only appear once those services emit them — fire a real request to the backend and run a Celery task.

**Telegram alerts not arriving**
- Verify `ALERT_TELEGRAM_BOT_TOKEN` and `ALERT_TELEGRAM_CHAT_ID` are set in the env file.
- The bot must already be added to the target chat / channel.
- For channels, chat_id is a negative integer (`-100…`).
- Check Alertmanager logs: `docker logs ${ENV_PREFIX}_alertmanager`.

**Port conflict on deploy**
- All three environments can co-exist on the same VM, so `PORT_PROMETHEUS` / `PORT_ALERTMANAGER` / `PORT_GRAFANA` must differ between dev / stage / prod env files. The suggested mapping is in `.env.example`.

**Prometheus disk pressure**
- Default retention is 15 days. Adjust `PROMETHEUS_RETENTION` (e.g. `7d` tighter, `30d` longer).
- The `prometheus_data` named volume lives under `/var/lib/docker/volumes/primepay_${ENV_PREFIX}_prometheus_data/_data`.

---

## What's NOT here yet (later weeks)

- Distributed tracing (OpenTelemetry → Tempo) — Week 3
- Structured logs aggregation (Loki + Promtail) — Week 3
- Business/domain metrics (funnel, ledger invariants, cascade health) — Week 4
- Synthetic monitoring (Uptime Kuma, synthetic payin transactions) — Week 5
- SLO definitions + burn-rate alerts — Week 6
- Runbooks under `docs/runbooks/` — Week 6
