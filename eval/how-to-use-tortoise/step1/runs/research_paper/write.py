"""Record the Lewis et al. (2020) RAG paper finding into Tortoise."""
from __future__ import annotations

import json
import sys
import warnings

warnings.filterwarnings("ignore")

from tortoise.sdk import TortoiseSDK  # noqa: E402

sdk = TortoiseSDK()

log = {"wrote": [], "edges": [], "counts": {}}


def note(text, method, kind, why):
    log["wrote"].append({"text": text, "method": method, "kind": kind, "why": why})


def nid(res):
    """Pull the node id out of whatever shape a door returns."""
    if isinstance(res, dict):
        node = res.get("node")
        if isinstance(node, dict) and node.get("id"):
            return node["id"]
        for k in ("id", "node_id", "operator_id"):
            if res.get(k):
                return res[k]
        for v in res.values():
            if isinstance(v, dict) and v.get("id"):
                return v["id"]
    raise RuntimeError(f"no id in {res!r}")


# ---------------------------------------------------------------- provenance
src = sdk.create_source(
    "https://arxiv.org/abs/2005.11401",
    "research",
    tier="high",
    title="Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
    sourceDate="2020-05-22",
    summary=(
        "Lewis et al., NeurIPS 2020 (arXiv:2005.11401). RAG models outperform a "
        "parametric-only seq2seq baseline by 44.2% Exact Match on Natural "
        "Questions; the gain is unstable when the retriever is frozen; the "
        "authors argue joint retriever+generator fine-tuning is necessary, "
        "based on a single ablation."
    ),
    topics=["retrieval-augmented generation", "question answering", "seq2seq"],
)
S = nid(src)
note(
    "Lewis et al. (2020), 'Retrieval-Augmented Generation for Knowledge-Intensive "
    "NLP Tasks', NeurIPS 2020, arXiv:2005.11401, doi/venue/date",
    "create_source", "research", "the paper is the document every claim derives from",
)

subj = sdk.create_subject(
    "Lewis et al. (2020)",
    subjectKind="authorGroup",
    role="authors of arXiv:2005.11401",
)
SUBJ = nid(subj)
note("Lewis et al. (2020) — the paper's authors",
     "create_subject", "authorGroup", "who asserts the finding")

# ------------------------------------------------------------------- objects
rag = sdk.create_object(
    "Retrieval-Augmented Generation (RAG)",
    objectKind="architecture",
    description=(
        "Retriever plus seq2seq generator, jointly fine-tuned, conditioning "
        "generation on retrieved documents."
    ),
)
RAG = nid(rag)
note("Retrieval-Augmented Generation (RAG) — retriever + generator, jointly fine-tuned",
     "create_object", "architecture", "the model being evaluated, and the subject of the necessity claim")

base = sdk.create_object(
    "Parametric-only seq2seq baseline",
    objectKind="architecture",
    description="Seq2seq model with no retrieval component; the comparison baseline.",
)
BASE = nid(base)
note("Parametric-only seq2seq baseline — the comparison condition",
     "create_object", "architecture", "the 44.2% delta is measured against this")

nq = sdk.create_object(
    "Natural Questions (NQ)",
    objectKind="asset",
    description="Question-answering benchmark used as the evaluation set.",
)
NQ = nid(nq)
note("Natural Questions (NQ) — the evaluation benchmark",
     "create_object", "asset", "the 44.2% EM figure is only meaningful with its dataset")


# -------------------------------------------------------------------- points
def point(kind, content, why, **props):
    res = sdk.create_point(kind, content, dedup=True, extractedFrom=[S], **props)
    if res.get("advisories"):
        print("ADVISORY", res["advisories"], file=sys.stderr)
    note(content, "create_point", kind, why)
    return nid(res)


P_RESULT = point(
    "observation",
    "RAG models outperform a parametric-only seq2seq baseline by 44.2% Exact Match "
    "on Natural Questions.",
    "the headline quantitative result, with metric, size and dataset",
    metric="exactMatch",
    delta="44.2%",
    dataset="Natural Questions",
    comparison="RAG vs parametric-only seq2seq baseline",
    assertConfidence="reported by the source, not independently replicated here",
)

