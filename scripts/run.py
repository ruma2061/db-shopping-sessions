"""Launch native processing; stream final aggregates into typed Parquet batches."""
import csv
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq


def write_table(source, target, schema, user_table=False):
    target.mkdir(parents=True, exist_ok=True)
    epoch = dt.date(1970, 1, 1)
    with source.open(encoding="utf-8", newline="") as stream:
        with pq.ParquetWriter(target / "part-0.parquet", schema, compression="snappy") as writer:
            batch = []
            for row in csv.DictReader(stream):
                if user_table:
                    purchased = row.pop("has_purchase") == "1"
                    if not purchased:
                        row["top_category"] = None
                for field in schema:
                    if pa.types.is_integer(field.type):
                        row[field.name] = int(row[field.name])
                row["day"] = epoch + dt.timedelta(days=int(row["day"]))
                batch.append(row)
                if len(batch) == 65536:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                    batch.clear()
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))


def main():
    data = Path(os.environ.get("DATA_DIR", "/data"))
    output = Path(os.environ.get("OUTPUT_DIR", "/output"))
    scratch = Path(os.environ.get("SCRATCH_DIR", "/scratch"))
    # Reserved for parallel/spilling implementation; validate requested budgets now.
    if int(os.environ.get("NCPU", "8")) < 1 or float(os.environ.get("MEM_GB", "24")) <= 0:
        raise ValueError("NCPU and MEM_GB must be positive")
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    scratch.mkdir(parents=True, exist_ok=True)
    for table in ("user_daily", "campaign_daily"):
        path = output / table
        if path.exists() and any(path.iterdir()):
            raise ValueError(f"Output directory must be empty: {path}")
    user_schema = pa.schema([
        ("user_id", pa.string()), ("day", pa.date32()), ("sessions", pa.int64()),
        ("events", pa.int64()), ("revenue_cents", pa.int64()), ("top_category", pa.string()),
    ])
    campaign_schema = pa.schema([
        ("campaign_id", pa.string()), ("channel", pa.string()), ("day", pa.date32()),
        ("sessions", pa.int64()), ("purchasing_sessions", pa.int64()),
        ("revenue_cents", pa.int64()), ("assisted_sessions", pa.int64()),
    ])
    with tempfile.TemporaryDirectory(prefix="sessions-", dir=scratch) as tmp:
        subprocess.run([
            os.environ.get("SESSIONS_CORE", "/app/sessions-core"), str(data), tmp,
            manifest["window_start"], manifest["window_end"],
        ], check=True)
        write_table(Path(tmp) / "user_daily.csv", output / "user_daily", user_schema, True)
        write_table(Path(tmp) / "campaign_daily.csv", output / "campaign_daily", campaign_schema)


if __name__ == "__main__":
    main()
