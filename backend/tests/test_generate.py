"""Unit tests for the plain-Python visualizer (tracer.py / loops.py).

Focus areas, in order of how much they've historically gone wrong:

  * fix_loop_headers() — the "which list element is current" logic. Two real
    bugs lived here: (a) duplicate elements resolved to the first occurrence,
    (b) nested loops over one iterable collided on the loop_indices key. Both
    are pinned by regression tests below.
  * PythonTracer.trace() — step capture, stdout, final-step contract, error/limit
  * find_for_loops() — exactly which `for` shapes are (and aren't) tracked
  * check_safe()  — the AST sandbox denylist/allowlist

These now exercise the object API (Step/Loop dataclasses, PythonTracer,
loops.current_index) directly rather than the old dict-shaped return values.

No third-party test runner required:  python -m unittest discover backend/tests
(also discoverable by pytest, since these are unittest.TestCase classes).
"""
import io
import os
import sys
import unittest

# The backend modules fall back to top-level imports (`from render.theme import ...`)
# on the serverless runtime, so the backend dir must be importable as a flat path.
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import generate  # noqa: E402  (thin entrypoint: ALLOWED_IMPORTS + build_frames re-export)
from runtime.sandbox import check_safe, UnsafeCodeError, MAX_STEPS  # noqa: E402
from execution.tracer import PythonTracer  # noqa: E402
from execution.loops import find_for_loops, fix_loop_headers, active_loop, current_index  # noqa: E402


def trace(src):
    """Trace `src` with this endpoint's import policy (mirrors the old generate.trace)."""
    return PythonTracer(generate.ALLOWED_IMPORTS).trace(src)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def steps_for(src):
    """Run the real pipeline: trace -> fix_loop_headers, as build_frames does."""
    loops = find_for_loops(src)
    return fix_loop_headers(trace(src), loops), loops


def body_steps(src):
    """Non-final steps only (drop the synthetic 'finished' frame)."""
    steps, loops = steps_for(src)
    return [s for s in steps if not s.is_final], loops


def resolve_current_idx(step, loops):
    """Mirror the composer's "current element" resolution.

    Returns (iterable_name, index) for the step's active loop, or (None, None)
    when no loop is active. The index itself is loops.current_index — the same
    function the LoopListPanel is fed — so this helper can no longer drift from
    what the user actually sees highlighted.
    """
    loop = active_loop(step.line, loops)
    if loop is None:
        return None, None
    return loop.iterable_name, current_index(step, loop)


# --------------------------------------------------------------------------
# find_for_loops — the tracked-loop gate
# --------------------------------------------------------------------------
class TestFindForLoops(unittest.TestCase):
    def test_simple_name_over_name(self):
        loops = find_for_loops("xs = [1, 2]\nfor x in xs:\n    print(x)\n")
        self.assertEqual(len(loops), 1)
        loop = loops[0]
        self.assertEqual(loop.loop_var, "x")
        self.assertEqual(loop.iterable_name, "xs")
        self.assertEqual(loop.header, 2)
        self.assertEqual(loop.start, 2)
        self.assertEqual(loop.end, 3)  # last body line

    def test_range_is_not_tracked(self):
        # iter is a Call, not a Name -> excluded (no list to draw progress for)
        self.assertEqual(find_for_loops("for i in range(3):\n    print(i)\n"), [])

    def test_list_literal_is_not_tracked(self):
        # iter is a List literal, not a Name
        self.assertEqual(find_for_loops("for x in [1, 2, 3]:\n    print(x)\n"), [])

    def test_tuple_target_is_not_tracked(self):
        # target is a Tuple (and iter a Call) -> excluded
        src = "d = {'a': 1}\nfor k, v in d.items():\n    print(k)\n"
        self.assertEqual(find_for_loops(src), [])

    def test_nested_loops_both_found_with_end_lines(self):
        src = ("xs = [1]\nys = [2]\n"
               "for a in xs:\n"
               "    for b in ys:\n"
               "        print(a, b)\n")
        loops = find_for_loops(src)
        self.assertEqual(len(loops), 2)
        by_target = {loop.loop_var: loop for loop in loops}
        self.assertEqual((by_target["a"].start, by_target["a"].end), (3, 5))
        self.assertEqual((by_target["b"].start, by_target["b"].end), (4, 5))

    def test_syntax_error_returns_empty(self):
        self.assertEqual(find_for_loops("for x in :\n"), [])


