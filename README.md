# Shopping Sessions - C++ baseline

Initial implementation for the Shopping Sessions data engineering hackathon.
The C++20 executable performs strict CSV and timestamp parsing, transitive identity
resolution, earliest-ingestion event deduplication, sessionization, revenue/category
aggregation, last-click attribution, and campaign assistance counting.

Python is limited to reading the manifest, launching the native executable, and
converting final aggregates to Parquet with PyArrow. Raw events and clicks are
processed in C++. C++ is suitable for this problem; this small serialization adapter
avoids making a native Arrow build a prerequisite for initial development.

## Status and limits

This is a development baseline, **not a submission-ready or benchmark-winning solution**.

- Native build verified on Windows with GCC 16.2, C++20, `-O2`, and warnings enabled.
- All 8 native test groups pass, including exact aggregate comparisons on 75 seeded
  randomized datasets. Local test-suite wall time: 11.532 seconds (includes process
  startup and temporary-file creation; not a throughput benchmark).
- The provided 36 fixtures, generator, and public development answer were not
  available as local bytes during this implementation; no claim of passing them.
- Docker packaging, Parquet serialization tests, and a restricted-container smoke test
  are included in CI. They were not run locally: the Docker Linux daemon was unavailable
  and PyArrow was not installed. Direct network downloads were unavailable.
- The native engine is currently single-threaded and in memory. `NCPU` and `MEM_GB`
  are read and validated but do not yet control workers, spilling, or memory allocation.
  Large-data memory safety against the 24 GB budget is unverified. Peak memory and
  scoring-scale throughput have not been measured.
- Docker build time, image size, and total 50 GB writable-disk compliance remain
  unmeasured. Do not submit until full official validation and resource benchmarks pass.

## Build and test

Linux (GCC 12 or newer with C++20 calendar support):

```sh
g++ -std=c++20 -O2 -Wall -Wextra -Wpedantic src/main.cpp -o sessions-core
SESSIONS_CORE=./sessions-core python3 tests/test_core.py
python3 -m pip install pyarrow==19.0.1
python3 tests/test_parquet.py
```

Windows PowerShell with MinGW GCC:

```powershell
g++ -std=c++20 -O2 -Wall -Wextra -Wpedantic src/main.cpp -o sessions-core.exe
python tests/test_core.py
```

CMake is also supported: `cmake -S . -B build -DCMAKE_BUILD_TYPE=Release`, then
`cmake --build build --config Release`. Set `SESSIONS_CORE` to the built executable
when running tests or the Python adapter.

## Docker execution

```sh
docker build --platform linux/amd64 -t shopping-sessions .
mkdir -p output scratch
# The mounts must be writable by UID 1000.
docker run --rm --network=none --read-only --user 1000:1000 \
  --cpus=8 --memory=24g --memory-swap=24g --pids-limit=4096 \
  --tmpfs /tmp:rw,size=64m \
  -v "$PWD/data:/data:ro" -v "$PWD/output:/output" -v "$PWD/scratch:/scratch" \
  shopping-sessions
python3 tests/test_container.py
```

`DATA_DIR`, `OUTPUT_DIR`, and `SCRATCH_DIR` override the default paths. The output
table directories must be empty to avoid accidentally mixing results from different
runs. Temporary aggregate CSVs live in a unique directory under scratch and are
removed after conversion. Final output is exactly one Parquet file in each table
folder, including empty tables, with DATE days, int64 counters, and nullable
`top_category`. No runtime downloads are performed.

## Implementation notes

- CSV parsing preserves carriage returns, spaces, case, and UTF-8 bytes.
- Invalid earliest event copies remain deduplication tombstones.
- Union-find uses path compression and union by size, retaining the byte-wise
  smallest linked ID, including IDs absent from events and clicks.
- Events and clicks are sorted by person and timestamp. Session and attribution
  scans are linear after sorting.
- Assists use per-campaign unions of session-index intervals with monotone endpoints
  and day boundaries. Repeated clicks do not generate repeated session counts or a
  click/session Cartesian product.
- Intermediate CSVs include an explicit purchase marker to distinguish a null top
  category from an actual empty category string. It is removed from final output.

## Next development milestones

1. Import and run all official fixtures and exact development-answer comparisons.
2. Verify Docker/Parquet CI and collect repeatable runtime, peak RSS, scratch usage,
   and output-size measurements on representative datasets.
3. Intern repeated strings into numeric dictionaries and introduce bounded partitions
   or external sorting before attempting full-scale input.
4. Add parallel parsing and independent-person processing governed by `NCPU`.
5. Profile before replacing the Parquet adapter or tuning hashes, sorts, and I/O.

The tests use a separate brute-force temporal reference for randomized comparisons.
They do not replace the competition's official fixtures or scorer.
