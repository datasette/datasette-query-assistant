from datasette.app import Datasette
from datasette_query_assistant import get_related_tables
import pytest
import sqlite_utils


@pytest.mark.asyncio
async def test_plugin_is_installed():
    datasette = Datasette(memory=True)
    response = await datasette.client.get("/-/plugins.json")
    assert response.status_code == 200
    installed_plugins = {p["name"] for p in response.json()}
    assert "datasette-query-assistant" in installed_plugins


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
