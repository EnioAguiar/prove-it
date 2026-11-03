FROM python:3.12-slim

RUN pip install --no-cache-dir \
        pytest==9.2.0 \
        hypothesis==6.170.14 \
        numpy==2.5.4 \
        pandas==3.1.0 \
    && mkdir -p /opt/proveit /work

COPY *.py /opt/proveit/

# One BLAS/OpenMP thread: otherwise numpy starts one per host CPU, and threads count
# against the sandbox's --pids-limit.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/work:/opt/proveit \
    PYTEST_ADDOPTS="-p no:cacheprovider -p proveit_plugin --rootdir=/work -c /dev/null --confcutdir=/work" \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

WORKDIR /work
USER 65534
