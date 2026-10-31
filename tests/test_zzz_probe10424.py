"""TEMP probe — measurement only, never committed."""
import sys
import tests.test_longmem_runner as M


def test_probe(tmp_path):
    import sys
    filler = [{"role": "user", "content": f"planning detail number {i} "
               "about the destination for the upcoming trip itinerary",
               "has_answer": False} for i in range(19)] + [
        {"role": "user", "content": "the destination is Kyoto",
         "has_answer": True}]
    q = {
        "question_id": "dupe_q",
        "question_type": "single-session-user",
        "question": "what is the destination",
        "answer": "Kyoto",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["dupe-s0"],
        "haystack_dates": ["2025-06-10"],
        "answer_session_ids": ["dupe-s0"],
        "haystack_sessions": [filler],
    }
    sdk = M._fresh_sdk(tmp_path)
    try:
        M.ingest_haystack(sdk, q, chunk_turns=2)
        ret = M.retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                      max_context_tokens=400)
    finally:
        sdk.close()
    untrimmed = M._estimate_tokens(
        M.render_context(ret["hits"], question_date=q.get("question_date") or None))
    print("PROBE untrimmed", untrimmed, "capped", ret["context_tokens"],
          "retr", ret["dedup_stats"]["chunks_retrieved"],
          "cappedn", ret["dedup_stats"]["chunks_capped"],
          "hits", len(ret["hits"]), "ctx", len(ret["context_points"]),
          file=sys.stderr)
