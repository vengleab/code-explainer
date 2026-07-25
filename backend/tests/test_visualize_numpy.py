"""
Tests for numpy_model.analyze() — the model behind the NumPy canvas page.

Two things are worth checking here, and they pull in opposite directions:
values must come from real NumPy (so seeding, dtypes and broadcasting are not
reimplemented), while slice *geometry* comes from the AST (the result array no
longer knows which rectangle produced it). Both are exercised below, along with
the error messages, which are user-facing copy shown in the sidebar.
"""
import json
import os
import sys
import unittest
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.numpy_model import analyze, ModelError, MAX_DIM, MAX_1D  # noqa: E402
from backend.sandbox import UnsafeCodeError  # noqa: E402
from backend import visualize_numpy  # noqa: E402

HEADER = "import numpy as np\n"
GRID = HEADER + "A = np.arange(36).reshape(6, 6)\n"


class TargetDetectionTests(unittest.TestCase):
    def test_slice_geometry(self):
        model = analyze(GRID + "C = A[1:3, 2:4]")
        target = model["target"]
        self.assertEqual(target["mode"], "slice")
        self.assertEqual((target["r0"], target["r1"], target["c0"], target["c1"]), (1, 3, 2, 4))
        self.assertEqual(target["out"], "C")
        self.assertEqual(target["result"], [[8, 9], [14, 15]])

    def test_open_ended_and_omitted_specs(self):
        target = analyze(GRID + "C = A[2:, :4]")["target"]
        self.assertEqual((target["r0"], target["r1"], target["c0"], target["c1"]), (2, 6, 0, 4))
        target = analyze(GRID + "C = A[1:3]")["target"]
        self.assertEqual((target["r0"], target["r1"], target["c0"], target["c1"]), (1, 3, 0, 6))

    def test_negative_indices_follow_python(self):
        target = analyze(GRID + "C = A[-2:, -3:]")["target"]
        self.assertEqual((target["r0"], target["r1"], target["c0"], target["c1"]), (4, 6, 3, 6))

    def test_bare_integer_index_drops_an_axis(self):
        target = analyze(GRID + "C = A[2]")["target"]
        self.assertEqual((target["r0"], target["r1"], target["c0"], target["c1"]), (2, 3, 0, 6))
        self.assertTrue(target["oneD"])  # real numpy: A[2] is 1-D
        target = analyze(GRID + "C = A[:, 3]")["target"]
        self.assertEqual((target["r0"], target["r1"], target["c0"], target["c1"]), (0, 6, 3, 4))
        self.assertTrue(target["oneD"])

    def test_slice_bounds_may_be_variables(self):
        target = analyze(GRID + "lo = 1\nhi = 4\nC = A[lo:hi, 0:2]")["target"]
        self.assertEqual((target["r0"], target["r1"]), (1, 4))

    def test_one_dimensional_array_indexes_values_not_rows(self):
        model = analyze(HEADER + "A = np.array([1, 2, 3, 4])\nC = A[1:3]")
        target = model["target"]
        self.assertEqual((target["r0"], target["r1"], target["c0"], target["c1"]), (0, 1, 1, 3))
        self.assertEqual(model["arrays"]["A"], [[1, 2, 3, 4]])
        self.assertTrue(model["oneD"]["A"])
        self.assertEqual(target["result"], [[2, 3]])

    def test_mask(self):
        target = analyze(GRID + "C = A[A >= 30]")["target"]
        self.assertEqual(target["mode"], "filter")
        self.assertEqual(target["cmp"], ">=")
        self.assertEqual(target["thresh"], 30)
        self.assertEqual(target["result"], [[30, 31, 32, 33, 34, 35]])
        self.assertTrue(target["oneD"])

    def test_mask_result_may_be_longer_than_a_1d_source(self):
        # The mask preset on the page: ~half of an 8×8 grid passes, which is more
        # values than a 1-D *source* may have. The result wraps in the strip, so
        # the row limit must not apply to it.
        model = analyze(HEADER + "np.random.seed(12345)\n"
                        "A = np.random.randint(0, 100, (8, 8))\nC = A[A > 50]")
        self.assertEqual(model["target"]["mode"], "filter")
        self.assertGreater(len(model["target"]["result"][0]), MAX_1D)

    def test_scalar_broadcast(self):
        target = analyze(GRID + "C = A + 10")["target"]
        self.assertEqual(target["mode"], "scalar")
        self.assertEqual(target["op"], "+")
        self.assertEqual(target["operand"], 10)
        self.assertEqual(target["result"][0][:3], [10, 11, 12])

    def test_scalar_from_a_variable_is_still_a_broadcast(self):
        target = analyze(GRID + "factor = 3\nC = A * factor")["target"]
        self.assertEqual(target["mode"], "scalar")
        self.assertEqual(target["operand"], 3)

    def test_elementwise_arrays(self):
        model = analyze(HEADER + "A = np.ones((2, 2))\nB = np.full((2, 2), 4)\nC = A + B")
        target = model["target"]
        self.assertEqual(target["mode"], "array")
        self.assertEqual((target["a"], target["b"]), ("A", "B"))
        self.assertEqual(target["result"], [[5, 5], [5, 5]])

    def test_last_animatable_statement_wins(self):
        target = analyze(GRID + "C = A[0:2, 0:2]\nD = A + 3")["target"]
        self.assertEqual(target["mode"], "scalar")
        self.assertEqual(target["out"], "D")

    def test_bare_expression_without_assignment(self):
        target = analyze(GRID + "A[1:3, 1:3]")["target"]
        self.assertEqual(target["mode"], "slice")
        self.assertEqual(target["out"], "result")

    def test_chained_results_are_reusable(self):
        model = analyze(GRID + "B = A + 1\nC = B[0:2, 0:2]")
        self.assertEqual(model["target"]["a"], "B")
        self.assertEqual(model["target"]["result"], [[1, 2], [7, 8]])

    def test_statements_after_the_target_do_not_change_the_frame(self):
        # A is rebound afterwards; the drawing must show A as it was.
        model = analyze(GRID + "C = A[0:2, 0:2]\nA = np.zeros((6, 6))")
        self.assertEqual(model["arrays"]["A"][0][:3], [0, 1, 2])


