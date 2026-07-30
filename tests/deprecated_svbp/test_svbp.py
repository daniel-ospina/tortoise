"""Tests for svbp — Stein Variational Belief Propagation primitives."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# JAX may not be available — skip all if not
try:
    import jax.numpy as jnp
    JAX_AVAILABLE = True
except ImportError:
    JAX_AVAILABLE = False


@pytest.mark.skipif(not JAX_AVAILABLE, reason="JAX not installed")
class TestSVBPPrimitives:
    def test_sigmoid_bounds(self):
        from tortoise.svbp import sigmoid
        assert float(sigmoid(-100)) < 0.01
        assert float(sigmoid(100)) > 0.99
        assert abs(float(sigmoid(0)) - 0.5) < 0.01

    def test_sigmoid_monotonic(self):
        from tortoise.svbp import sigmoid
        x = jnp.array([-1.0, 0.0, 1.0])
        y = sigmoid(x)
        assert float(y[0]) < float(y[1]) < float(y[2])

    def test_rbf_kernel_shape(self):
        from tortoise.svbp import rbf_kernel
        x = jnp.array([[0.0], [1.0], [2.0]])
        K, grad_K = rbf_kernel(x, 1.0)
        assert K.shape == (3, 3)
        assert grad_K.shape == (3, 3, 1)

    def test_rbf_kernel_positive_definite(self):
        from tortoise.svbp import rbf_kernel
        x = jnp.array([[0.0], [0.5], [1.0]])
        K, _ = rbf_kernel(x, 0.5)
        # Kernel matrix should be symmetric positive
        assert jnp.allclose(K, K.T)

    def test_median_heuristic(self):
        from tortoise.svbp import median_heuristic
        x = jnp.array([[0.0], [0.5], [1.0], [1.5], [2.0]])
        h = median_heuristic(x)
        assert float(h) > 0

    def test_module_imports(self):
        """Verify svbp module can be imported and has expected functions."""
        import tortoise.svbp as svbp
        assert callable(svbp.sigmoid)
        assert callable(svbp.rbf_kernel)
        assert callable(svbp.median_heuristic)
