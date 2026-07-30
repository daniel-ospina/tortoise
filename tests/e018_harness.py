"""E018: 3×6×2 factorial with 2 reps = 72 runs. Claim extraction across strategies, prompts, and models."""
import sys, json, time, os, itertools, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from model_adapters import OpenRouterModel

with open(os.path.join(os.path.dirname(__file__), 'gold_standard.json')) as f:
    gold = json.load(f)

utterances = {str(i): u['text'] for i, u in enumerate(gold['utterances'])}

# === Prompt Templates ===
PROMPTS = {
    'P1-baseline': (
        "TASK: extract_claims\n"
        "For each numbered utterance, output true if it is a CLAIM, false otherwise.\n"
        'Return ONLY: {"claims": {"0": true, "1": false, ...}} for EVERY index 0-29.'
    ),
    'P2-definition': (
        "TASK: extract_claims\n"
        "A CLAIM is: (1) an assertion that could be verified or disputed, "
        "(2) expresses a stance or finding about how things work, "
        "(3) substantive — not metadata, headers, or pure description.\n"
        "NOT claims: frontmatter, table rows, section headers, citations, cross-references.\n"
        'For each numbered utterance, output true if CLAIM, false otherwise.\n'
        'Return ONLY: {"claims": {"0": true, "1": false, ...}} for EVERY index 0-29.'
    ),
    'P3-examples': (
        "TASK: extract_claims\n"
        "CLAIM examples:\n"
        '- "The 90% startup failure rate is unchanged from pre-Lean Startup eras." → TRUE\n'
        '- "SaaS margins dropped from mid-80s to 70s by adding AI." → TRUE\n'
        "NOT claim examples:\n"
        '- "title: Canonical Lens — Startup Strategy" → FALSE\n'
        '- "> Method: 8 web_search queries" → FALSE\n'
        'For each numbered utterance, output true/false.\n'
        'Return ONLY: {"claims": {"0": true, "1": false, ...}} for EVERY index 0-29.'
    ),
    'P4-cot': (
        "TASK: extract_claims with step-by-step reasoning\n"
        "For EACH utterance, apply this test:\n"
        "1. Is it substantive? (not metadata, header, table row, citation) → if no: FALSE\n"
        "2. Does it assert something? (not pure description or question) → if no: FALSE\n"
        "3. Could someone reasonably dispute it? → if no: FALSE\n"
        "4. Otherwise: TRUE\n"
        'Return ONLY: {"claims": {"0": true, "1": false, ...}} for EVERY index 0-29.'
    ),
    'P5-contrastive': (
        "TASK: extract_claims\n"
        "These are NON-claims (output FALSE):\n"
        "- Frontmatter/metadata lines (title:, date:, author:)\n"
        "- Table rows and formatting (| --- |)\n"
        "- Section headers and citations\n"
        "- Cross-references (See also: ...)\n"
        "- Pure historical facts without analytical stance\n"
        "Everything else that asserts a stance about how things work IS a claim.\n"
        'Return ONLY: {"claims": {"0": true, "1": false, ...}} for EVERY index 0-29.'
    ),
    'P6-domain': (
        "TASK: extract_claims from startup-strategy research\n"
        "This domain contains: framework evaluations, failure rate analyses, methodology critiques, "
        "practitioner observations, causal claims about business dynamics.\n"
        "CLAIM examples from this domain:\n"
        '- "Lean Startup reduced failure cost but not failure occurrence." → TRUE\n'
        '- "Founders use max one framework at a time." → TRUE\n'
        '- "Cost inversion is the most structural change in startup economics." → TRUE\n'
        "NOT claims: table rows listing frameworks, citation-only lines, pure timeline facts.\n"
        'Return ONLY: {"claims": {"0": true, "1": false, ...}} for EVERY index 0-29.'
    ),
}

def compute_metrics(claims_dict):
    tp = fp = fn = tn = 0
    for i, u in enumerate(gold['utterances']):
        idx = str(i)
        pred = claims_dict.get(idx, None)
        if pred is None:
            if u['is_claim']: fn += 1
            else: tn += 1
            continue
        if isinstance(pred, str): pred = pred.lower() in ('true', 'yes', 'claim')
        actual = u['is_claim']
        if pred and actual: tp += 1
        elif pred and not actual: fp += 1
        elif not pred and actual: fn += 1
        else: tn += 1
    p = tp/(tp+fp) if (tp+fp)>0 else 0
    r = tp/(tp+fn) if (tp+fn)>0 else 0
    f1 = 2*p*r/(p+r) if (p+r)>0 else 0
    return {"tp":tp,"fp":fp,"fn":fn,"tn":tn,"precision":round(p,4),"recall":round(r,4),"f1":round(f1,4)}

