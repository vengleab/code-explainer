"""Unit tests for the pandas visualizer (tracer.py PandasTracer + composer.py).

The pandas path had no automated coverage before the refactor; these lock in the
pieces most likely to regress:

  * PandasTracer.trace()  — snapshots DataFrames/Series/1D-2D ndarrays/scalars,
    skips function bodies, and the NaN-diff behavior the DataFramePanel depends on.
  * PandasVisualizer.build_frames() — end-to-end render smoke (compose() must not
    crash on new-column, fillna/NaN, filter-subset, ndarray, and error snippets).
"""
import os
import sys
import unittest

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import generate_pandas  # noqa: E402
from execution.tracer import PandasTracer  # noqa: E402
from render.composer import as_frame  # noqa: E402


def trace(src):
    return PandasTracer(generate_pandas.ALLOWED_IMPORTS).trace(src)


FILLNA_SRC = (
    "import pandas as pd\n"
    "import numpy as np\n"
    "df = pd.DataFrame({'a': [1, np.nan, 3]})\n"
    "df['b'] = df['a'].fillna(0)\n"
)


class TestPandasTrace(unittest.TestCase):
    def test_final_step_has_dataframe_and_is_final(self):
        steps = trace("import pandas as pd\ndf = pd.DataFrame({'a': [1, 2]})\n")
        last = steps[-1]
        self.assertTrue(last.is_final)
        self.assertIsNone(last.line)
        self.assertIsNone(last.error)
        self.assertIsInstance(last.variables.get("df"), pd.DataFrame)

    def test_scalars_and_frames_both_captured(self):
        steps = trace("import pandas as pd\ndf = pd.DataFrame({'a': [1, 2]})\nn = df.shape[0]\n")
        final = steps[-1].variables
        self.assertIsInstance(final.get("df"), pd.DataFrame)
        self.assertEqual(final.get("n"), 2)  # scalar kept alongside the frame

    def test_snapshots_are_isolated_across_steps(self):
        # Each step copies the frame, so an in-place mutation on a later step
        # must not retroactively change an earlier step's snapshot.
        steps = trace("import pandas as pd\ndf = pd.DataFrame({'a': [1, 2]})\ndf['a'] = df['a'] * 10\n")
        frames = [s.variables["df"] for s in steps if "df" in s.variables]
        self.assertEqual(list(frames[0]["a"]), [1, 2])
        self.assertEqual(list(frames[-1]["a"]), [10, 20])

    def test_runtime_error_captured_on_final_step(self):
        steps = trace("import pandas as pd\ndf = pd.DataFrame({'a': [1]})\nx = df['missing']\n")
        self.assertTrue(steps[-1].is_final)
        self.assertIsNotNone(steps[-1].error)

    def test_as_frame_widens_series(self):
        series = pd.Series([1, 2, 3], name="s")
        self.assertIsInstance(as_frame(series), pd.DataFrame)
        self.assertIsNone(as_frame(42))

    def test_1d_ndarray_is_captured_and_widened(self):
        steps = trace("import numpy as np\na = np.array([1, 2, 3])\n")
        final = steps[-1].variables
        self.assertIsInstance(final.get("a"), np.ndarray)
        frame = as_frame(final["a"])
        self.assertIsInstance(frame, pd.DataFrame)
        self.assertEqual(list(frame["value"]), [1, 2, 3])

    def test_2d_ndarray_is_captured_and_widened(self):
        steps = trace("import numpy as np\nm = np.array([[1, 2], [3, 4]])\n")
        final = steps[-1].variables
        self.assertIsInstance(final.get("m"), np.ndarray)
        frame = as_frame(final["m"])
        self.assertIsInstance(frame, pd.DataFrame)
        self.assertEqual(frame.shape, (2, 2))

    def test_3d_ndarray_is_not_captured(self):
        # Only 1D/2D arrays render as a grid; higher-dim arrays are dropped
        # rather than falling into the scalar strip as a garbled multi-line repr.
        steps = trace("import numpy as np\nt = np.zeros((2, 2, 2))\n")
        self.assertNotIn("t", steps[-1].variables)

    def test_ndarray_snapshots_are_isolated_across_steps(self):
        steps = trace("import numpy as np\na = np.array([1, 2])\na = a * 10\n")
        arrays = [s.variables["a"] for s in steps if "a" in s.variables]
        self.assertEqual(list(arrays[0]), [1, 2])
        self.assertEqual(list(arrays[-1]), [10, 20])


class TestPandasRenderSmoke(unittest.TestCase):
    def _assert_renders(self, src):
        frames, durations = generate_pandas.build_frames(src, ms=300, code_size=11, scale=1.0)
        steps = trace(src)
        self.assertEqual(len(frames), len(steps))
        self.assertEqual(len(durations), len(steps))
        for frame in frames:
            self.assertGreater(frame.width, 0)
            self.assertGreater(frame.height, 0)

    def test_new_column_renders(self):
        self._assert_renders(
            "import pandas as pd\n"
            "df = pd.DataFrame({'a': [1, 2, 3], 'b': [4, 5, 6]})\n"
            "df['c'] = df['a'] + df['b']\n")

    def test_fillna_nan_diff_renders(self):
        # exercises the nan-vs-nan diff-highlight branch in DataFramePanel
        self._assert_renders(FILLNA_SRC)

    def test_filter_subset_renders(self):
        self._assert_renders(
            "import pandas as pd\n"
            "df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [5, 6, 7, 8]})\n"
            "big = df[df['x'] > 2]\n")

    def test_error_snippet_renders(self):
        self._assert_renders(
            "import pandas as pd\n"
            "df = pd.DataFrame({'a': [1]})\n"
            "x = df['missing']\n")

    def test_1d_ndarray_renders(self):
        self._assert_renders(
            "import numpy as np\n"
            "a = np.array([1, 2, 3])\n"
            "a = a * 2\n")

    def test_2d_ndarray_renders(self):
        self._assert_renders(
            "import numpy as np\n"
            "m = np.array([[1, 2], [3, 4]])\n"
            "m = m + 1\n")

    def test_dataframe_and_ndarray_together_render(self):
        self._assert_renders(
            "import pandas as pd\n"
            "import numpy as np\n"
            "df = pd.DataFrame({'a': [1, 2, 3]})\n"
            "arr = np.array([10, 20, 30])\n"
            "n = arr.sum()\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
