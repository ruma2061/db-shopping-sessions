"""Run with PyArrow installed to verify actual serialized schemas and nulls."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import pyarrow as pa
import pyarrow.parquet as pq

spec = importlib.util.spec_from_file_location("runner", Path(__file__).resolve().parents[1] / "scripts/run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class ParquetTests(unittest.TestCase):
    def test_types_null_and_empty_category(self):
        schema = pa.schema([("user_id", pa.string()), ("day", pa.date32()), ("sessions", pa.int64()),
                            ("events", pa.int64()), ("revenue_cents", pa.int64()), ("top_category", pa.string())])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            source = path / "in.csv"
            header = b"user_id,day,sessions,events,revenue_cents,top_category,has_purchase\n"
            source.write_bytes(header + b'"u",20727,1,1,0,"",0\n"v",20727,1,1,5,"",1\n')
            runner.write_table(source, path / "out", schema, True)
            result = pq.read_table(path / "out")
            self.assertEqual(result.schema, schema)
            self.assertEqual(result["top_category"].to_pylist(), [None, ""])
            source.write_bytes(header)
            runner.write_table(source, path / "empty", schema, True)
            result = pq.read_table(path / "empty")
            self.assertEqual(result.num_rows, 0)
            self.assertEqual(result.schema, schema)


if __name__ == "__main__":
    unittest.main(verbosity=2)
