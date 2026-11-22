FROM gcc:14-bookworm AS build
WORKDIR /build
COPY src/main.cpp .
# Link GCC's C++ and exception runtimes into the executable; glibc remains dynamic.
RUN g++ -std=c++20 -O3 -DNDEBUG -Wall -Wextra -Wpedantic -static-libstdc++ -static-libgcc main.cpp -o sessions-core

FROM python:3.12-slim-bookworm
RUN pip install --no-cache-dir pyarrow==19.0.1
WORKDIR /app
COPY --from=build /build/sessions-core /app/sessions-core
COPY scripts/run.py /app/run.py
ENV DATA_DIR=/data OUTPUT_DIR=/output SCRATCH_DIR=/scratch NCPU=8 MEM_GB=24 PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
# Fail image construction if the executable cannot load in the final runtime.
RUN python -c "import subprocess; p = subprocess.run(['/app/sessions-core'], capture_output=True, text=True); assert p.returncode == 1 and 'error: Usage: sessions-core' in p.stderr, p.stderr"
ENTRYPOINT ["python", "/app/run.py"]