class RealNumpyTests(unittest.TestCase):
    def test_seed_is_reproducible_and_matches_numpy(self):
        import numpy as np
        code = HEADER + "np.random.seed(12345)\nA = np.random.randint(0, 100, (8, 8))\nC = A[1:5, 1:5]"
        model = analyze(code)
        np.random.seed(12345)
        self.assertEqual(model["arrays"]["A"], np.random.randint(0, 100, (8, 8)).tolist())

    def test_true_division_produces_floats(self):
        target = analyze(HEADER + "A = np.full((1, 2), 10)\nC = A / 4")["target"]
        self.assertEqual(target["result"], [[2.5, 2.5]])

    def test_float_values_are_rounded_not_noisy(self):
        target = analyze(HEADER + "A = np.array([[0.1, 0.2]])\nC = A + 0.2")["target"]
        self.assertEqual(target["result"], [[0.3, 0.4]])

    def test_boolean_dtype_is_drawable_as_zero_one(self):
        model = analyze(HEADER + "A = np.eye(3, dtype=bool)\nC = A[0:2, 0:2]")
        self.assertEqual(model["arrays"]["A"][0], [1, 0, 0])

    def test_other_arrays_are_listed_for_the_sidebar(self):
        model = analyze(HEADER + "A = np.ones((2, 2))\nZ = np.zeros((3, 3))\nC = A + 1")
        self.assertIn("Z", model["arrays"])
        self.assertEqual(model["oneD"]["Z"], False)

    def test_oversized_extra_arrays_are_skipped_not_fatal(self):
        model = analyze(HEADER + "A = np.ones((2, 2))\nBig = np.zeros((40, 40))\nC = A + 1")
        self.assertNotIn("Big", model["arrays"])
        self.assertIn("A", model["arrays"])


class ErrorTests(unittest.TestCase):
    def assert_error(self, code, needle):
        with self.assertRaises(ModelError) as ctx:
            analyze(code)
        self.assertIn(needle, str(ctx.exception).lower())

    def test_no_animatable_expression(self):
        self.assert_error(HEADER + "A = np.ones((2, 2))", "add an expression to animate")

    def test_undefined_name(self):
        self.assert_error(HEADER + "C = Z[1:2]", "not defined")

    def test_shape_mismatch(self):
        self.assert_error(HEADER + "A = np.ones((2, 2))\nB = np.ones((3, 3))\nC = A + B",
                          "shapes do not match")

    def test_too_big(self):
        self.assert_error(HEADER + "A = np.zeros((20, 20))\nC = A + 1", f"{MAX_DIM}×{MAX_DIM}")

    def test_long_1d_allowed_up_to_its_own_limit(self):
        analyze(HEADER + f"A = np.arange({MAX_1D})\nC = A[0:3]")  # must not raise
        self.assert_error(HEADER + f"A = np.arange({MAX_1D + 1})\nC = A[0:3]", "1-d arrays")

    def test_three_dimensional_array(self):
        self.assert_error(HEADER + "A = np.zeros((2, 2, 2))\nC = A + 1", "only 1-d and 2-d")

    def test_empty_slice(self):
        self.assert_error(GRID + "C = A[3:3, 0:2]", "selects nothing")

    def test_out_of_bounds_index(self):
        self.assert_error(GRID + "C = A[9]", "out of bounds")

    def test_slice_step(self):
        self.assert_error(GRID + "C = A[0:6:2, 0:2]", "steps")

    def test_too_many_indices_for_1d(self):
        self.assert_error(HEADER + "A = np.arange(4)\nC = A[0:2, 0:1]", "1-d")

    def test_mask_on_a_different_array(self):
        self.assert_error(HEADER + "A = np.ones((2, 2))\nB = np.ones((2, 2))\nC = A[B > 0]",
                          "same array")

    def test_scalar_result_is_not_drawable(self):
        self.assert_error(GRID + "C = A[2, 3]", "single number")

    def test_nan_cannot_be_drawn(self):
        # 0.0 / 0.0 is NaN in NumPy (a warning, not an exception), so the guard
        # in _to_grid is the only thing standing between it and invalid JSON.
        self.assert_error(HEADER + "A = np.array([[1.0, 0.0]])\nC = A / A", "nan or infinity")

    def test_division_by_zero_scalar(self):
        self.assert_error(GRID + "C = A / 0", "division by zero")

    def test_non_array_operand(self):
        self.assert_error(HEADER + "A = [[1, 2], [3, 4]]\nC = A[0:1]", "not a numpy array")

    def test_string_dtype(self):
        self.assert_error(HEADER + "A = np.array([['a', 'b']])\nC = A[0:1, 0:1]", "only numbers")

    def test_syntax_error(self):
        self.assert_error(HEADER + "C = A[1:\n", "syntaxerror")

    def test_code_too_long(self):
        self.assert_error(HEADER + "# pad\n" * 900 + "A = np.ones((2,2))\nC = A + 1", "too long")

    def test_empty_code(self):
        self.assert_error("", "write some numpy")


