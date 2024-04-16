from anthropic import AsyncAnthropic
from datasette import hookimpl, Response, Forbidden
import os
import urllib
import re
from datasette.utils import sqlite3
from typing import List, Set

SYSTEM_PROMPT = """
You answer questions by generating SQL queries using SQLite schema syntax.
Always start with -- SQL comments explaining what you are about to do.
No yapping. Output SQL with extensive SQL comments and nothing else.
Or output SQL in a sql tagged fenced markdown code block.
Return only one SQL SELECT query.
""".strip()

SCHEMA_SQL = """
select group_concat(sql, ';
') from sqlite_master where type != 'trigger'
"""
SCHEMA_SQL_SPECIFIC = """
select group_concat(sql, ';
') from sqlite_master where tbl_name in (PARAMS) and type != 'trigger'
"""


async def get_schema(db, table=None):
    if table:

        def _related(conn):
            return get_related_tables(conn, table)

        tables = await db.execute_fn(_related)
        tables.add(table)
        sql = SCHEMA_SQL_SPECIFIC.replace("PARAMS", ",".join("?" for _ in tables))
        return (await db.execute(sql, tuple(tables))).first()[0]
    else:
        return (await db.execute(SCHEMA_SQL)).first()[0]


async def has_permission(datasette, actor, database):
    return await datasette.permission_allowed(
        actor, "execute-sql", database, default=True
    )


async def generate_sql(client, messages, prefix=""):
    # if errors: show previous errors
    message = await client.messages.create(
        system=SYSTEM_PROMPT,
        max_tokens=1024,
        messages=messages,
        # model="claude-3-sonnet-20240229",
        model="claude-3-haiku-20240307",
        # model="claude-3-opus-20240229",
    )
    return prefix + message.content[0].text


async def generate_sql_with_retries(client, db, question, schema, max_retries=3):
    messages = [
        {"role": "user", "content": "The table schema is:\n" + schema},
        {"role": "assistant", "content": "Ask questions to generate SQL"},
        {"role": "user", "content": "How many rows in the sqlite_master table?"},
        {
            "role": "assistant",
            "content": "select count(*) from sqlite_master\n-- Count rows in the sqlite_master table",
        },
        {"role": "user", "content": question},
        {
            "role": "assistant",
            "content": "select",
        },
    ]
    attempt = 0
    while attempt < max_retries:
        attempt += 1
        sql = await generate_sql(client, messages, "select")
        # Try to run it as an explain
        # First remove any of those leading comment lines
        lines = sql.split("\n")
        not_comments = [line for line in lines if not line.startswith("-- ")]
        explain = "explain " + "\n".join(not_comments)
        print("--")
        print(explain)
        print("--")
        try:
            if explain.lower().split()[1] != "select":
                raise ValueError("only select queries are supported")
            await db.execute(explain)
            return sql
        except (sqlite3.Error, sqlite3.Warning, ValueError) as ex:
            messages.append(
                {
                    "role": "assistant",
                    "content": sql,
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": "Error: {}".format(str(ex)),
                }
            )
    # If we get here we are going to give up, but we'll send the query anyway
    sql += f"\n-- Gave up after {max_retries} attempts"
    return sql


async def assistant(request, datasette):
    database = request.url_vars["database"]
    db = datasette.get_database(database)
    if not await has_permission(datasette, request.actor, database):
        raise Forbidden("You do not have execute-sql permission")

    if request.method == "POST":
        post_vars = await request.post_vars()
        question = (post_vars.get("question") or "").strip()
        table = post_vars.get("table") or None
        if not question:
            datasette.add_message(request, "Question is required", datasette.ERROR)
            return Response.redirect(request.full_path)

        # Here we go
        schema = await get_schema(db, table)

        client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

        sql = await generate_sql_with_retries(client, db, question, schema)
        return Response.redirect(
            datasette.urls.database(database)
            + "?"
            + urllib.parse.urlencode({"sql": sql})
        )

    # Figure out tables
    table = request.args.get("table")
    schema = await get_schema(db, table)
    return Response.html(
        await datasette.render_template(
            "query_assistant.html",
            {"schema": schema, "database": database, "table": table},
            request=request,
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
) -> Set[str]:
    def get_directly_related_tables(table: str, explored_tables: Set[str]) -> Set[str]:
        related_tables = set()
        cursor = sqlite_connection.cursor()
        # Get tables that table has a foreign key to
        cursor.execute(f'PRAGMA foreign_key_list("{table}")')
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
                cursor.execute(f'PRAGMA foreign_key_list("{other_table}")')
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

    return set(all_related_tables)
