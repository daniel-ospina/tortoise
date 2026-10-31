import os, sys, time, logging
logging.basicConfig(level=logging.DEBUG)
if len(sys.argv) > 1 and sys.argv[1] == "tmp-home":
    import tempfile
    os.environ["HOME"] = tempfile.mkdtemp()
from tortoise.embeddings import EmbeddingModel
t0 = time.time()
m = EmbeddingModel.get()
print("get() ->", m, "%.1fs" % (time.time() - t0))
print("kind:", EmbeddingModel._last_failure_kind, "err:", repr(EmbeddingModel._last_error)[:400])
try:
    from huggingface_hub import constants
    print("HF_HUB_CACHE:", constants.HF_HUB_CACHE)
except Exception as e:
    print("no constants", e)