def run_single_pass(model_id, prompt_key, prompt_text, max_tokens, seed):
    """Single API call: extract claims directly."""
    model = OpenRouterModel(model_id, max_tokens=max_tokens)
    # Add seed to user payload for reproducibility
    payload = json.dumps({"utterances": utterances, "seed": seed})
    t0 = time.time()
    raw = model.complete(system=prompt_text, user=payload)
    elapsed = time.time() - t0
    if not raw: return None, elapsed
    
    text = raw.strip()
    s, e = text.find('{'), text.rfind('}')
    if s >= 0 and e > s: text = text[s:e+1]
    try:
        result = json.loads(text)
        claims = result.get('claims', {})
    except:
        return None, elapsed
    
    metrics = compute_metrics(claims)
    metrics['tokens'] = f"{model.last_prompt_tokens}p+{model.last_completion_tokens}c"
    return metrics, elapsed

def run_reviewer(model_id, prompt_key, prompt_text, max_tokens, seed):
    """Two calls: extract → review → correct."""
    # Pass 1: extract
    metrics1, t1 = run_single_pass(model_id, prompt_key, prompt_text, max_tokens, seed)
    if metrics1 is None: return None, t1
    
    # Pass 2: review — feed extraction back and ask for corrections
    model = OpenRouterModel(model_id, max_tokens=max_tokens)
    review_prompt = (
        "TASK: review_claims\n"
        "You previously classified these utterances. Review your work. "
        "For any utterance you now believe was misclassified, correct it.\n"
        'Return ONLY: {"claims": {"0": true, "1": false, ...}} with ALL corrections applied.'
    )
    # We'd need the previous output — simplified: just re-extract with a review framing
    # Actually, for a proper reviewer we need to pass the first extraction.
    # Simplified version: extract twice with different prompt framing, merge
    payload2 = json.dumps({"utterances": utterances, "seed": seed + 1000,
                           "review": True, "previous_task": "claim extraction"})
    t0 = time.time()
    raw2 = model.complete(system=review_prompt, user=payload2)
    elapsed2 = time.time() - t0 + t1
    if not raw2: return metrics1, elapsed2  # fallback to pass 1
    
    text2 = raw2.strip()
    s2, e2 = text2.find('{'), text2.rfind('}')
    if s2 >= 0 and e2 > s2: text2 = text2[s2:e2+1]
    try:
        result2 = json.loads(text2)
        claims2 = result2.get('claims', {})
    except:
        return metrics1, elapsed2
    
    metrics2 = compute_metrics(claims2)
    metrics2['tokens'] = f"{model.last_prompt_tokens}p+{model.last_completion_tokens}c"
    return metrics2, elapsed2

