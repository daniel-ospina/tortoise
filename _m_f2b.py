import importlib.util, re
spec = importlib.util.spec_from_file_location("m", "tests/test_9397_about_edge_properties.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
variants = {
 "article-optional, prop|props|confidence":
   rf"\b(?:write|writes|carry|carries|set|sets|store|stores)\s+(?:a|an|the)?\s*(?:[\w'’]+'s\s+)?(?:propert(?:y|ies)|confidence)\b",
 "article-optional + attribute set":
   rf"\b(?:write|writes|carry|carries|set|sets|store|stores)\s+(?:a|an|the)?\s*(?:[\w'’]+'s\s+)?(?:propert(?:y|ies)|confidence|attribute set)\b",
}
RED=["`projection._rebuild_all_replay` writes `confidence` on an about edge.",
     "`projection._rebuild_all_replay` writes a property on an about edge.",
     "`projection._rebuild_all_replay` carries the binder's `confidence` on an about edge."]
GREEN=["`projection._rebuild_all_replay` writes no property on an about edge."]
for name, pat in variants.items():
    m.EXEMPT_COUNTED_PATTERNS = ((re.compile(pat), (), ""),)
    miss=[s for s in RED if not m._denied_verdicts([("p",s)])]
    fr=[s for s in GREEN if m._denied_verdicts([("p",s)])]
    docs=m._denied_verdicts(m._census_sections())
    print(name, "| misses:", len(miss), "| false-green-ctrl:", len(fr), "| docs offenders:", len(docs))
    for d in docs[:3]: print("    ", d[:180])
