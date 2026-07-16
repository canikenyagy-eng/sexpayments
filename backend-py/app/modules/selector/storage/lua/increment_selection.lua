-- increment_selection.lua — atomic ++total_selections on the stats blob.
--
-- KEYS[1] = stats key
-- Returns the new total_selections, or nil if the key doesn't exist.

local raw = redis.call('GET', KEYS[1])
if not raw then
    return nil
end

local s = cjson.decode(raw)
s.total_selections = (tonumber(s.total_selections) or 0) + 1
redis.call('SET', KEYS[1], cjson.encode(s))
return s.total_selections
