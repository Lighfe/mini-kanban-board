from tests.conftest import is_disposable_database


def test_in_memory_sqlite_is_disposable():
    assert is_disposable_database("sqlite:///:memory:")


def test_database_named_for_tests_is_disposable():
    assert is_disposable_database("postgresql+psycopg://kanban:kanban@localhost:5432/kanban_test")


def test_app_database_is_not_disposable():
    assert not is_disposable_database("postgresql+psycopg://kanban:kanban@localhost:5432/kanban")
    assert not is_disposable_database("sqlite:///kanban.db")


def test_postgres_without_database_name_is_not_disposable():
    # Postgres defaults the database name to the user name ("kanban").
    assert not is_disposable_database("postgresql+psycopg://kanban:kanban@localhost:5432")
