"""
Forensic Validation Script — Phase 79A (v2)
Properly extracts nested code objects from .pyc and compiled source.
"""
import ast
import dis
import json
import marshal
import struct
import types
from pathlib import Path

PYC_PATH = Path("recovery_llm_worker/llm_worker.cpython-314.pyc")
RECONSTRUCTED_PATH = Path("recovery_llm_worker/reconstructed/llm_worker_recovered.py")
OUTPUT_DIR = Path("recovery_llm_worker")

def extract_all_code_objects(pyc_path: Path) -> dict[str, types.CodeType]:
    """Extract ALL code objects from .pyc, nested or not."""
    with open(pyc_path, "rb") as f:
        f.read(16)  # magic + flags + timestamp + size
        code = marshal.load(f)
    
    code_objects = {}
    def collect(co, depth=0, parent=""):
        key = co.co_name
        if parent:
            key = parent + "." + co.co_name
        # Handle duplicate names
        if key in code_objects:
            key += f"#{depth}"
        code_objects[key] = co
        for const in co.co_consts:
            if isinstance(const, types.CodeType):
                collect(const, depth+1, key if co.co_name != "<module>" else "")
    collect(code)
    return code_objects

def extract_source_code_objects(source_path: Path) -> dict[str, types.CodeType]:
    """Compile source and extract ALL code objects."""
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    code = compile(tree, str(source_path), "exec")
    
    code_objects = {}
    def collect(co, depth=0, parent=""):
        key = co.co_name
        if parent:
            key = parent + "." + co.co_name
        if key in code_objects:
            key += f"#{depth}"
        code_objects[key] = co
        for const in co.co_consts:
            if isinstance(const, types.CodeType):
                collect(const, depth+1, key if co.co_name != "<module>" else "")
    collect(code)
    return code_objects

def classify_co(co: types.CodeType) -> dict:
    """Extract comprehensive metadata from a code object."""
    instructions = list(dis.get_instructions(co))
    return {
        "name": co.co_name,
        "argcount": co.co_argcount,
        "posonlyargcount": co.co_posonlyargcount,
        "kwonlyargcount": co.co_kwonlyargcount,
        "nlocals": co.co_nlocals,
        "firstlineno": co.co_firstlineno,
        "names": list(co.co_names),
        "varnames": list(co.co_varnames),
        "consts_raw": [repr(c)[:100] for c in co.co_consts if not isinstance(c, types.CodeType)],
        "filename": co.co_filename,
        "is_async": bool(co.co_flags & 0x100),
        "is_coroutine": bool(co.co_flags & 0x100),
        "has_exception_handler": any(
            i.opname in ("SETUP_FINALLY", "BEGIN_FINALLY", "POP_EXCEPT")
            for i in instructions
        ),
        "has_await": any(i.opname == "GET_AWAITABLE" for i in instructions),
        "has_return": any(i.opname == "RETURN_VALUE" for i in instructions),
        "instruction_count": len(instructions),
        "nested_code_objects": [c.co_name for c in co.co_consts if isinstance(c, types.CodeType)],
        "opnames": list(set(i.opname for i in instructions)),
    }

def compare_code_objects(pyc_meta: dict, src_meta: dict) -> tuple[str, list[str]]:
    """Compare two code objects. Returns (classification, reasons)."""
    reasons = []
    score = 0
    max_score = 10
    
    # Name match
    if pyc_meta["name"] == src_meta["name"]:
        score += 1
    else:
        reasons.append(f"name mismatch: {pyc_meta['name']} vs {src_meta['name']}")
    
    # Arg count
    if pyc_meta["argcount"] == src_meta["argcount"]:
        score += 1
    else:
        reasons.append(f"argcount: {pyc_meta['argcount']} vs {src_meta['argcount']}")
    
    # Locals
    if pyc_meta["nlocals"] == src_meta["nlocals"]:
        score += 1
    elif abs(pyc_meta["nlocals"] - src_meta["nlocals"]) <= 3:
        score += 0.5
        reasons.append(f"nlocals close: {pyc_meta['nlocals']} vs {src_meta['nlocals']}")
    else:
        reasons.append(f"nlocals: {pyc_meta['nlocals']} vs {src_meta['nlocals']}")
    
    # Async
    if pyc_meta["is_async"] == src_meta["is_async"]:
        score += 1
    else:
        reasons.append(f"async mismatch: {pyc_meta['is_async']} vs {src_meta['is_async']}")
    
    # Names overlap
    pyc_names = set(pyc_meta["names"])
    src_names = set(src_meta["names"])
    if pyc_names and src_names:
        overlap = len(pyc_names & src_names) / len(pyc_names | src_names)
        score += overlap
        if overlap < 0.5:
            reasons.append(f"names overlap low: {overlap:.2f}")
    
    # Constants overlap
    pyc_consts = set(c for c in pyc_meta["consts_raw"] if isinstance(c, str) and len(c) < 100)
    src_consts = set(c for c in src_meta["consts_raw"] if isinstance(c, str) and len(c) < 100)
    if pyc_consts and src_consts:
        overlap = len(pyc_consts & src_consts) / len(pyc_consts | src_consts)
        score += overlap
        if overlap < 0.3:
            reasons.append(f"consts overlap low: {overlap:.2f}")
    
    # Nested count
    if len(pyc_meta["nested_code_objects"]) == len(src_meta["nested_code_objects"]):
        score += 1
    else:
        reasons.append(f"nested count: {len(pyc_meta['nested_code_objects'])} vs {len(src_meta['nested_code_objects'])}")
    
    # Instruction count ratio
    if pyc_meta["instruction_count"] > 0:
        ratio = src_meta["instruction_count"] / pyc_meta["instruction_count"]
        if 0.8 <= ratio <= 1.2:
            score += 1
        elif 0.5 <= ratio <= 1.5:
            score += 0.5
        else:
            reasons.append(f"instr ratio: {ratio:.2f} ({src_meta['instruction_count']}/{pyc_meta['instruction_count']})")
    
    # Exception handler
    if pyc_meta["has_exception_handler"] == src_meta["has_exception_handler"]:
        score += 0.5
    else:
        reasons.append(f"exception handler: {pyc_meta['has_exception_handler']} vs {src_meta['has_exception_handler']}")
    
    # Return
    if pyc_meta["has_return"] == src_meta["has_return"]:
        score += 0.5
    
    # Classification
    if score >= 8:
        return "MATCH", reasons
    elif score >= 5:
        return "PARTIAL", reasons
    else:
        return "WEAK", reasons

