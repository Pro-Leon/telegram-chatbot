import importlib
tools = ['uncompyle6', 'decompyle3', 'xdis', 'bytecode']
for t in tools:
    try:
        m = importlib.import_module(t)
        ver = getattr(m, '__version__', 'unknown')
        print(f'{t}: AVAILABLE (version={ver})')
    except ImportError:
        print(f'{t}: NOT AVAILABLE')
