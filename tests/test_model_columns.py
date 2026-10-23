"""The 009 columns are varchar, so their models must not declare native Postgres enums."""

from sqlalchemy import Enum
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models.purchase_event import PurchaseEvent
from app.models.user import User


def test_new_enum_columns_are_not_native_enums():
    for column in (User.__table__.c.role, PurchaseEvent.__table__.c.action):
        assert isinstance(column.type, Enum) and column.type.native_enum is False, column


def test_postgres_ddl_for_the_new_tables_has_no_enum_types():
    for table in (User.__table__, PurchaseEvent.__table__):
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect())).lower()
        assert "userrole" not in ddl and "purchaseeventaction" not in ddl