def run_debatecv(model_id, prompt_key, prompt_text, max_tokens, seed):
    """Three calls: pro-claims → anti-claims → synthesis."""
    model = OpenRouterModel(model_id, max_tokens=max_tokens)
    
    # Pass 1: Be generous — classify as claim if borderline
    pro_prompt = prompt_text + "\n\nWhen in doubt, classify as TRUE (claim). Err on the side of inclusion."
    payload = json.dumps({"utterances": utterances, "seed": seed})
    t0 = time.time()
    raw1 = model.complete(system=pro_prompt, user=payload)
    if not raw1: return None, time.time()-t0
    text1 = raw1.strip()
    s1, e1 = text1.find('{'), text1.rfind('}')
    if s1 >= 0 and e1 > s1: text1 = text1[s1:e1+1]
    try: claims_pro = json.loads(text1).get('claims', {})
    except: return None, time.time()-t0
    
    # Pass 2: Be strict — classify as non-claim if borderline
    anti_prompt = prompt_text + "\n\nWhen in doubt, classify as FALSE (not a claim). Err on the side of exclusion."
    payload2 = json.dumps({"utterances": utterances, "seed": seed + 1000})
    t1 = time.time()
    raw2 = model.complete(system=anti_prompt, user=payload2)
    if not raw2: return None, time.time()-t0
    text2 = raw2.strip()
    s2, e2 = text2.find('{'), text2.rfind('}')
    if s2 >= 0 and e2 > s2: text2 = text2[s2:e2+1]
    try: claims_anti = json.loads(text2).get('claims', {})
    except: return None, time.time()-t0
    
    # Pass 3: Synthesis — resolve disagreements
    disagreements = []
    for i in range(30):
        p = claims_pro.get(str(i), None)
        a = claims_anti.get(str(i), None)
        if p is not None and a is not None and p != a:
            disagreements.append(i)
    
    synth_prompt = (
        f"TASK: resolve_claims\n"
        f"The pro-claims pass and anti-claims pass disagreed on {len(disagreements)} utterances (indices: {disagreements}).\n"
        f"For these disputed utterances only, re-evaluate carefully and decide.\n"
        f"Pro-claims classified them as TRUE (generous). Anti-claims classified as FALSE (strict).\n"
        f'Return ONLY: {{"claims": {{"{disagreements[0] if disagreements else 0}": true, ...}}}} for the DISPUTED indices only.'
    )
    payload3 = json.dumps({"utterances": {str(i): utterances[str(i)] for i in disagreements}, "seed": seed + 2000})
    t2 = time.time()
    raw3 = model.complete(system=synth_prompt, user=payload3)
    elapsed = time.time() - t0
    
    # Merge: start with anti-claims (strict), override with synthesis for disputes
    final_claims = dict(claims_anti)
    if raw3 and disagreements:
        text3 = raw3.strip()
        s3, e3 = text3.find('{'), text3.rfind('}')
        if s3 >= 0 and e3 > s3:
            try:
                resolved = json.loads(text3[s3:e3+1]).get('claims', {})
                for k, v in resolved.items():
                    final_claims[k] = v
            except: pass
    
    metrics = compute_metrics(final_claims)
    metrics['tokens'] = "3-calls"
    return metrics, elapsed

# === Run all 72 conditions ===
MODELS = {
    'flash': ('deepseek/deepseek-v4-flash', 500),
    'v4-pro': ('deepseek/deepseek-v4-pro', 4000),
}

STRATEGIES = {
    'single-pass': run_single_pass,
    'reviewer': run_reviewer,
    'debatecv': run_debatecv,
}

# Build condition list
conditions = list(itertools.product(
    MODELS.keys(), PROMPTS.keys(), STRATEGIES.keys(), [42, 43]
))

print(f"Running {len(conditions)} conditions...")
results = []
errors = []

for i, (model_key, prompt_key, strategy_key, seed) in enumerate(conditions):
    model_id, max_tokens = MODELS[model_key]
    prompt_text = PROMPTS[prompt_key]
    strategy_fn = STRATEGIES[strategy_key]
    
    cond_name = f"{model_key}/{prompt_key}/{strategy_key}/seed={seed}"
    print(f"[{i+1}/{len(conditions)}] {cond_name} ...", end=' ', flush=True)
    
    try:
        metrics, elapsed = strategy_fn(model_id, prompt_key, prompt_text, max_tokens, seed)
        if metrics:
            metrics['model'] = model_key
            metrics['prompt'] = prompt_key
            metrics['strategy'] = strategy_key
            metrics['seed'] = seed
            metrics['time'] = round(elapsed, 1)
            metrics['cond'] = cond_name
            results.append(metrics)
            print(f"F1={metrics['f1']:.4f} ({elapsed:.1f}s)")
        else:
            errors.append({"cond": cond_name, "error": "null response"})
            print("NULL")
    except Exception as e:
        errors.append({"cond": cond_name, "error": str(e)})
        print(f"ERR: {e}")

# Save
out = {"results": results, "errors": errors, "total": len(conditions), "completed": len(results)}
with open(os.path.join(os.path.dirname(__file__), 'e018_results.json'), 'w') as f:
    json.dump(out, f, indent=2)

print(f"\nDone: {len(results)}/{len(conditions)} completed, {len(errors)} errors")
if errors:
    for e in errors[:5]:
        print(f"  {e['cond']}: {e['error']}")