# --------------------------------------------------------------------------
# fix_loop_headers — current-index correctness (the headline bug area)
# --------------------------------------------------------------------------
class TestLoopCurrentIndex(unittest.TestCase):
    def test_unique_elements_track_position(self):
        src = ("xs = [10, 20, 30]\n"
               "for x in xs:\n"
               "    print(x)\n")
        steps, loops = body_steps(src)
        seen = [resolve_current_idx(s, loops) for s in steps if active_loop(s.line, loops)]
        # header+body pairs for iterations 0,1,2 then the terminating header (==len)
        idxs = [idx for name, idx in seen]
        self.assertEqual(idxs, [0, 0, 1, 1, 2, 2, 3])

    def test_duplicate_elements_regression(self):
        """The reported bug: 3rd 'cat' must highlight index 2, not index 0."""
        src = ("words = ['cat', 'dog', 'cat', 'bird', 'dog', 'cat']\n"
               "counts = {}\n"
               "\n"
               "for w in words:\n"
               "    counts[w] = counts.get(w, 0) + 1\n"
               "print(counts)\n")
        steps, loops = body_steps(src)
        # Every step that runs the body line (line 5) must resolve to the true
        # positional index of that iteration, regardless of repeated values.
        body_line_idxs = [resolve_current_idx(s, loops)[1] for s in steps if s.line == 5]
        self.assertEqual(body_line_idxs, [0, 1, 2, 3, 4, 5])
        # And no body step should ever collapse onto index 0 by value.
        for s in steps:
            if s.line == 5:
                self.assertIn("words", s.loop_indices)

    def test_terminating_pass_marks_all_done(self):
        src = ("xs = [1, 2]\n"
               "for x in xs:\n"
               "    print(x)\n")
        steps, loops = body_steps(src)
        header_idxs = [s.loop_indices["xs"] for s in steps if s.line == 2]
        # two real iterations then the exhausted pass at len(xs) == 2
        self.assertEqual(header_idxs, [0, 1, 2])

    def test_header_var_matches_iteration_it_initiates(self):
        """The one-iteration-lag fix: header snapshot shows the upcoming value."""
        src = ("fruits = ['apple', 'banana']\n"
               "for f in fruits:\n"
               "    print(f)\n")
        steps, loops = body_steps(src)
        header_vals = [s.variables.get("f") for s in steps if s.line == 2]
        # header#1 -> 'apple', header#2 -> 'banana', terminating -> stays 'banana'
        self.assertEqual(header_vals, ["apple", "banana", "banana"])

    def test_loop_var_rebound_in_body_still_positional(self):
        """Old .index() fallback ValueError'd here; positional index must hold."""
        src = ("xs = [1, 2, 3]\n"
               "for x in xs:\n"
               "    x = x * 100\n"     # rebinds loop var to a value not in xs
               "    print(x)\n")
        steps, loops = body_steps(src)
        body_idxs = [resolve_current_idx(s, loops)[1] for s in steps if s.line in (3, 4)]
        # two body lines per iteration, three iterations
        self.assertEqual(body_idxs, [0, 0, 1, 1, 2, 2])

    def test_tuple_iterable_with_duplicates(self):
        src = ("xs = (5, 5, 5)\n"
               "for x in xs:\n"
               "    print(x)\n")
        steps, loops = body_steps(src)
        body_idxs = [resolve_current_idx(s, loops)[1] for s in steps if s.line == 3]
        self.assertEqual(body_idxs, [0, 1, 2])

    def test_nested_loops_distinct_iterables_reset(self):
        src = ("xs = [1, 2]\n"
               "ys = [7, 8]\n"
               "for a in xs:\n"
               "    for b in ys:\n"
               "        print(a, b)\n")
        steps, loops = body_steps(src)
        # On the innermost body line, the active loop is ys; its index should
        # cycle 0,1 within each outer iteration (i.e. reset, not run 0..3).
        inner = [s.loop_indices["ys"] for s in steps if s.line == 5]
        self.assertEqual(inner, [0, 1, 0, 1])
        # Outer index advances 0 then 1 across the two passes.
        outer_headers = [s.loop_indices["xs"] for s in steps if s.line == 3]
        self.assertEqual(outer_headers, [0, 1, 2])  # +terminating

    def test_nested_loops_same_iterable_innermost_wins(self):
        """Regression: outer body-stamp must not clobber the inner loop's index."""
        src = ("xs = [1, 2]\n"
               "for a in xs:\n"
               "    for b in xs:\n"
               "        print(a, b)\n")
        steps, loops = body_steps(src)
        inner_loop = min(loops, key=lambda loop: loop.span)
        self.assertEqual(inner_loop.loop_var, "b")
        # On the innermost body line the highlighted index tracks b's position,
        # cycling 0,1 per outer pass — never stuck on a's position.
        inner = [resolve_current_idx(s, loops)[1] for s in steps if s.line == 4]
        self.assertEqual(inner, [0, 1, 0, 1])
        # On the inner loop's terminating header, everything is done: index==len.
        inner_headers = [s.loop_indices["xs"] for s in steps if s.line == 3]
        self.assertEqual(inner_headers[-1], 2)  # exhausted -> len(xs)

    def test_empty_iterable_does_not_crash(self):
        src = ("xs = []\n"
               "for x in xs:\n"
               "    print(x)\n"
               "print('done')\n")
        steps, loops = body_steps(src)
        # header fires once (immediately exhausted); no body steps
        self.assertFalse(any(s.line == 3 for s in steps))
        self.assertTrue(any(s.line == 2 for s in steps))

    def test_no_loops_leaves_steps_untouched(self):
        src = "a = 1\nb = a + 1\n"
        steps, loops = body_steps(src)
        self.assertEqual(loops, [])
        self.assertFalse(any(s.loop_indices for s in steps))

    def test_range_loop_gets_no_loop_idx(self):
        src = "for i in range(3):\n    print(i)\n"
        steps, loops = body_steps(src)
        self.assertEqual(loops, [])
        self.assertFalse(any(s.loop_indices for s in steps))


