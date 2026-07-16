-- update_bandit.lua — atomic decay + α/β update.
--
-- KEYS[1] = stats key (e.g. "sel:traders:stats:42")
-- ARGV[1] = reward (float in [0, 1])
-- ARGV[2] = decay_factor (gamma, float in (0, 1])
-- ARGV[3] = decay_interval_sec (positive int as string)
-- ARGV[4] = now (unix seconds float)
--
-- Returns the updated JSON blob, or nil if the key doesn't exist.

local raw = redis.call('GET', KEYS[1])
if not raw then
    return nil
end

local s = cjson.decode(raw)
local reward = tonumber(ARGV[1])
local gamma = tonumber(ARGV[2])
local interval = tonumber(ARGV[3])
local now = tonumber(ARGV[4])

local elapsed = now - tonumber(s.last_updated)
local factor
if elapsed <= 0 then
    factor = 1.0
else
    factor = math.pow(gamma, elapsed / interval)
end

local alpha = tonumber(s.alpha)
local beta = tonumber(s.beta)

s.alpha = 1.0 + (alpha - 1.0) * factor + reward
s.beta  = 1.0 + (beta  - 1.0) * factor + (1.0 - reward)
s.last_updated = now

local encoded = cjson.encode(s)
redis.call('SET', KEYS[1], encoded)
return encoded
