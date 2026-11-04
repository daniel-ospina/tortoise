import importlib.util
spec = importlib.util.spec_from_file_location("m", "tests/test_9397_about_edge_properties.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
RED = [
 ("F1 writes no property","`sdk.supersede_point` writes no property on an about edge."),
 ("F1 writes no confidence","`sdk.supersede_point` writes no `confidence` on an about edge."),
 ("F3 does not keep","`sdk.supersede_point` does not keep the binder's `confidence` on an about edge."),
 ("F3 never keeps","`sdk.supersede_point` never keeps the binder's `confidence` on an about edge."),
 ("F5 active excludes","The census excludes `sdk.supersede_point` from the census of writers."),
 ("F2 exempt writes a property","`projection._rebuild_all_replay` writes a property on an about edge."),
 ("F2 exempt writes confidence","`projection._rebuild_all_replay` writes `confidence` on an about edge."),
 ("F2 exempt carries confidence","`projection._rebuild_all_replay` carries the binder's `confidence` on an about edge."),
 ("F2 no longer the exemption","`projection._rebuild_all_replay` is no longer the exemption."),
 ("F2 not an exemption","`projection._rebuild_all_replay` is not an exemption any more."),
 ("F6 not property-bearing","`sdk.supersede_point` is not property-bearing."),
]
GREEN = [
 ("F4 outside","`sdk.supersede_point` carries no property outside the RelationUpsert whitelist."),
 ("F4 relative","Because the carry is `ON CREATE`, `sdk.supersede_point` never sets a property on an edge the successor already holds."),
 ("F4 other than","`sdk.supersede_point` carries no property other than `confidence`."),
 ("true exempt writes no property","`projection._rebuild_all_replay` writes no property on an about edge."),
 ("true keep","`sdk.supersede_point` keeps the binder's `confidence` across a supersede."),
 ("true attrs","`DirectEdgeRepoint` carries no `attrs`."),
 ("true not only","`sdk.supersede_point` is not the only writer that carries the set by property MAP."),
 ("true history","`sdk.supersede_point` was a declared FALSE POSITIVE before `#9417`; it is a counted MAP-form writer now."),
 ("true rebuild note","The REBUILD half of the `sdk.supersede_point` supersede transfer is still bare (`#10139`)."),
 ("true none currently property bearing","`session_source_migration._move_in` writes nothing; none currently property-bearing."),
]
bad=0
for lbl,s in RED:
    o = m._denied_verdicts([("p",s)])
    if not o: bad+=1; print("MISS (want RED)  |", lbl)
for lbl,s in GREEN:
    o = m._denied_verdicts([("p",s)])
    if o: bad+=1; print("FALSE RED        |", lbl, "|", o[0][:110])
print("failures:", bad)
print("docs denied:", m._denied_verdicts(m._census_sections()))
print("docs unstated:", m._unstated_verdicts(m._census_sections()))
print("docs counts:", m._declared_false_positive_counts(m._census_sections()))
