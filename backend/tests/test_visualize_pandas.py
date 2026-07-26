"""
Tests for pandas_model.analyze() — the data model behind the Pandas canvas visualizer page.
"""
import json
import os
import sys
import unittest
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.pandas_model import analyze, ModelError
from backend import visualize_pandas

HEADER = "import pandas as pd\nimport numpy as np\n"
INIT_DF = HEADER + "df = pd.DataFrame({'dept': ['eng', 'eng', 'sales'], 'salary': [90, 60, 50]})\n"


class PandasTargetDetectionTests(unittest.TestCase):
    def test_groupby_aggregation(self):
        model = analyze(INIT_DF + "summary = df.groupby('dept').mean()")
        self.assertEqual(model["target"]["mode"], "groupby")
        self.assertEqual(model["target"]["out"], "summary")
        self.assertIn("summary", model["dfs"])
        self.assertEqual(model["dfs"]["summary"]["columns"], ["salary"])

    def test_filter_mask(self):
        model = analyze(INIT_DF + "top = df[df['salary'] > 55]")
        self.assertEqual(model["target"]["mode"], "filter")
        self.assertEqual(model["target"]["out"], "top")
        self.assertEqual(len(model["dfs"]["top"]["data"]), 2)

    def test_new_column_assignment(self):
        model = analyze(INIT_DF + "df['bonus'] = df['salary'] * 0.1")
        self.assertEqual(model["target"]["mode"], "transform")
        self.assertIn("bonus", model["dfs"]["df"]["columns"])

    def test_sort_values(self):
        model = analyze(INIT_DF + "ranked = df.sort_values(by='salary', ascending=False)")
        self.assertEqual(model["target"]["mode"], "sort")
        self.assertEqual(model["target"]["out"], "ranked")
        self.assertEqual(model["dfs"]["ranked"]["data"][0][1], 90)

    def test_fillna(self):
        code = HEADER + "df = pd.DataFrame({'a': [1, np.nan, 3]})\nfilled = df.fillna(0)"
        model = analyze(code)
        self.assertEqual(model["target"]["mode"], "fillna")
        self.assertEqual(model["dfs"]["filled"]["data"][1][0], 0)


class PandasWsgiTests(unittest.TestCase):
    def call(self, method="POST", body=None, path=visualize_pandas.ROUTE_PATH):
        raw = json.dumps(body or {}).encode() if body is not None else b""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "CONTENT_LENGTH": str(len(raw)),
            "wsgi.input": BytesIO(raw),
        }
        captured = []
        chunks = visualize_pandas.app(environ, lambda status, headers: captured.append((status, headers)))
        return int(captured[0][0].split()[0]), json.loads(b"".join(chunks))

    def test_post_returns_pandas_model(self):
        status, payload = self.call(body={"code": INIT_DF + "top = df[df['salary'] > 55]"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["target"]["mode"], "filter")
        self.assertIn("df", payload["dfs"])

    def test_user_error_returns_400(self):
        status, payload = self.call(body={"code": "x = 1"})
        self.assertEqual(status, 400)
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
