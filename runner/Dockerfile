FROM python:3.12-slim

RUN pip install --no-cache-dir \
        pytest==9.2.0 \
        hypothesis==6.170.14 \
        numpy==2.5.4 \
        pandas==3.1.0 \
    && mkdir -p /opt/proveit /work

COPY *.py /opt/proveit/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/work:/opt/proveit \
    PYTEST_ADDOPTS="-p no:cacheprovider -p proveit_plugin --rootdir=/work -c /dev/null --confcutdir=/work"

WORKDIR /work
USER 65534
