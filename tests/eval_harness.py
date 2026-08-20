"""Evaluation harness: precision/recall/F1 for LLM claim extraction across models."""
import sys, json, time, os  # noqa: E401, I001
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from model_adapters import OpenRouterModel, MODELS  # noqa: F401, I001

# Load gold standard
with open(os.path.join(os.path.dirname(__file__), 'gold_standard.json')) as f:
    gold = json.load(f)

# Tortoise point extraction prompt
POINTS_SYS = (
    "TASK: extract_claims\n"
    "You are given numbered transcript utterances. For EACH one, determine if it expresses "
    "a CLAIM (an assertion, finding, or stance that could be true or false). Return JSON:\n"
    '{"claims": {"<index>": true/false, ...}} with one entry per input index.\n'
    "A claim must: (1) assert something that could be verified or disputed, "
    "(2) express a stance or finding about how things work, "
    "(3) be substantive (not metadata, headers, formatting, or pure description).\n"
    "NOT claims: frontmatter fields, table rows, section headers, citations, pure historical facts without stance."
)

def evaluate_model(model_name, model_factory):
    """Run claim extraction and compute precision/recall/F1."""
    model = model_factory()
    
    # Build input: numbered utterances
    utterances = {str(i): u['text'] for i, u in enumerate(gold['utterances'])}
    
    user_payload = json.dumps({"context": "startup-strategy research lenses", "utterances": utterances})
    
    t0 = time.time()
    try:
        raw = model.complete(system=POINTS_SYS, user=user_payload)
    except Exception as e:
        return {"model": model_name, "error": str(e), "time": time.time() - t0}
    elapsed = time.time() - t0
    
    # Parse response
    try:
        # Clean markdown fences
        text = raw.strip()
        if text.startswith('```'):
            text = text.split('```', 2)[1]
            if text.startswith('json'): text = text[4:]  # noqa: E701
        result = json.loads(text)
        claims = result.get('claims', {})
    except (json.JSONDecodeError, KeyError) as e:
        return {"model": model_name, "error": f"Parse error: {e}", "raw": raw[:200], "time": elapsed}
    
    # Compute metrics with semantic matching
    tp = fp = fn = tn = 0
    for i, u in enumerate(gold['utterances']):
        idx = str(i)
        predicted = claims.get(idx, False)
        if isinstance(predicted, str):
            predicted = predicted.lower() in ('true', 'yes', 'claim')
        actual = u['is_claim']
        
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
        elif not predicted and actual:
            fn += 1
        else:
            tn += 1
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    accuracy = (tp + tn) / len(gold['utterances'])
    
    # Cost estimate
    prompt_tokens = getattr(model, 'last_prompt_tokens', 0)
    completion_tokens = getattr(model, 'last_completion_tokens', 0)
    
    return {
        "model": model_name,
        "time": round(elapsed, 1),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "accuracy": round(accuracy, 3),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "raw_response": raw[:300]
    }

if __name__ == '__main__':
    models_to_test = [
        ('deepseek-flash', MODELS['deepseek-flash']),
        ('deepseek-v4-pro', MODELS['deepseek-v4-pro']),
        ('deepseek-r1-xhigh', MODELS['deepseek-r1-xhigh']),
    ]
    
    results = []
    for name, factory in models_to_test:
        print(f'\n{"="*50}')
        print(f'Testing: {name}')
        print(f'{"="*50}')
        r = evaluate_model(name, factory)
        results.append(r)
        if 'error' in r:
            print(f'  ERROR: {r["error"]}')
        else:
            print(f'  Time: {r["time"]}s')
            print(f'  TP={r["tp"]} FP={r["fp"]} FN={r["fn"]} TN={r["tn"]}')
            print(f'  Precision: {r["precision"]:.3f} | Recall: {r["recall"]:.3f} | F1: {r["f1"]:.3f} | Accuracy: {r["accuracy"]:.3f}')
            print(f'  Tokens: {r["prompt_tokens"]} prompt + {r["completion_tokens"]} completion')
            print(f'  Response: {r.get("raw_response","")[:200]}')
    
    # Save results
    out_path = os.path.join(os.path.dirname(__file__), 'eval_results.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f'\nResults saved to {out_path}')