# --------------------------------------------------------------------------
# trace — step capture, stdout, final-step contract, error/limit paths
# --------------------------------------------------------------------------
class TestTrace(unittest.TestCase):
    def test_final_step_contract_on_success(self):
        steps = trace("x = 1\ny = 2\n")
        last = steps[-1]
        self.assertTrue(last.is_final)
        self.assertIsNone(last.line)
        self.assertIsNone(last.error)
        self.assertEqual(last.variables.get("x"), 1)
        self.assertEqual(last.variables.get("y"), 2)

    def test_stdout_is_cumulative_and_snapshotted_before_line(self):
        steps = trace("print('a')\nprint('b')\n")
        # snapshot happens *before* each line runs: line 2's step sees only 'a'
        line2 = next(s for s in steps if s.line == 2)
        self.assertEqual(line2.stdout, "a\n")
        self.assertEqual(steps[-1].stdout, "a\nb\n")

    def test_runtime_error_captured_on_final_step(self):
        steps = trace("x = 1\ny = x / 0\n")
        self.assertTrue(steps[-1].is_final)
        self.assertIn("ZeroDivisionError", steps[-1].error)

    def test_step_limit_is_enforced(self):
        steps = trace("x = 0\nwhile True:\n    x = x + 1\n")
        non_final = [s for s in steps if not s.is_final]
        self.assertLessEqual(len(non_final), MAX_STEPS)
        self.assertIsNotNone(steps[-1].error)
        self.assertIn("step limit", steps[-1].error)

    def test_functions_and_modules_excluded_from_vars(self):
        src = ("import math\n"
               "def helper(n):\n"
               "    return n\n"
               "x = helper(3)\n"
               "y = math.floor(2.7)\n")
        final_vars = trace(src)[-1].variables
        self.assertNotIn("math", final_vars)    # module hidden
        self.assertNotIn("helper", final_vars)  # function hidden
        self.assertEqual(final_vars.get("x"), 3)
        self.assertEqual(final_vars.get("y"), 2)

    def test_uncopyable_value_falls_back_to_repr(self):
        # generators can't be deepcopy'd; the snapshot must not blow up
        steps = trace("g = (i for i in range(3))\nx = 1\n")
        self.assertTrue(steps[-1].is_final)
        self.assertIn("g", steps[-1].variables)


# --------------------------------------------------------------------------
# check_safe — the AST sandbox
# --------------------------------------------------------------------------
class TestCheckSafe(unittest.TestCase):
    def assertUnsafe(self, src):
        with self.assertRaises(UnsafeCodeError):
            check_safe(src, generate.ALLOWED_IMPORTS)

    def test_disallowed_import(self):
        self.assertUnsafe("import os\n")

    def test_disallowed_from_import(self):
        self.assertUnsafe("from os import path\n")

    def test_allowed_import_ok(self):
        check_safe("import math\nx = math.pi\n", generate.ALLOWED_IMPORTS)  # must not raise

    def test_allowed_from_import_ok(self):
        check_safe("from math import sqrt\nx = sqrt(4)\n", generate.ALLOWED_IMPORTS)  # must not raise

    def test_dunder_attribute_blocked(self):
        self.assertUnsafe("x = (1).__class__\n")

    def test_denied_builtins_blocked(self):
        for name in ("open", "eval", "exec", "getattr", "__import__", "globals"):
            with self.subTest(name=name):
                self.assertUnsafe(f"{name}\n")

    def test_pandas_and_numpy_stay_out_of_this_endpoint(self):
        # generate.py composes its allowlist from sandbox.STDLIB_IMPORTS; widening
        # that shared set must never hand this endpoint pandas/numpy.
        for name in ("pandas", "numpy", "pd", "np"):
            with self.subTest(name=name):
                self.assertNotIn(name, generate.ALLOWED_IMPORTS)
                self.assertUnsafe(f"import {name}\n")


    def test_async_blocked(self):
        self.assertUnsafe("async def f():\n    return 1\n")

    def test_syntax_error_is_unsafe(self):
        self.assertUnsafe("def f(:\n")


