-- Phase 0.3 baseline queries (read-only: SELECT + Redis read-only commands).
-- Canonical ref: docs/LUNA_RELIABILITY_PROGRAM.md Phase 0.3.
-- No schema change. Run against a DB snapshot / replica; paste results into docs/LUNA_BASELINES.md.

-- Q1. Violation rate: prompt_echo + safety/quality outcomes (last 7 days).
SELECT
  COUNT(*) AS turns,
  COUNT(*) FILTER (
    WHERE scoring_flags::jsonb ? 'prompt_echo'
       OR conversational_validation_flags::jsonb ? 'prompt_echo'
       OR roleplay_validation_flags::jsonb ? 'prompt_echo'
  ) AS prompt_echo_turns,
  COUNT(*) FILTER (WHERE validation_outcome LIKE '%safety%') AS safety_failures,
  COUNT(*) FILTER (WHERE validation_outcome LIKE '%quality%') AS quality_failures,
  COUNT(*) FILTER (WHERE routing_decision = 'auto_approved') AS auto_approved,
  COUNT(*) FILTER (WHERE routing_decision = 'operator_queued') AS operator_queued
FROM generation_telemetry
WHERE created_at > NOW() - interval '7 days';

-- Q1b. Routing-decision split (coarse; veto reason lives in logs until Phase 4.1).
SELECT routing_decision, COUNT(*) AS turns
FROM generation_telemetry
WHERE created_at > NOW() - interval '7 days'
GROUP BY routing_decision
ORDER BY turns DESC;

-- Q2. Repeat: back-to-back normalized-equal outbound pairs per (creator,user), 7 days.
-- messages columns per db/schema.sql (direction/content/created_at).
WITH norm AS (
  SELECT creator_id, user_id, created_at,
         lower(regexp_replace(content, '\s+', ' ', 'g')) AS ntext,
         lag(lower(regexp_replace(content, '\s+', ' ', 'g')))
           OVER (PARTITION BY creator_id, user_id ORDER BY created_at) AS prev_ntext,
         lag(direction) OVER (PARTITION BY creator_id, user_id ORDER BY created_at) AS prev_dir
  FROM messages
  WHERE direction = 'outbound'
    AND created_at > NOW() - interval '7 days'
)
SELECT COUNT(*) AS outbound_turns,
       COUNT(*) FILTER (WHERE ntext = prev_ntext) AS repeat_pairs
FROM norm;

-- Q3. Dedup-hit: persisted per-turn counters, 7 days.
SELECT
  SUM(duplicate_send_suppressed_count) AS dedup_hits,
  SUM(already_executed_count) AS already_executed,
  SUM(inbound_redelivery_count) AS inbound_redeliveries,
  COUNT(*) AS turns
FROM generation_telemetry
WHERE created_at > NOW() - interval '7 days';

-- Q3b (logs, not SQL): grep worker logs for the fail-open dedup markers.
--   grep -c "inbound dedup suppressed" worker.log        -- db/redis.py:934
--   grep -c "Routing veto" worker.log                   -- workers/llm_worker.py:5275 (0.3 line adds flags/score)
--   grep -c "duplicate:" worker.log                     -- duplicate inbound returns duplicate:<tg_id>

-- Q4. Streams (Redis read-only; run via redis-cli; stream names per db/redis.py).
--   XPENDING send_messages llm_workers
--   XINFO STREAM send_messages
--   XPENDING inbound_messages llm_workers
--   XINFO STREAM inbound_messages
-- Record: pending count per group + smallest idle time + stream length.
