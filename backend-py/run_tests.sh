#!/bin/bash
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/primepay"
export REDIS_URL="redis://localhost:6379/0"
export SECRET_KEY="dummy"
export PYTHONPATH=.
poetry run pytest tests/unit/
