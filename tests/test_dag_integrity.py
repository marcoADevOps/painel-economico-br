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


def test_marts_is_scheduled_on_every_staging_asset(dag_bag):
    from painel.dag_support import STAGING_ANP, STAGING_BCB, STAGING_IBGE

    condition = dag_bag.dags["marts"].timetable.asset_condition
    expected = {STAGING_BCB.uri, STAGING_IBGE.uri, STAGING_ANP.uri}
    assert {asset.uri for asset in condition.objects} == expected


@pytest.mark.parametrize(
    ("dag_id", "asset_name"),
    [("bcb_sgs", "STAGING_BCB"), ("ibge_sidra", "STAGING_IBGE"), ("anp_precos", "STAGING_ANP")],
)
def test_source_dags_publish_their_staging_asset(dag_bag, dag_id, asset_name):
    import painel.dag_support as support

    publish = dag_bag.dags[dag_id].get_task("publish_staging")
    assert [o.uri for o in publish.outlets] == [getattr(support, asset_name).uri]


def test_every_mart_has_a_build_script():
    marts_module = pytest.importorskip("marts", reason="dags/ not on path")
    for name in marts_module.MARTS:
        assert (marts_module.SQL_DIR / f"{name}.sql").is_file(), name
    assert (marts_module.SQL_DIR / "ddl.sql").is_file()


@pytest.mark.parametrize("dag_id", ["bcb_sgs", "ibge_sidra", "anp_precos"])
def test_staging_is_published_only_after_quality_checks(dag_bag, dag_id):
    dag = dag_bag.dags[dag_id]
    assert dag.get_task("publish_staging").upstream_task_ids == {"quality_checks"}
    assert dag.get_task("quality_checks").upstream_task_ids >= {"raw_to_staging"}
