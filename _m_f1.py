import importlib.util
spec = importlib.util.spec_from_file_location("m", "tests/test_9397_about_edge_properties.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

base = m.DENIAL_MARKERS
for extra in ("writes no property", "keeps no property"):
    m.DENIAL_MARKERS = base + (extra,)
    docs = m._denied_verdicts(m._census_sections())
    probe = m._denied_verdicts([("probe", "`sdk.supersede_point` writes no property on an about edge.")])
    print(repr(extra), "| docs offenders:", len(docs), "| probe:", "RED" if probe else "GREEN")
    for d in docs[:2]:
        print("    ", d[:200])
m.DENIAL_MARKERS = base
