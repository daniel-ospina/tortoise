import importlib.util
spec = importlib.util.spec_from_file_location("m", "tests/test_9397_about_edge_properties.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
cases = [
 ("F1 write-verb no property", "`sdk.supersede_point` writes no property on an about edge."),
 ("F1 write-verb no confidence", "`sdk.supersede_point` writes no `confidence` on an about edge."),
 ("F1 control carry", "`sdk.supersede_point` carries no property on an about edge."),
 ("F2 exempt writes a property", "`projection._rebuild_all_replay` writes a property on an about edge."),
 ("F2 exempt writes confidence", "`projection._rebuild_all_replay` writes `confidence` on an about edge."),
 ("F2 exempt no longer the exemption", "`projection._rebuild_all_replay` is no longer the exemption."),
 ("F3 does not keep", "`sdk.supersede_point` does not keep the binder's `confidence` on an about edge."),
 ("F3 never keeps", "`sdk.supersede_point` never keeps the binder's `confidence` on an about edge."),
 ("F3 true affirmative", "`sdk.supersede_point` keeps the binder's `confidence` across a supersede."),
 ("F4 restricted outside", "`sdk.supersede_point` carries no property outside the RelationUpsert whitelist."),
 ("F4 restricted relative", "Because the carry is `ON CREATE`, `sdk.supersede_point` never sets a property on an edge the successor already holds."),
 ("F4 other than", "`sdk.supersede_point` carries no property other than `confidence`."),
 ("F4 writes-no-property other than", "`sdk.supersede_point` writes no property other than the whitelisted `confidence`."),
 ("F5 active excludes", "The census excludes `sdk.supersede_point` from the census of writers."),
 ("F5 control passive", "`sdk.supersede_point` is excluded from the census of writers."),
 ("F6 not property-bearing", "`sdk.supersede_point` is not property-bearing."),
 ("F6 control class negation", "`sdk.supersede_point` is not a re-materializing writer."),
]
for label, s in cases:
    o = m._denied_verdicts([("probe", s)])
    print(("RED  " if o else "GREEN"), "|", label)
print()
print("head docs denied:", m._denied_verdicts(m._census_sections()))
print("head docs unstated:", m._unstated_verdicts(m._census_sections()))
