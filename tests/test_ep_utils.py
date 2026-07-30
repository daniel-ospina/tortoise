"""Shared EP test utilities (extracted from deprecated SVBP tests)."""
from tortoise.ep import TortoiseEP

class InMemoryEP:
    """Deterministic EP. Fixed factor order → identical output always."""

    def __init__(self, damping=0.5, n_quad=8):
        self.damping = damping
        self.n_quad = n_quad
        self.messages: dict = {}
        self.posteriors: dict = {}

    @staticmethod
    def _nat(a, b): return (a - 1, b - 1)
    @staticmethod
    def _beta(e1, e2): return (max(e1 + 1, 0.01), max(e2 + 1, 0.01))

    def run(self, impl_factors, evidence=None, n_iter=30):
        if evidence:
            for cid, (a, b) in evidence.items():
                self.posteriors[cid] = (a, b)
        for _ in range(n_iter):
            for op_id, op_type, inputs, weight in impl_factors:
                if len(inputs) != 2: continue
                id_a, id_b = inputs
                post_a = self.posteriors.get(id_a, (1.0, 1.0))
                post_b = self.posteriors.get(id_b, (1.0, 1.0))
                msg_a = self.messages.get((op_id, id_a, op_type), (0.0, 0.0))
                msg_b = self.messages.get((op_id, id_b, op_type), (0.0, 0.0))
                pa_e1, pa_e2 = self._nat(*post_a)
                pb_e1, pb_e2 = self._nat(*post_b)
                cav_a = self._beta(pa_e1 - msg_a[0], pa_e2 - msg_a[1])
                cav_b = self._beta(pb_e1 - msg_b[0], pb_e2 - msg_b[1])
                phi_fn = phi_nand if op_type == "NAND" else phi_impl
                mom_a, mom_b = tilted_moments(*cav_a, *cav_b, weight, phi_fn, n_quad=self.n_quad)
                new_a, new_b = moments_to_beta(*mom_a), moments_to_beta(*mom_b)
                raw_a = (self._nat(*new_a)[0] - self._nat(*cav_a)[0], self._nat(*new_a)[1] - self._nat(*cav_a)[1])
                raw_b = (self._nat(*new_b)[0] - self._nat(*cav_b)[0], self._nat(*new_b)[1] - self._nat(*cav_b)[1])
                d = self.damping
                oa, ob = self.messages.get((op_id, id_a, op_type), (0.0, 0.0)), self.messages.get((op_id, id_b, op_type), (0.0, 0.0))
                self.messages[(op_id, id_a, op_type)] = (d*raw_a[0]+(1-d)*oa[0], d*raw_a[1]+(1-d)*oa[1])
                self.messages[(op_id, id_b, op_type)] = (d*raw_b[0]+(1-d)*ob[0], d*raw_b[1]+(1-d)*ob[1])
                for cid in [id_a, id_b]:
                    ea, eb = evidence.get(cid, (1.0,1.0)) if evidence else (1.0,1.0)
                    e1, e2 = self._nat(ea, eb)
                    for (_, c, _), (m1,m2) in self.messages.items():
                        if c == cid: e1 += m1; e2 += m2
                    self.posteriors[cid] = self._beta(e1, e2)


# ═══════════════════════════════════════════════════════════════════
# THEOREM 1: Path independence
# ═══════════════════════════════════════════════════════════════════
