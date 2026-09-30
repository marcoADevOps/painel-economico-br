ARG AIRFLOW_VERSION=3.3.2

# ---------------------------------------------------------------------------
# runtime: image deployed to the VM (DAGs, src and SQL baked in)
# ---------------------------------------------------------------------------
FROM apache/airflow:${AIRFLOW_VERSION} AS runtime
ARG AIRFLOW_VERSION

# Pin apache-airflow and use the official constraints file so extra packages
# never upgrade or downgrade Airflow itself.
COPY requirements.txt /tmp/requirements.txt
RUN PY_VERSION="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" \
    && pip install --no-cache-dir \
        "apache-airflow==${AIRFLOW_VERSION}" \
        -r /tmp/requirements.txt \
        --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PY_VERSION}.txt"

COPY --chown=airflow:root src/ /opt/airflow/src/
COPY --chown=airflow:root sql/ /opt/airflow/sql/
COPY --chown=airflow:root dags/ /opt/airflow/dags/
ENV PYTHONPATH=/opt/airflow/src

# ---------------------------------------------------------------------------
# test: runtime + lint/test tooling, used by CI
#   docker build -f docker/Dockerfile --target test -t painel-airflow:test .
#   docker run --rm painel-airflow:test python -m pytest
# ---------------------------------------------------------------------------
FROM runtime AS test
ARG AIRFLOW_VERSION

COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" -r /tmp/requirements-dev.txt

WORKDIR /opt/airflow/project
COPY --chown=airflow:root pyproject.toml ./
COPY --chown=airflow:root src/ src/
COPY --chown=airflow:root dags/ dags/
COPY --chown=airflow:root tests/ tests/