P_UNSTABLE = point(
    "observation",
    "The reported RAG gain is unstable when the retriever is frozen.",
    "the boundary condition under which the gain does not hold",
    condition="retriever frozen",
    stability="unstable",
)

P_NECESSARY = point(
    "statement",
    "Joint fine-tuning of the retriever and the generator is necessary for the "
    "reported gains.",
    "the paper's necessity conclusion — the claim the ablation is meant to license",
    assertedBy="Lewis et al. (2020)",
    scope="the reported gains of RAG",
)

P_SINGLE_ABLATION = point(
    "observation",
    "The necessity argument rests on a single ablation, and the paper reports no "
    "further ablations corroborating it.",
    "records how thin the evidence behind the necessity claim is",
    evidenceBase="single ablation",
    methodologicalCaveat=True,
)

# -------------------------------------------------------------------- events
ev = sdk.create_event(
    "Ingest: Lewis et al. (2020) RAG — 44.2% Exact Match gain on Natural Questions "
    "vs a parametric-only seq2seq baseline, unstable under a frozen retriever; "
    "joint fine-tuning argued necessary from a single ablation",
    "extraction",
    aboutSubject=SUBJ,
    aboutDocument=S,
    aboutPoint=P_RESULT,
)
E = nid(ev)
note(
    "Ingest event: the findings of arXiv:2005.11401 recorded on this date",
    "create_event", "extraction", "the act of recording the paper is itself an episode",
)

# --------------------------------------------------------------- structural
def edge(relation, frm, to, label):
    res = sdk.create_edge(relation, frm, to)
    log["edges"].append({"relation": relation, "from": label, "to": to,
                         "created": res.get("created")})
    if not res.get("created"):
        print("EDGE NOT CREATED", relation, label, to, file=sys.stderr)


edge("authoredBy", S, SUBJ, "source:arXiv:2005.11401")
# point -> source provenance is already wired by create_point(extractedFrom=[S]);
# no duplicate references edge is added here.

edge("related", P_RESULT, P_UNSTABLE, "point:result")
edge("related", P_SINGLE_ABLATION, P_NECESSARY, "point:single-ablation")
edge("aboutObject", P_RESULT, RAG, "point:result")
edge("aboutObject", P_RESULT, BASE, "point:result")
edge("aboutObject", P_RESULT, NQ, "point:result")
edge("aboutObject", P_UNSTABLE, RAG, "point:unstable")
edge("aboutObject", P_NECESSARY, RAG, "point:necessary")

# ---------------------------------------------------------------- epistemic
op = sdk.create_operator(
    "IMPL",
    source_id=P_UNSTABLE,
    target_ids=[P_NECESSARY],
    label="supports",
    semantics="supports",
    direction="->",
)
OP = nid(op)
note(
    "IMPL: 'the gain is unstable when the retriever is frozen' -> 'joint "
    "fine-tuning of retriever and generator is necessary'",
    "create_operator", "IMPL", "the paper's inference chain, isolated as an attackable bridge",
)

mit = sdk.mitigate_operator(
    OP,
    reason=(
        "The instability-to-necessity inference rests on a single ablation; the "
        "paper reports no further ablations or replications, so the necessity "
        "claim is under-evidenced rather than refuted."
    ),
    strength=0.30,
)
MIT = nid(mit)
note(
    "Mitigation (strength 0.30): the necessity claim rests on a single ablation",
    "mitigate_operator", "mitigation",
    "damps the inference to necessity without claiming it is false",
)
edge("related", MIT, P_SINGLE_ABLATION, "mitigation:single-ablation")

ids = {
    "source": S, "subject": SUBJ, "object:RAG": RAG, "object:baseline": BASE,
    "object:NQ": NQ, "event:ingest": E, "operator:IMPL": OP, "mitigation": MIT,
    "point:result": P_RESULT, "point:unstable": P_UNSTABLE,
    "point:necessary": P_NECESSARY, "point:single-ablation": P_SINGLE_ABLATION,
}

proj = sdk._get_proj()
st = proj.g.query(
    "MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c ORDER BY l"
)
counts = {row[0]: row[1] for row in st.result_set}
edge_counts = {row[0]: row[1] for row in proj.g.query(
    "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY t"
).result_set}

print(json.dumps({"log": log, "ids": ids, "counts": counts,
                  "edges": edge_counts}, indent=1))