class SandboxTests(unittest.TestCase):
    """The safety layers must still apply on this endpoint (see CLAUDE.md)."""

    def assert_unsafe(self, code):
        with self.assertRaises(UnsafeCodeError):
            analyze(code)

    def test_disallowed_import(self):
        self.assert_unsafe("import os\nA = os\n")

    def test_open_is_denied(self):
        self.assert_unsafe(HEADER + "f = open('/etc/passwd')\n")

    def test_eval_is_denied(self):
        self.assert_unsafe(HEADER + "x = eval('1+1')\n")

    def test_dunder_access_is_denied(self):
        self.assert_unsafe(HEADER + "A = np.array([1]).__class__\n")

    def test_pandas_is_not_importable_here(self):
        self.assert_unsafe("import pandas as pd\n")

    def test_runaway_loop_hits_the_wall_clock(self):
        from backend.sandbox import ExecutionTimeout
        with self.assertRaises(ExecutionTimeout):
            analyze(HEADER + "A = np.ones((2, 2))\nwhile True:\n    pass\nC = A + 1")


class WsgiTests(unittest.TestCase):
    def call(self, method="POST", body=None, path=visualize_numpy.ROUTE_PATH):
        raw = json.dumps(body or {}).encode() if body is not None else b""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "CONTENT_LENGTH": str(len(raw)),
            "wsgi.input": BytesIO(raw),
        }
        captured = []
        chunks = visualize_numpy.app(environ, lambda status, headers: captured.append((status, headers)))
        return int(captured[0][0].split()[0]), json.loads(b"".join(chunks))

    def test_post_returns_the_model(self):
        status, payload = self.call(body={"code": GRID + "C = A[1:3, 2:4]"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["target"]["mode"], "slice")
        self.assertIn("A", payload["arrays"])

    def test_user_errors_are_400(self):
        status, payload = self.call(body={"code": HEADER + "A = np.ones((2, 2))"})
        self.assertEqual(status, 400)
        self.assertIn("add an expression", payload["error"])

    def test_unsafe_code_is_400(self):
        status, payload = self.call(body={"code": "import os"})
        self.assertEqual(status, 400)
        self.assertIn("not allowed", payload["error"])

    def test_missing_code_is_400(self):
        status, payload = self.call(body={})
        self.assertEqual(status, 400)

    def test_invalid_json_is_400(self):
        environ = {"REQUEST_METHOD": "POST", "PATH_INFO": visualize_numpy.ROUTE_PATH,
                   "CONTENT_LENGTH": "3", "wsgi.input": BytesIO(b"{[}")}
        captured = []
        chunks = visualize_numpy.app(environ, lambda s, h: captured.append((s, h)))
        self.assertTrue(captured[0][0].startswith("400"))
        self.assertIn("invalid JSON", json.loads(b"".join(chunks))["error"])

    def test_get_reports_usage(self):
        status, payload = self.call(method="GET", body=None)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_wrong_path_is_404(self):
        status, _ = self.call(body={"code": "x"}, path="/api/nope")
        self.assertEqual(status, 404)

    def test_wrong_method_is_405(self):
        status, _ = self.call(method="DELETE", body={})
        self.assertEqual(status, 405)


if __name__ == "__main__":
    unittest.main()
