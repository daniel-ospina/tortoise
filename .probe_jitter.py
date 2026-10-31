import time, statistics, sys
sys.path.insert(0, ".")
from tortoise import monitoring

class FakeSDK:
    def __init__(self, db_ok=True):
        self._db_ok = db_ok
    def _get_proj(self):
        from unittest.mock import MagicMock
        proj = MagicMock()
        proj.g.query.return_value = MagicMock(result_set=[[1]])
        return proj

lat = []
acq = []
for i in range(40):
    monitoring._reset_probe_worker()
    box = {"elapsed": 0.0}
    def slow_acquire(b=box):
        t = time.monotonic(); time.sleep(0.5)
        b["elapsed"] = time.monotonic() - t
        return FakeSDK()
    r = monitoring.probe_db(acquire=slow_acquire)
    lat.append(r["latency_ms"]); acq.append(box["elapsed"]*1000)
print("acquire-shape latency_ms: max=%.1f p50=%.1f n_over_250=%d" % (max(lat), statistics.median(lat), sum(1 for x in lat if x >= 250)))
sdk = []
for i in range(40):
    monitoring._reset_probe_worker()
    sdk.append(monitoring.probe_db(FakeSDK())["latency_ms"])
print("sdk-shape latency_ms: max=%.1f p50=%.1f" % (max(sdk), statistics.median(sdk)))
