import asyncio
from memory.context import build_qwen3_context, current_message_in_history
from core.llm_provider import get_llm_provider

async def test():
    msgs=await build_qwen3_context(999999, 'hi', persona='You are Sunny Skye, friendly', creator_id=1)
    user_msg='hi'
    has=current_message_in_history(msgs, user_msg)
    print(f'has hi in history? {has}')
    final=msgs.copy()
    if not has:
        final.append({'role':'user','content':user_msg})
    print(f'final to Pola {len(final)} messages')
    for i,m in enumerate(final):
        print(f"[{i}] {m['role']}: {m['content'][:400].replace(chr(10),' | ')}")
    p=get_llm_provider()
    txt=await p.generate_with_history(system_instruction='', messages=final, max_output_tokens=40, temperature=0.7)
    print('--- Pola reply ---')
    print(txt[:500])

asyncio.run(test())
