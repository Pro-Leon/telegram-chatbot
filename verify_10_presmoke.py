import asyncio, hashlib, time
from core.one_call_pipeline import one_call_generation

async def run():
    msgs=["hey","hi","hello","what's up","how was your day","thanks so much","haha","hey beautiful","I want to buy something","can you send me the video?"]
    ok=0
    for i,msg in enumerate(msgs):
        gid=hashlib.md5(f"presmoke-{i}:{msg}".encode()).hexdigest()[:12]
        start=time.monotonic()
        try:
            res=await one_call_generation(user_id=2000+i, creator_id=1, user_message=msg, persona="You are Sunny, warm", profile={}, user={"first_name":"test","funnel_stage":"new","message_count":5}, persona_name="Sunny", generation_id=gid)
            elapsed=int((time.monotonic()-start)*1000)
            prov=getattr(res,"provider_name","?")
            model=getattr(res,"model_name","?")
            valid=getattr(res,"is_valid",False)
            inp=getattr(res,"input_tokens",None)
            out=getattr(res,"output_tokens",None)
            lat=getattr(res,"latency_ms",elapsed)
            print(f"{i+1:2d} msg={msg!r:30s} provider={prov} model={model} valid={valid} in={inp} out={out} latency={lat} reply_len={len(res.reply) if res.reply else 0}")
            if valid and prov=="ollama" and model=="qwen2.5:3b":
                ok+=1
            else:
                print(f"   FAIL validation_error={getattr(res,'validation_error',None)}")
        except Exception as e:
            print(f"{i+1} EXC {e}")
            import traceback; traceback.print_exc()
    print(f"\nPRE-SMOKE {ok}/10")
    if ok==10:
        print("10/10 PASS")
    else:
        print("FAIL")
    return ok==10

asyncio.run(run())