# --------------------------------------------------------------------------
# Import policy — each endpoint composes ALLOWED_IMPORTS from
# sandbox.STDLIB_IMPORTS, so pin the exact contents: this is security policy.
# --------------------------------------------------------------------------
class TestImportPolicyComposition(unittest.TestCase):
    STDLIB = {"math", "random", "string", "itertools", "functools", "collections",
              "datetime", "re", "json", "statistics", "decimal", "fractions"}

    def test_shared_base_set(self):
        from runtime.sandbox import STDLIB_IMPORTS
        self.assertEqual(set(STDLIB_IMPORTS), self.STDLIB)

    def test_plain_python_endpoint_is_stdlib_only(self):
        self.assertEqual(set(generate.ALLOWED_IMPORTS), self.STDLIB)

    def test_pandas_endpoint_adds_only_the_data_stack(self):
        import generate_pandas
        self.assertEqual(set(generate_pandas.ALLOWED_IMPORTS),
                         self.STDLIB | {"pandas", "numpy", "pd", "np"})

    def test_endpoints_do_not_share_a_mutable_set(self):
        # A stray mutation on one endpoint must not reach the other, nor the base.
        from runtime.sandbox import STDLIB_IMPORTS
        import generate_pandas
        self.assertIsNot(generate.ALLOWED_IMPORTS, generate_pandas.ALLOWED_IMPORTS)
        self.assertIsInstance(STDLIB_IMPORTS, frozenset)


# --------------------------------------------------------------------------
# build_frames — end-to-end render smoke tests (compose() must not crash on any
# step type: header/body/terminating/final/error, done/current/waiting rows)
# --------------------------------------------------------------------------
class TestRenderSmoke(unittest.TestCase):
    def _assert_renders(self, src):
        frames, durations = generate.build_frames(src, ms=300, code_size=22, scale=1.0)
        steps, _ = steps_for(src)
        self.assertEqual(len(frames), len(steps))
        self.assertEqual(len(durations), len(steps))
        for frame in frames:
            self.assertGreater(frame.width, 0)
            self.assertGreater(frame.height, 0)

    def test_duplicates_snippet_renders(self):
        self._assert_renders(
            "words = ['cat', 'dog', 'cat', 'bird']\n"
            "counts = {}\n"
            "for w in words:\n"
            "    counts[w] = counts.get(w, 0) + 1\n"
            "print(counts)\n")

    def test_nested_loops_render(self):
        self._assert_renders(
            "xs = [1, 2]\nys = [3, 4]\n"
            "for a in xs:\n"
            "    for b in ys:\n"
            "        print(a * b)\n")

    def test_no_loop_code_renders(self):
        self._assert_renders("a = 1\nb = a + 2\nprint(b)\n")

    def test_error_snippet_renders(self):
        # exercises the console panel's error branch
        self._assert_renders("x = 1\ny = x / 0\n")


class TestWsgiStatusCodes(unittest.TestCase):
    """The GIF endpoint's HTTP status contract.

    This exists to pin the 400 path in runtime/serverless.py, which is the one
    place an import mistake shows up as a *wrong status* rather than a crash:
    it catches UnsafeCodeError by class, so if sandbox.py is ever loaded under
    two module names in one process, the `except` misses and a rejected snippet
    silently becomes a 500. Cheap assertion, expensive bug.
    """

    def _post(self, module, path, body):
        captured = []
        module.app({"REQUEST_METHOD": "POST", "PATH_INFO": path,
                    "CONTENT_LENGTH": str(len(body)),
                    "wsgi.input": io.BytesIO(body)},
                   lambda status, headers: captured.append(status))
        return captured[0]

    def test_unsafe_code_is_400_not_500(self):
        status = self._post(generate, "/api/generate", b'{"code": "import os"}')
        self.assertTrue(status.startswith("400"), status)

    def test_unsafe_code_is_400_on_the_pandas_endpoint(self):
        import generate_pandas
        status = self._post(generate_pandas, "/api/generate-pandas",
                            b'{"code": "import socket"}')
        self.assertTrue(status.startswith("400"), status)

    def test_valid_code_is_200(self):
        status = self._post(generate, "/api/generate", b'{"code": "a = 1\\nb = a + 2\\n"}')
        self.assertTrue(status.startswith("200"), status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