def main():
    print("=" * 70)
    print("PHASE 79A — FORENSIC CODE OBJECT COMPARISON (v2)")
    print("=" * 70)
    
    # Extract
    print("\n[1] Extracting .pyc code objects...")
    pyc_cos = extract_all_code_objects(PYC_PATH)
    pyc_top = {k: v for k, v in pyc_cos.items() if "." not in k and not k.startswith("__")}
    print(f"    Total: {len(pyc_cos)}, Top-level: {len(pyc_top)}")
    for name in sorted(pyc_top.keys()):
        co = pyc_top[name]
        print(f"      {name}: line {co.co_firstlineno}, args={co.co_argcount}, locals={co.co_nlocals}, async={bool(co.co_flags & 0x100)}")
    
    print("\n[2] Compiling reconstructed source...")
    src_cos = extract_source_code_objects(RECONSTRUCTED_PATH)
    src_top = {k: v for k, v in src_cos.items() if "." not in k and not k.startswith("__")}
    print(f"    Total: {len(src_cos)}, Top-level: {len(src_top)}")
    for name in sorted(src_top.keys()):
        co = src_top[name]
        print(f"      {name}: line {co.co_firstlineno}, args={co.co_argcount}, locals={co.co_nlocals}, async={bool(co.co_flags & 0x100)}")
    
    # Compare
    print("\n[3] Comparison:")
    comparison_lines = []
    comparison_lines.append("=" * 70)
    comparison_lines.append("CODE OBJECT COMPARISON — .pyc vs RECONSTRUCTED SOURCE (v2)")
    comparison_lines.append("=" * 70)
    comparison_lines.append(f".pyc: {PYC_PATH}")
    comparison_lines.append(f"Source: {RECONSTRUCTED_PATH}")
    comparison_lines.append(f".pyc top-level: {sorted(pyc_top.keys())}")
    comparison_lines.append(f"Source top-level: {sorted(src_top.keys())}")
    comparison_lines.append("")
    
    results = {}
    all_func_names = sorted(set(pyc_top.keys()) | set(src_top.keys()))
    
    for name in all_func_names:
        pyc_meta = classify_co(pyc_top[name]) if name in pyc_top else None
        src_meta = classify_co(src_top[name]) if name in src_top else None
        
        if pyc_meta and src_meta:
            classification, reasons = compare_code_objects(pyc_meta, src_meta)
        elif pyc_meta:
            classification, reasons = "MISSING", ["not in reconstructed source"]
        else:
            classification, reasons = "EXTRA", ["not in .pyc"]
        
        results[name] = (classification, reasons)
        icon = {"MATCH": "✓", "PARTIAL": "~", "WEAK": "△", "MISSING": "✗", "EXTRA": "+"}[classification]
        
        comparison_lines.append(f"{'='*70}")
        comparison_lines.append(f"  {icon} {name} — {classification}")
        comparison_lines.append(f"{'='*70}")
        
        if pyc_meta:
            comparison_lines.append(f"  .pyc: args={pyc_meta['argcount']} posonly={pyc_meta['posonlyargcount']} kwonly={pyc_meta['kwonlyargcount']} locals={pyc_meta['nlocals']} line={pyc_meta['firstlineno']} async={pyc_meta['is_async']} instrs={pyc_meta['instruction_count']}")
            comparison_lines.append(f"    names({len(pyc_meta['names'])}): {pyc_meta['names'][:15]}{'...' if len(pyc_meta['names'])>15 else ''}")
            str_consts = [c for c in pyc_meta['consts_raw'] if not c.startswith('<')][:8]
            comparison_lines.append(f"    consts: {str_consts}")
            comparison_lines.append(f"    nested: {pyc_meta['nested_code_objects']}")
            comparison_lines.append(f"    ops: {pyc_meta['opnames'][:15]}{'...' if len(pyc_meta['opnames'])>15 else ''}")
        else:
            comparison_lines.append(f"  .pyc: NOT FOUND")
        
        if src_meta:
            comparison_lines.append(f"  src:  args={src_meta['argcount']} posonly={src_meta['posonlyargcount']} kwonly={src_meta['kwonlyargcount']} locals={src_meta['nlocals']} line={src_meta['firstlineno']} async={src_meta['is_async']} instrs={src_meta['instruction_count']}")
            comparison_lines.append(f"    names({len(src_meta['names'])}): {src_meta['names'][:15]}{'...' if len(src_meta['names'])>15 else ''}")
            str_consts = [c for c in src_meta['consts_raw'] if not c.startswith('<')][:8]
            comparison_lines.append(f"    consts: {str_consts}")
            comparison_lines.append(f"    nested: {src_meta['nested_code_objects']}")
            comparison_lines.append(f"    ops: {src_meta['opnames'][:15]}{'...' if len(src_meta['opnames'])>15 else ''}")
        else:
            comparison_lines.append(f"  src:  NOT FOUND")
        
        if reasons:
            comparison_lines.append(f"  reasons: {'; '.join(reasons)}")
        comparison_lines.append("")
    
    # Summary
    comparison_lines.append("")
    comparison_lines.append("=" * 70)
    comparison_lines.append("SUMMARY")
    comparison_lines.append("=" * 70)
    for name, (cls, reasons) in sorted(results.items()):
        icon = {"MATCH": "✓", "PARTIAL": "~", "WEAK": "△", "MISSING": "✗", "EXTRA": "+"}[cls]
        comparison_lines.append(f"  {icon} {name}: {cls}")
    
    counts = {}
    for cls, _ in results.values():
        counts[cls] = counts.get(cls, 0) + 1
    comparison_lines.append(f"\n  Totals: {counts}")
    
    comparison_text = "\n".join(comparison_lines)
    (OUTPUT_DIR / "code_object_comparison.txt").write_text(comparison_text, encoding="utf-8")
    print(f"\n  Written: recovery_llm_worker/code_object_comparison.txt")
    
    # Now compare ALL nested code objects
    print("\n[4] Nested code object comparison:")
    nested_comparison = []
    nested_comparison.append("=" * 70)
    nested_comparison.append("NESTED CODE OBJECT COMPARISON")
    nested_comparison.append("=" * 70)
    
    for func_name in all_func_names:
        if func_name in pyc_top and func_name in src_top:
            nested_comparison.append(f"\n--- {func_name} nested code objects ---")
            # Get nested from .pyc
            pyc_nested = {k: v for k, v in pyc_cos.items() if k.startswith(func_name + ".") and k != func_name}
            src_nested = {k: v for k, v in src_cos.items() if k.startswith(func_name + ".") and k != func_name}
            
            pyc_names = set(k.split(".", 1)[1] if "." in k else k for k in pyc_nested.keys())
            src_names = set(k.split(".", 1)[1] if "." in k else k for k in src_nested.keys())
            
            nested_comparison.append(f"  .pyc nested: {sorted(pyc_names)}")
            nested_comparison.append(f"  src nested:  {sorted(src_names)}")
            
            for nname in sorted(pyc_names | src_names):
                pyc_key = func_name + "." + nname
                src_key = func_name + "." + nname
                if pyc_key in pyc_nested and src_key in src_nested:
                    pm = classify_co(pyc_nested[pyc_key])
                    sm = classify_co(src_nested[src_key])
                    cls, reasons = compare_code_objects(pm, sm)
                    icon = {"MATCH": "✓", "PARTIAL": "~", "WEAK": "△"}.get(cls, "?")
                    nested_comparison.append(f"    {icon} {nname}: {cls} (args={pm['argcount']}/{sm['argcount']}, locals={pm['nlocals']}/{sm['nlocals']})")
                elif pyc_key in pyc_nested:
                    nested_comparison.append(f"    ✗ {nname}: MISSING in source")
                else:
                    nested_comparison.append(f"    + {nname}: EXTRA in source")
    
    nested_text = "\n".join(nested_comparison)
    (OUTPUT_DIR / "nested_code_object_comparison.txt").write_text(nested_text, encoding="utf-8")
    print(f"  Written: recovery_llm_worker/nested_code_object_comparison.txt")
    
    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)

if __name__ == "__main__":
    main()
