"""Unit tests for execution/masks.py's `child = parent[parent <cmp> constant]` detector.

Only this exact shape should match — anything else (a slice, a variable threshold,
a mask against a different array, a multi-condition mask) must yield no filter, so
composer.py's cell-mask highlighting stays off by default rather than guessing.
"""
import os
import sys
import unittest

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import numpy as np  # noqa: E402
from execution.masks import ArrayMaskFilter, apply_cmp, find_array_mask_filters  # noqa: E402


class TestFindArrayMaskFilters(unittest.TestCase):
    def test_matches_simple_mask_filter(self):
        filters = find_array_mask_filters("import numpy as np\nA = np.array([1])\nC = A[A > 50]\n")
        self.assertEqual(filters, [ArrayMaskFilter(child_name="C", parent_name="A", cmp=">", thresh=50)])

    def test_matches_all_six_comparisons(self):
        for cmp in (">", "<", ">=", "<=", "==", "!="):
            filters = find_array_mask_filters(f"A = x\nC = A[A {cmp} 3]\n")
            self.assertEqual(filters[0].cmp, cmp)

    def test_ignores_a_plain_slice(self):
        self.assertEqual(find_array_mask_filters("A = x\nC = A[3:5, 3:5]\n"), [])

    def test_ignores_mask_against_a_different_array(self):
        self.assertEqual(find_array_mask_filters("A = x\nB = y\nC = A[B > 50]\n"), [])

    def test_ignores_variable_threshold(self):
        self.assertEqual(find_array_mask_filters("A = x\nt = 50\nC = A[A > t]\n"), [])

    def test_ignores_multi_target_assignment(self):
        self.assertEqual(find_array_mask_filters("A = x\nC = D = A[A > 50]\n"), [])

    def test_ignores_syntax_error(self):
        self.assertEqual(find_array_mask_filters("def (:\n"), [])

    def test_finds_multiple_filters_in_one_snippet(self):
        src = "A = x\nB = y\nC = A[A > 50]\nD = B[B < 10]\n"
        filters = find_array_mask_filters(src)
        self.assertEqual(len(filters), 2)
        self.assertEqual({f.parent_name for f in filters}, {"A", "B"})


class TestApplyCmp(unittest.TestCase):
    def test_apply_cmp_matches_python_semantics(self):
        arr = np.array([1, 2, 3])
        for cmp, expected in ((">", [False, False, True]), ("<=", [True, True, False])):
            self.assertEqual([bool(v) for v in apply_cmp(arr, cmp, 2)], expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
