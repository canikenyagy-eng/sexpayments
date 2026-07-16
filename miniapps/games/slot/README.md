# Prime Casino — Slot mini-app

A Telegram-WebApp slot machine that lets a trader bet/win against their USDT
WORK balance. **Carved out of the monorepo and currently disconnected** — it is
not mounted in the backend API and not routed in the frontend. Reconnect it
deliberately when the product is ready.

Originally added in commit `74fb2f6 "Add Prime Casino mini app"` as a fat
controller (`backend-py/app/api/v1/endpoints/miniapp.py`) plus a Vue view. It
was extracted here so it stops living inside the core payment backend.

## Layout

```
miniapps/games/slot/
  backend/
    game.py        # pure RNG/payout maths — standalone, fully unit-tested
    schemas.py     # request/response models (camelCase for the WebApp)
    config.py      # SlotSettings (env_prefix=MINIAPP_CASINO_)
    api.py         # FastAPI router (config + spin) — INTEGRATION-PARKED
    migrations/    # 001_create_casino_spins.sql (table the old code never created)
    tests/         # pure-logic tests for game.py
    .env.example
    requirements.txt
  frontend/
    MiniAppCasinoView.vue   # the WebApp UI
```

## Standalone vs integration-parked

- `game.py`, `schemas.py`, `config.py`, `tests/` are **fully standalone** —
  no host imports. `cd backend && pytest` runs green on the stdlib + pydantic.
- `api.py` is **integration-parked**: it imports the host backend's
  `FinanceService` / auth / DB session, so it only resolves once mounted inside
  `backend-py`. It is intentionally not importable on its own while disconnected.

## What was unwired from the monorepo

- `backend-py/app/api/v1/router.py` — removed the `miniapp` router include.
- `backend-py/app/api/v1/endpoints/miniapp.py` — deleted (moved here).
- `backend-py/app/core/config.py` + `.env.example` — removed `MINIAPP_CASINO_*`.
- `frontend-vue/src/router/index.ts` — removed the `/miniapp/casino` route.
- `frontend-vue/src/components/layout/TheSidebar.vue` — removed the nav links.
- `frontend-vue/src/components/layout/UserBalanceWidget.vue` — removed the
  `primepay:balance-updated` listener that the spin flow used to refresh balance.
- `frontend-vue/src/views/MiniAppCasinoView.vue` — moved to `frontend/`.

## To reconnect later

1. Apply `backend/migrations/001_create_casino_spins.sql` to the target DB.
2. Mount `backend/api.py:router` into `backend-py`'s v1 router (re-add the
   import + `include_router`), and restore the `MINIAPP_CASINO_*` settings (or
   load `SlotSettings`).
3. Re-add the frontend route + nav + balance-refresh listener and point the
   view's API calls at the mounted endpoints.
