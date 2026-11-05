FROM python:3.12-slim

RUN pip install --no-cache-dir \
        pytest==9.2.0 \
        hypothesis==6.170.14 \
        numpy==2.5.4 \
        pandas==3.1.0 \
    && mkdir -p /opt/proveit /work

COPY *.py /opt/proveit/

# Hypothesis builds a unicode table the first time a test uses st.text()/st.characters()
# (~5 s at 0.5 CPU) and caches it on disk. The sandbox is read-only, so the cache could never
# be saved and every text test paid that cost again. Build it once here instead.
ENV HYPOTHESIS_STORAGE_DIRECTORY=/opt/proveit/hypothesis
RUN python -W ignore -c "from hypothesis import strategies as st; \
st.text().example(); st.characters().example(); st.characters(codec='utf-8').example(); \
st.characters(codec='ascii').example(); st.from_regex(r'\w+').example()" \
    && chmod -R a+rX /opt/proveit/hypothesis

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
