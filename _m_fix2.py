import importlib.util, re
spec = importlib.util.spec_from_file_location("m", "tests/test_9397_about_edge_properties.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

RESTRICTION = ("other than","outside","beyond","except","aside from","apart from",
               "already holds","already carried","pre-existing","excluding")

def restriction_in_clause(low, match):
    hi = min((pos for pos in (low.find(b, match.start()) for b in m._CLAUSE_BREAKS) if pos != -1),
             default=len(low))
    window = low[match.start():min(hi, match.start()+60)]
    return any(r in window for r in RESTRICTION)

PROP = m._PROPERTY_NOUN
NEWPATS = [
 # no-property family: add the WRITE and KEEP verbs (F1)
 (re.compile(rf"\b(?:carry|carries|write|writes|keep|keeps)\s+(?:no\s+{PROP}|nothing)\b"),
  ("`sdk.supersede_point` writes no property on an about edge.",
   "`sdk.supersede_point` writes no `confidence` on an about edge.",
   "`sdk.supersede_point` keeps no property on an about edge."),
  "`sdk.supersede_point` carries no property outside the RelationUpsert whitelist."),
 # negated-predicate family: add KEEP (F3) + restriction escape (F4)
 (re.compile(rf"\b(?:does\s+not|do\s+not|never|no\s+longer)\s+(?:count|counts|set|sets|write|writes|carry|carries|keep|keeps)\b[^.;,]{{0,40}}?\b(?:{PROP}|writers?|census|counted)\b"),
  ("`sdk.supersede_point` does not keep the binder's `confidence` on an about edge.",
   "`sdk.supersede_point` never keeps the binder's `confidence` on an about edge."),
  "Because the carry is `ON CREATE`, `sdk.supersede_point` never sets a property on an edge the successor already holds."),
 # ACTIVE voice of "excluded from the census" (F5)
 (re.compile(r"\b(?:excludes?|omits?)\b[^;,]{0,40}?\bfrom\s+the\s+(?:census|counted|writer)"),
  ("The census excludes `sdk.supersede_point` from the census of writers.",),
  "`sdk.supersede_point` is counted among the writers."),
]
m.DENIAL_PATTERNS = tuple(NEWPATS) + m.DENIAL_PATTERNS
# apply the restriction escape to BOTH rule paths
_odc = m._denies_the_class_by_pattern
def by_pattern(sentence):
    low = sentence.lower(); hits=[]
    for pattern, _a, _b in m.DENIAL_PATTERNS:
        for match in pattern.finditer(low):
            if not m._history_in_clause(low, match) and not m._class_marker_negated(low, match) \
               and not restriction_in_clause(low, match):
                hits.append(match.group(0))
    return hits
m._denies_the_class_by_pattern = by_pattern
_od = m._denies_the_class
def denies_class(sentence, marker):
    low = sentence.lower()
    return any(not m._history_in_clause(low, mt) and not m._class_marker_negated(low, mt)
               and not restriction_in_clause(low, mt)
               for mt in re.finditer(re.escape(marker.lower()), low))
m._denies_the_class = denies_class

RED = [
 ("F1 write no property","`sdk.supersede_point` writes no property on an about edge."),
 ("F1 write no confidence","`sdk.supersede_point` writes no `confidence` on an about edge."),
 ("F3 does not keep","`sdk.supersede_point` does not keep the binder's `confidence` on an about edge."),
 ("F3 never keeps","`sdk.supersede_point` never keeps the binder's `confidence` on an about edge."),
 ("F5 excludes","The census excludes `sdk.supersede_point` from the census of writers."),
 ("ctrl carry no property","`sdk.supersede_point` carries no property on an about edge."),
 ("ctrl never sets","`sdk.supersede_point` never sets a property on the about edge."),
 ("ctrl drop the property","`sdk.supersede_point` is said to drop the property."),
]
GREEN = [
 ("F4 outside","`sdk.supersede_point` carries no property outside the RelationUpsert whitelist."),
 ("F4 relative","Because the carry is `ON CREATE`, `sdk.supersede_point` never sets a property on an edge the successor already holds."),
 ("F4 other than","`sdk.supersede_point` carries no property other than `confidence`."),
 ("true keep","`sdk.supersede_point` keeps the binder's `confidence` across a supersede."),
 ("true attrs","`DirectEdgeRepoint` carries no `attrs`."),
 ("true not only","`sdk.supersede_point` is not the only writer that carries the set by property MAP."),
 ("true history","`sdk.supersede_point` was a declared FALSE POSITIVE before `#9417`; it is a counted MAP-form writer now."),
 ("true rebuild note","The REBUILD half of the `sdk.supersede_point` supersede transfer is still bare (`#10139`)."),
]
for lbl,s in RED:
    print(("RED  " if m._denied_verdicts([("p",s)]) else "GREEN"),"| want RED  |",lbl)
for lbl,s in GREEN:
    o=m._denied_verdicts([("p",s)])
    print(("RED  " if o else "GREEN"),"| want GREEN|",lbl, (o[0][:70] if o else ""))
# F2: EXEMPT direction
m.EXEMPT_COUNTED_MARKERS = m.EXEMPT_COUNTED_MARKERS + ("writes a property","writes the property","no longer the exemption","not an exemption")
for lbl, sent in (("F2 writes a property","`projection._rebuild_all_replay` writes a property on an about edge."),
                  ("F2 writes confidence","`projection._rebuild_all_replay` writes `confidence` on an about edge."),
                  ("F2 no longer the exemption","`projection._rebuild_all_replay` is no longer the exemption."),
                  ("F2 not an exemption","`projection._rebuild_all_replay` is not an exemption any more."),
                  ("true exempt writes no property","`projection._rebuild_all_replay` writes no property on an about edge.")):
    print(("RED  " if m._denied_verdicts([("p",sent)]) else "GREEN"), "|", lbl)
# F6: property-bearing as MAP class vocabulary
m.CLASS_VERDICT_MARKERS = dict(m.CLASS_VERDICT_MARKERS)
m.CLASS_VERDICT_MARKERS["MAP"] = m.CLASS_VERDICT_MARKERS["MAP"] + ("property bearing",)
for lbl, sent in (("F6 not property-bearing","`sdk.supersede_point` is not property-bearing."),
                  ("true property-bearing","`sdk.supersede_point` is property-bearing."),
                  ("true none currently","none currently property-bearing")):
    o=m._denied_verdicts([("p",sent)])
    print(("RED  " if o else "GREEN"), "|", lbl, (o[0][:70] if o else ""))
print("docs denied:", len(m._denied_verdicts(m._census_sections())))
for d in m._denied_verdicts(m._census_sections())[:3]: print("   ", d[:200])
print("docs unstated:", m._unstated_verdicts(m._census_sections()))
