import importlib.util
spec = importlib.util.spec_from_file_location("m", "tests/test_9397_about_edge_properties.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
flat = (m._flat(m._doc(m.STORAGE)).lower(), m._flat(m._doc(m.EXTRACTOR)).lower())
for s in ("no longer the exemption","no longer an exemption","not an exemption","property bearing","writes a property","writes the property"):
    print(repr(s), "live:", any(s in t for t in flat))
