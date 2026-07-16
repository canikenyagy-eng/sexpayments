-- decay_and_get_batch.lua — read-with-decay for a batch of entities.
--
-- For each stats key in KEYS:
--   1. If absent, return cjson.null in its slot.
--   2. Otherwise decay α and β toward the (1, 1) prior by the elapsed time
--      since last_updated, write back, and return the updated JSON.
--
-- Doing this server-side keeps the engine's hot path to a single round-
-- trip and guarantees that two concurrent select()s on the same entity
-- see a consistent decayed state.
--
-- ARGV[1] = decay_factor (gamma in (0, 1])
-- ARGV[2] = decay_interval_sec
-- ARGV[3] = now (unix seconds)

local gamma = tonumber(ARGV[1])
local interval = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local result = {}
for i = 1, #KEYS do
    local raw = redis.call('GET', KEYS[i])
    if not raw then
        result[i] = false
    else
        local s = cjson.decode(raw)
        local last = tonumber(s.last_updated)
        local elapsed = now - last
        local factor = 1.0
        if elapsed > 0 then
            factor = math.pow(gamma, elapsed / interval)
        end
        s.alpha = 1.0 + (tonumber(s.alpha) - 1.0) * factor
        s.beta  = 1.0 + (tonumber(s.beta)  - 1.0) * factor
        s.last_updated = now
        local encoded = cjson.encode(s)
        redis.call('SET', KEYS[i], encoded)
        result[i] = encoded
    end
end
return result
