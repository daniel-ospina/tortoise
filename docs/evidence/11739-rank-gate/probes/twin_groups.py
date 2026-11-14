#!/usr/bin/env python3
"""Twin-group classification for #11739 gate 2 — the script that produced the mechanism figures.

Committed VERBATIM from the one-off pass over the same /tmp/g2-work caches as probes/rank_gate.py,
so the "Why the composed arm loses facts" table is followable instead of one-off. It reads the
assembled shards and the row cache; it needs no model and no encoder (rank is recomputed against
the RAW arm's vectors, exactly as gate.json's A rows do). Run from a tortoise checkout:
    python3 twin_groups.py
"""
import json, collections, numpy as np
d=json.load(open('/tmp/g2-work/comp_texts.json'))
ids,texts=d['ids'],d['texts']
cnt=collections.Counter(texts)
comp=np.linalg.norm(np.vstack([np.load(f'/tmp/g2-work/comp_vec_{i}.npy') for i in range(10)]),axis=1,keepdims=True)
comp=np.vstack([np.load(f'/tmp/g2-work/comp_vec_{i}.npy') for i in range(10)]); comp=comp/np.linalg.norm(comp,axis=1,keepdims=True)
rows=[json.loads(l) for l in open('/tmp/11739-rows.jsonl')]
by={r['id']:r for r in rows}
pop=[by[i] for i in ids]
raw=np.asarray([r['embedding'] for r in pop],np.float32); raw/=np.linalg.norm(raw,axis=1,keepdims=True)
n=len(pop); ranks=np.zeros(n,np.int64); sims=np.zeros(n,np.float32)
CH=1024
for s in range(0,n,CH):
    e=min(s+CH,n); sc=raw[s:e]@comp.T; ss=sc[np.arange(e-s),np.arange(s,e)]
    ranks[s:e]=(sc>ss[:,None]).sum(1)+1; sims[s:e]=ss
g=np.array([cnt[t] for t in texts])
inband=(sims>=0.70)&(sims<0.85); dis=ranks>10
res={
 'population':int(n),
 'distinct_composed_texts':int(len(cnt)),
 'displaced_total':int(dis.sum()),
 'displaced_in_band':int((dis&inband).sum()),
 'displaced_with_ge10_identical_text_twins':int((dis&(g>=11)).sum()),
 'displaced_with_ge10_twins_in_band':int((dis&(g>=11)&inband).sum()),
 'displaced_with_0_twins':int((dis&(g==1)).sum()),
 'displaced_with_0_twins_in_band':int((dis&(g==1)&inband).sum()),
 'displaced_in_boilerplate_ge50':int((dis&(g>=51)).sum()),
 'displaced_with_2_to_10_twins':int((dis&(g>=2)&(g<=10)).sum()),
 'displaced_with_2_to_10_twins_in_band':int((dis&(g>=2)&(g<=10)&inband).sum()),
 'points_in_ge11_inside_top10':int(((g>=11)&(ranks<=10)).sum()),
 'largest_group_rank':int(ranks[int(np.argmax(g))]),
 'largest_group_sim':float(round(float(sims[int(np.argmax(g))]),4)),
 'largest_group':int(g.max()),
 'points_in_groups_gt1':int((g>1).sum()),
 'points_in_groups_ge11':int((g>=11).sum()),
 'median_rank_when_twin_group':float(np.median(ranks[g>1])),
 'median_rank_when_unique':float(np.median(ranks[g==1])),
}
# rank-1 among displaced: what outranks them (twin vs non-twin)
print(json.dumps(res,indent=2))
