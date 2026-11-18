"""Atomic batch append, packet identity and generation on the Redis side."""

from compass_agent.llm.continuation import canonical
from compass_agent.models.errors import ModelError

CHECK_INTEGRITY = """
local function valid()
    local count = redis.call('XLEN', KEYS[1])
    return count == tonumber(redis.call('HGET', KEYS[2], 'count') or '-1')
       and count == redis.call('HLEN', KEYS[3])
       and (count == 0 or (
           redis.call('XRANGE', KEYS[1], '-', '+', 'COUNT', 1)[1][1]
               == redis.call('HGET', KEYS[2], 'first')
           and redis.call('XREVRANGE', KEYS[1], '+', '-', 'COUNT', 1)[1][1]
               == redis.call('HGET', KEYS[2], 'last')
       ))
end
"""

VALIDATE = (
    CHECK_INTEGRITY
    + """
return valid() and 1 or 0
"""
)

APPEND = (
    CHECK_INTEGRITY
    + """
local generation, anchor = tonumber(ARGV[1]), ARGV[2]
local keysExist = redis.call('EXISTS', KEYS[2])
local published = redis.call('HGET', KEYS[2], 'generation')
if published and tonumber(published) > generation then return 'stale' end
if anchor ~= '0-0' and #redis.call('XRANGE', KEYS[1], anchor, anchor, 'COUNT', 1) == 0 then
    return 'lost'
end
if keysExist == 0 then
    if redis.call('EXISTS', KEYS[3]) > 0 then return 'lost' end
    local old = redis.call('XRANGE', KEYS[1], '-', '+', 'COUNT', 8193)
    if #old > 0 and ARGV[3] ~= '1' then return 'lost' end
    if #old > 8192 then return 'lost' end
    local byId, size = {}, 0
    for _, row in ipairs(old) do
        local data = row[2][2]
        local ok, decoded = pcall(cjson.decode, data)
        if not ok or not decoded.packet or not decoded.packet.packetId then return 'lost' end
        local id = decoded.packet.packetId
        if byId[id] then return 'lost' end
        byId[id], size = row[1], size + #data
    end
    for id, seq in pairs(byId) do redis.call('HSET', KEYS[3], id, seq) end
    redis.call('HSET', KEYS[2], 'count', #old, 'bytes', size)
    if #old > 0 then
        redis.call('HSET', KEYS[2], 'first', old[1][1], 'last', old[#old][1])
    end
elseif not valid() then
    return 'lost'
end
redis.call('HSET', KEYS[2], 'generation', ARGV[1])
local batch, bytes = {}, tonumber(redis.call('HGET', KEYS[2], 'bytes'))
for i = 4, #ARGV, 2 do
    local id, data = ARGV[i], ARGV[i + 1]
    local seq = redis.call('HGET', KEYS[3], id)
    if seq then
        local existing = redis.call('XRANGE', KEYS[1], seq, seq, 'COUNT', 1)
        if #existing == 0 then return 'lost' end
        if existing[1][2][2] ~= data then return 'conflict' end
    else
        bytes = bytes + #data
        table.insert(batch, {id, data})
    end
end
if bytes > 4194304 then return 'limit' end
for _, row in ipairs(batch) do
    local seq = redis.call('XADD', KEYS[1], '*', 'entry', row[2])
    redis.call('HSET', KEYS[3], row[1], seq)
    redis.call('HSETNX', KEYS[2], 'first', seq)
    redis.call('HSET', KEYS[2], 'last', seq)
end
redis.call('HSET', KEYS[2], 'bytes', bytes)
redis.call('HINCRBY', KEYS[2], 'count', #batch)
return 'ok'
"""
)

RETAIN = (
    CHECK_INTEGRITY
    + """
if valid() and redis.call('HGET', KEYS[2], 'generation') == ARGV[1]
   and redis.call('HGET', KEYS[2], 'last') == ARGV[2]
   and redis.call('TTL', KEYS[1]) == -1 then
    for _, key in ipairs(KEYS) do redis.call('EXPIRE', key, 3600) end
end
return 1
"""
)


def publication_keys(key):
    return key, key + ":publication", key + ":packets"


async def append_batches(redis, keys, run, boxes):
    args = [
        str(run.publish_generation),
        run.projected_seq,
        "1"
        if not run.publish_initialized
        and (run.publish_generation == 1 or run.projected_seq != "0-0")
        else "0",
    ]
    for box in boxes:
        packets = box.payload["packets"]
        for index, packet in enumerate(packets):
            entry = {
                "packet": packet,
                "outboxId": str(box.id),
                "batchIndex": index,
                "batchSize": len(packets),
            }
            args.extend((packet["packetId"], canonical(entry).decode()))
    result = await redis.eval(APPEND, len(keys), *keys, *args)
    if result != "ok":
        code, status = {
            "stale": ("publish_lease_lost", 409),
            "lost": ("stream_data_lost", 410),
            "conflict": ("packet_conflict", 409),
            "limit": ("run_content_limit", 409),
        }[result]
        raise ModelError(code, status)
