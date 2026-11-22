"""Small end-to-end test with the scorer's filesystem/security/resource settings."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import pyarrow as pa
import pyarrow.parquet as pq

# Defaults match scoring; overrides are for small local smoke tests only.
cpus = int(os.environ.get("TEST_CPUS", "8"))
memory_gb = int(os.environ.get("TEST_MEM_GB", "24"))
if cpus < 1 or memory_gb < 1:
    raise ValueError("TEST_CPUS and TEST_MEM_GB must be positive integers")
print(f"Container smoke test: {cpus} CPUs, {memory_gb} GiB RAM", flush=True)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    data, output, scratch = (root / name for name in ("data", "output", "scratch"))
    for path in (data, output, scratch):
        path.mkdir()
    # Permit the container's numeric UID to traverse the temporary parent and write mounts.
    os.chmod(root, 0o755)
    os.chmod(output, 0o777)
    os.chmod(scratch, 0o777)
    # Host-owned table folders permit cleanup when the host UID differs from 1000.
    for table in ("user_daily", "campaign_daily"):
        (output / table).mkdir()
        os.chmod(output / table, 0o777)
    for name, text in {
        "manifest.json": json.dumps({"window_start": "2026-10-01T00:00:00Z", "window_end": "2026-10-15T00:00:00Z"}),
        "products.csv": "product_id,category\np,books\n",
        "campaigns.csv": "campaign_id,channel\nc,email\n",
        "events/part.csv": "event_id,user_id,ts,ingest_ts,event_type,product_id,price_cents\ne,u,2026-10-01T00:00:00Z,2026-10-01T00:00:00Z,purchase,p,250\n",
        "clicks/part.csv": "click_id,user_id,campaign_id,ts\nc,u,c,2026-10-01T00:00:00Z\n",
        "links/part.csv": "user_id_a,user_id_b\nu,A\n",
    }.items():
        path = data / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
    subprocess.run([
        "docker", "run", "--rm", "--network=none", "--read-only", "--user", "1000:1000",
        f"--cpus={cpus}", f"--memory={memory_gb}g", f"--memory-swap={memory_gb}g", "--pids-limit=4096",
        "-e", f"NCPU={cpus}", "-e", f"MEM_GB={memory_gb}",
        "--tmpfs", "/tmp:rw,size=64m", "-v", f"{data}:/data:ro",
        "-v", f"{output}:/output", "-v", f"{scratch}:/scratch", "shopping-sessions",
    ], check=True)
    users = pq.read_table(output / "user_daily")
    campaigns = pq.read_table(output / "campaign_daily")
    assert users.schema.field("day").type == pa.date32()
    assert campaigns.schema.field("day").type == pa.date32()
    assert users.num_rows == campaigns.num_rows == 1
    assert users["user_id"].to_pylist() == ["A"]
    assert users["top_category"].to_pylist() == ["books"]
    assert users["revenue_cents"].to_pylist() == [250]
    assert campaigns["campaign_id"].to_pylist() == ["c"]
    assert campaigns["sessions"].to_pylist() == [1]
    assert campaigns["assisted_sessions"].to_pylist() == [1]
    assert not list(scratch.iterdir())
    print("Restricted container smoke test passed")
