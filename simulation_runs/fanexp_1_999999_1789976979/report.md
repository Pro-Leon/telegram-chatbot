# Fan Experience — fan 999999 creator 1
started 2026-09-21T07:49:13.644325+00:00 ended 2026-09-21T07:49:39.117277+00:00
**stopped:** breakage at turn 1: fan experienced breakage **breakages:** 1

Fan sees exactly what Telegram would show — no internal stages.

| turn | fan sent (Telegram) | fan saw (Telegram) | wait | breakage |
|---|---|---|---|---|
| 1 | hi | (no reply — breakage) | 24489ms | breakage: fan waited 24489ms — Fan waited 15s for reply — lock/LLM hang (P1.6 lock ttl 300s, llama timeout 12s) (lock/LLM hang, fan sees typing... then nothing) |

## System trace (for debugging, fan doesn't see this)
- turn 1 stage=timeout details={"telegram_message_id": 60188, "traceback": "Traceback (most recent call last):\n  File \"C:\\Users\\User\\AppData\\Local\\Python\\pythoncore-3.14-64\\Lib\\asyncio\\tasks.py\", line 488, in wait_for\n    return await fut\n           ^^^^^^^^^\n  File \"E:\\chatbot\\workers\\llm_worker.py\", line 254