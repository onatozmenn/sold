from sold.db import _SCHEMA_SQL, _split_sql
from sold.db.models import Base


def test_postgres_schema_splitter_and_orm_table_coverage():
    statements = _split_sql(_SCHEMA_SQL.read_text(encoding="utf-8"))

    assert statements[0].startswith("CREATE EXTENSION")
    assert all(not statement.lower().startswith("bkz.") for statement in statements)
    assert {
        "consumer_sales",
        "listing_outcomes",
        "realized_labels",
        "aggregate_observations",
    }.issubset(Base.metadata.tables)
    assert any("ADD COLUMN IF NOT EXISTS sale_mode" in statement for statement in statements)