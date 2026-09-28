import pathlib
p=pathlib.Path('E:/chatbot/core/one_call_pipeline.py')
t=p.read_text(encoding='utf-8')
c=t.count('qwen3:4b')
print(f'found {c} qwen3:4b')
t=t.replace('qwen3:4b','qwen2.5:3b')
p.write_text(t,encoding='utf-8')
print('replaced')
print(f"now qwen2.5:3b {t.count('qwen2.5:3b')}")

# Also fix core/llm_provider_ollama.py
p2=pathlib.Path('E:/chatbot/core/llm_provider_ollama.py')
t2=p2.read_text(encoding='utf-8')
print(f"llm_provider_ollama qwen3 {t2.count('qwen3:4b')} qwen2.5 {t2.count('qwen2.5:3b')}")
t2=t2.replace('qwen3:4b','qwen2.5:3b')
# Also update comments that say Qwen3
t2=t2.replace('Qwen3','Qwen2.5')
t2=t2.replace('qwen3','qwen2.5')
p2.write_text(t2,encoding='utf-8')
print("fixed llm_provider_ollama")

# Fix core/one_call.py comments
p3=pathlib.Path('E:/chatbot/core/one_call.py')
t3=p3.read_text(encoding='utf-8')
print(f"one_call qwen3 {t3.count('qwen3')} qwen2.5 {t3.count('qwen2.5')}")
t3=t3.replace('Qwen3','Qwen2.5')
t3=t3.replace('qwen3','qwen2.5')
# But need to keep Qwen2.5:3b correct, not double
t3=t3.replace('Qwen2.5:3b','qwen2.5:3b')
t3=t3.replace('Qwen2.5','qwen2.5')
p3.write_text(t3,encoding='utf-8')
print("fixed one_call")

# Fix core/config.py already done, but ensure
p4=pathlib.Path('E:/chatbot/core/config.py')
t4=p4.read_text(encoding='utf-8')
print(f"config qwen3 {t4.count('qwen3')} qwen2.5 {t4.count('qwen2.5')}")
# Already fixed

print("done")
