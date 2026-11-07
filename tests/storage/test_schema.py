"""Schema guards that need no database."""

from sqlalchemy import CheckConstraint

from garminagent.storage.models import Base

RAW_TABLES = ("raw_payload", "activity_file")
OPERATIONAL_TABLES = ("athlete", "sync_cursor", "sync_run")
NORMALIZED_TABLES = (
    "activity",
    "daily_summary",
    "activity_sample",
    "activity_lap",
    "sleep_summary",
    "hrv_summary",
    "weight_daily",
    "athlete_zones",
)
DERIVED_TABLES = ("athlete_threshold",)
# Data minimization: location and free text stay in the raw layer only.
FORBIDDEN = ("lat", "lon", "location", "name", "description", "comment", "note")


def test_every_table_belongs_to_a_layer() -> None:
    # A new table must be placed here, so the guard below can't silently miss it.
    layers = RAW_TABLES + OPERATIONAL_TABLES + NORMALIZED_TABLES + DERIVED_TABLES
    assert sorted(layers) == sorted(Base.metadata.tables)


def test_normalized_and_derived_tables_have_no_location_or_free_text_columns() -> None:
    for table in NORMALIZED_TABLES + DERIVED_TABLES:
        columns = Base.metadata.tables[table].columns.keys()
        offending = [c for c in columns if any(word in c for word in FORBIDDEN)]
        assert not offending, f"{table} has columns {offending}"


def test_every_data_table_belongs_to_an_athlete_and_cascades() -> None:
    for name, table in Base.metadata.tables.items():
        if name == "athlete":
            continue
        fks = [fk for fk in table.foreign_keys if fk.column.table.name == "athlete"]
        assert fks, f"{name} has no athlete foreign key"
        assert all(fk.ondelete == "CASCADE" for fk in fks), f"{name} must cascade deletes from athlete"


def test_code_columns_are_constrained_to_codes() -> None:
    # Text columns in M6a tables hold codes only; a CHECK constraint keeps free text out.
    for table, columns in {
        "athlete_zones": {"sport", "training_method"},
        "athlete_threshold": {"value_type", "sport", "source"},
    }.items():
        checks = " ".join(
            str(c.sqltext) for c in Base.metadata.tables[table].constraints if isinstance(c, CheckConstraint)
        )
        for column in columns:
            assert f"{column} IN (" in checks, f"{table}.{column} has no code check"
