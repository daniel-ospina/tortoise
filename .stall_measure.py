import time, sys
sys.path.insert(0, ".")
from tortoise import monitoring
from unittest.mock import MagicMock

class FakeSDK:
    def _get_proj(self):
        proj = MagicMock(); proj.g.query.return_value = MagicMock(result_set=[[1]])
        return proj

monitoring._reset_probe_worker()
box = {"e": 0.0}
def slow_acquire():
    b = time.monotonic(); time.sleep(0.5); box["e"] = time.monotonic() - b
    return FakeSDK()
t0 = time.monotonic()
r = monitoring.probe_db(acquire=slow_acquire)
total = (time.monotonic() - t0) * 1000
print("latency_ms=%.1f total=%.1f acquisition=%.1f  old_bound(<250)=%s  new_bound<=%.1f = %s"
      % (r["latency_ms"], total, box["e"]*1000, r["latency_ms"] < 250,
         total - box["e"]*1000 + 100, r["latency_ms"] <= total - box["e"]*1000 + 100))
