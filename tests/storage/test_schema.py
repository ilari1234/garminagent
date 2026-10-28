"""Schema guards that need no database."""

from garminagent.storage.models import Base

NORMALIZED_TABLES = (
    "activity",
    "daily_summary",
    "activity_sample",
    "activity_lap",
    "sleep_summary",
    "hrv_summary",
    "weight_daily",
)
# Data minimization: location and free text stay in the raw layer only.
FORBIDDEN = ("lat", "lon", "location", "name", "description", "comment", "note")


def test_normalized_tables_have_no_location_or_free_text_columns() -> None:
    for table in NORMALIZED_TABLES:
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
