"""Every file in dags/ must import without errors (what the dag-processor does)."""

from pathlib import Path

import pytest

DAGS_DIR = Path(__file__).resolve().parents[1] / "dags"


@pytest.fixture(scope="session")
def dag_bag():
    from airflow.dag_processing.dagbag import DagBag

    # Airflow 3.3 removed include_examples; examples are off via LOAD_EXAMPLES.
    return DagBag(dag_folder=DAGS_DIR)


def test_no_import_errors(dag_bag):
    assert dag_bag.import_errors == {}


def test_dags_have_owner_and_tags(dag_bag):
    for dag_id, dag in dag_bag.dags.items():
        assert dag.tags, f"{dag_id} has no tags"
        assert dag.default_args.get("owner"), f"{dag_id} has no owner"
