-- Prime Casino slot spin log.
--
-- The original endpoint wrote to this table via raw SQL guarded by a runtime
-- "does the table exist?" check, but no migration ever created it — so spin
-- logging silently no-op'd in every environment. This is the missing schema,
-- parked with the carved-out service. Apply it in whatever DB the slot
-- service is wired to when it's reconnected.
--
-- ``user_id`` references the host platform's trader user id. No FK is declared
-- here so the table stays independent of the host schema while disconnected.

CREATE TABLE IF NOT EXISTS miniapp_casino_spins (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             INTEGER        NOT NULL,
    bet_usdt            NUMERIC(15, 4) NOT NULL,
    payout_usdt         NUMERIC(15, 4) NOT NULL DEFAULT 0,
    multiplier          NUMERIC(10, 4) NOT NULL DEFAULT 0,
    is_win              BOOLEAN        NOT NULL DEFAULT FALSE,
    symbols             VARCHAR(128)   NOT NULL,
    balance_before_usdt NUMERIC(15, 4) NOT NULL,
    balance_after_usdt  NUMERIC(15, 4) NOT NULL,
    created_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_miniapp_casino_spins_user_id
    ON miniapp_casino_spins (user_id);

CREATE INDEX IF NOT EXISTS ix_miniapp_casino_spins_created_at
    ON miniapp_casino_spins (created_at);
