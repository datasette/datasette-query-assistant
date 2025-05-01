from datasette.app import Datasette
from datasette_query_assistant import get_related_tables
from inline_snapshot import snapshot
import pytest_asyncio
import pytest
import sqlite_utils
import urllib

pytestmark = [pytest.mark.vcr(ignore_localhost=True)]


@pytest.fixture(scope="module")
def vcr_config():
    return {"filter_headers": ["x-api-key"]}


@pytest_asyncio.fixture
async def datasette():
    ds = Datasette()
    db = ds.add_memory_database("test")
    await db.execute_write(
        "create table if not exists foo (id integer primary key, name text)"
    )
    return ds


def test_get_related_tables():
    db = sqlite_utils.Database(memory=True)
    db["foo.bar.baz"].insert({"id": 1}, pk="id")
    db["species"].insert({"id": 1, "name": "Dog"}, pk="id")
    db["animals"].insert(
        {"id": 1, "name": "Cleo", "species": 1},
        pk="id",
        foreign_keys=(("species", "species", "id"),),
    )
    assert get_related_tables(db.conn, "foo.bar.baz") == set()
    assert get_related_tables(db.conn, "species") == {"species", "animals"}
    assert get_related_tables(db.conn, "animals") == {"species", "animals"}


@pytest.mark.asyncio
@pytest.mark.vcr()
async def test_database_assistant_page(datasette):
    response = await datasette.client.get("/test/-/assistant")
    assert response.status_code == 200
    assert "Query assistant for test" in response.text
    assert (
        "<pre>CREATE TABLE foo (id integer primary key, name text)</pre>"
        in response.text
    )
    # Submit the form
    csrftoken = response.cookies["ds_csrftoken"]
    post_response = await datasette.client.post(
        "/test/-/assistant",
        cookies={
            "ds_csrftoken": csrftoken,
        },
        data={
            "question": "Show me all the data in the foo table",
            "csrftoken": csrftoken,
        },
    )
    assert post_response.status_code == 302
    qs = dict(urllib.parse.parse_qsl(post_response.headers["location"].split("?")[1]))
    assert qs["sql"] == snapshot(
        """\
-- SQL query to select all columns and all rows from the table 'foo'
SELECT * FROM foo;\
"""
    )


@pytest.mark.asyncio
async def test_table_assistant_page(datasette):
    response = await datasette.client.get("/test/-/assistant?table=foo")
    assert response.status_code == 200
    assert "Query assistant for foo" in response.text
    assert (
        "<pre>CREATE TABLE foo (id integer primary key, name text)</pre>"
        in response.text
    )
    # Submit the form
    csrftoken = response.cookies["ds_csrftoken"]
    post_response = await datasette.client.post(
        "/test/-/assistant",
        cookies={
            "ds_csrftoken": csrftoken,
        },
        data={
            "question": "Count of rows in foo",
            "csrftoken": csrftoken,
        },
    )
    assert post_response.status_code == 302
    qs = dict(urllib.parse.parse_qsl(post_response.headers["location"].split("?")[1]))
    assert qs["sql"] == snapshot(
        """\
-- Count the total number of rows in the table 'foo'
SELECT COUNT(*) 
FROM foo;\
"""
    )
