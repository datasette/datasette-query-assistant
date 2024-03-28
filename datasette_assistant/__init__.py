from anthropic import AsyncAnthropic
from datasette import hookimpl, Response, Forbidden
import os
import urllib
import sqlite3
from typing import List, Set

SYSTEM_PROMPT = """
You answer questions by generating SQL queries using SQLite schema syntax.
Always start with SQL comments explaining what you are about to do.
No yapping. Output SQL with extensive SQL comments and nothing else.
""".strip()

SCHEMA_SQL = """
select group_concat(sql, '; ') from sqlite_master;
"""

client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


async def has_permission(datasette, actor, database):
    return await datasette.permission_allowed(actor, "execute-sql", database)


async def generate_sql(question, schema, errors=None):
    messages = [
        {"role": "user", "content": "The table schema is:\n" + schema},
        {"role": "assistant", "content": "Ask questions to generate SQL"},
        {"role": "user", "content": "How many rows in the sqlite_master table?"},
        {"role": "assistant", "content": "-- Count rows in sqite_master table\nselect count(*) from sqlite_master"},
        {"role": "user", "content": question},
    ]
    # if errors: show previous errors
    message = await client.messages.create(
        system=SYSTEM_PROMPT,
        max_tokens=1024,
        messages=messages,
        model="claude-3-haiku-20240307",
    )
    return message.content[0].text


async def assistant(request, datasette):
    database = request.url_vars["database"]
    db = datasette.get_database(database)
    if not await has_permission(datasette, request.actor, database):
        raise Forbidden("You do not have execute-sql permission")

    if request.method == "POST":
        post_vars = await request.post_vars()
        question = (post_vars.get("question") or "").strip()
        if not question:
            datasette.add_message(request, "Question is required", datasette.ERROR)
            return Response.redirect(request.path)

        # Here we go
        schema = (await db.execute(SCHEMA_SQL)).first()[0]

        sql = await generate_sql(question, schema)
        return Response.redirect(
            datasette.urls.database(database)
            + "?"
            + urllib.parse.urlencode({"sql": sql})
        )

    # Figure out tables
    table = request.args.get("table")
    tables = []
    if not table:
        tables = await db.table_names()
    else:
        # Get tables related to this one
        def act(conn):
            return get_related_tables(conn, table)

        tables = await db.execute_fn(act)

    return Response.html(
        await datasette.render_template(
            "assistant.html", {"tables": tables}, request=request
        )
    )


@hookimpl
def table_actions(datasette, actor, table, database):
    async def inner():
        if await has_permission(datasette, actor, database):
            return [
                {
                    "href": datasette.urls.database(database)
                    + "/-/assistant?{}".format(
                        urllib.parse.urlencode({"table": table})
                    ),
                    "label": "Query this table with AI assistance",
                    "description": "Ask a question to build a SQL query",
                }
            ]

    return inner


@hookimpl
def database_actions(datasette, actor, database):
    async def inner():
        if await has_permission(datasette, actor, database):
            return [
                {
                    "href": datasette.urls.database(database) + "/-/assistant",
                    "label": "Query this database with AI assistance",
                    "description": "Ask a question to build a SQL query",
                }
            ]

    return inner


@hookimpl
def register_routes():
    return [
        (
            r"^/(?P<database>[^/]+)/-/assistant$",
            assistant,
        ),
    ]


def get_related_tables(
    sqlite_connection: sqlite3.Connection, table_name: str
) -> List[str]:
    def get_directly_related_tables(table: str, explored_tables: Set[str]) -> Set[str]:
        related_tables = set()
        cursor = sqlite_connection.cursor()
        # Get tables that table has a foreign key to
        cursor.execute(f"PRAGMA foreign_key_list({table})")
        for row in cursor.fetchall():
            related_table = row[2]
            if related_table not in explored_tables:
                related_tables.add(related_table)
                explored_tables.add(related_table)

        # Get tables that have a foreign key to table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        for row in cursor.fetchall():
            other_table = row[0]
            if other_table not in explored_tables:
                cursor.execute(f"PRAGMA foreign_key_list({other_table})")
                for fk_row in cursor.fetchall():
                    if fk_row[2] == table:
                        related_tables.add(other_table)
                        explored_tables.add(other_table)
                        break

        return related_tables

    all_related_tables = set()
    directly_related_tables = get_directly_related_tables(table_name, {table_name})
    while directly_related_tables:
        all_related_tables.update(directly_related_tables)
        new_directly_related_tables = set()
        for tbl in directly_related_tables:
            new_directly_related_tables.update(
                get_directly_related_tables(tbl, all_related_tables)
            )
        directly_related_tables = new_directly_related_tables

    return list(all_related_tables)
