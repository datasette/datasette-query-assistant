from datasette import hookimpl, Response, Forbidden
from datasette.resources import DatabaseResource
import dataclasses
from llm import get_async_model
import re
import urllib
from markupsafe import escape
import markdown2
from datasette.utils import sqlite3
import itsdangerous
from typing import Tuple, Optional, Set

SYSTEM_PROMPT = """
You answer questions by generating SQL queries using SQLite schema syntax.
Always start with -- SQL comments explaining what you are about to do.
No yapping. Output SQL with extensive SQL comments in a sql tagged
fenced markdown code block.

Return only one SQL SELECT query. Follow the query with an explanation
of what the query does and how it works, which should include bold for
emphasis where appropriate.

Example question:

How many rows in the sqlite_master table?

Example output (shown between ----):
----
```sql
select count(*) from sqlite_master
```
Count the **number of rows** in the `sqlite_master` table.
----
The table schema is:
""".lstrip()

SCHEMA_SQL = """
select group_concat(sql, ';
') from sqlite_master where type != 'trigger'
"""
SCHEMA_SQL_SPECIFIC = """
select group_concat(sql, ';
') from sqlite_master where tbl_name in (PARAMS) and type != 'trigger'
"""


@dataclasses.dataclass
class Config:
    model_id: str


def config(datasette):
    return


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
    return await datasette.allowed(
        action="execute-sql",
        resource=DatabaseResource(database),
        actor=actor,
    )


_sql_re = re.compile(r"```sql\n(?P<sql>.*?)\n```(?P<explanation>.*)", re.DOTALL)


def extract_sql_and_explanation(sql) -> Tuple[str, Optional[str]]:
    match = _sql_re.search(sql)
    if match:
        return match.group("sql"), match.group("explanation")
    return sql, None


async def generate_sql_with_retries(
    model, db, question, schema, sql=None, max_retries=3
) -> Tuple[str, Optional[str]]:
    # if sql:
    # question = "Previous query:\n" + sql + "\n\n" + question
    attempt = 0
    conversation = model.conversation()
    while attempt < max_retries:
        attempt += 1
        response = await conversation.prompt(
            question, system=SYSTEM_PROMPT + schema, stream=False
        )
        sql, explanation = extract_sql_and_explanation(await response.text())
        # Try to run it as an explain
        # First remove any of those leading comment lines
        lines = sql.split("\n")
        not_comments = [line for line in lines if not line.startswith("-- ")]
        explain = "explain " + "\n".join(not_comments)
        try:
            if explain.lower().split()[1] != "select":
                raise ValueError("only select queries are supported")
            await db.execute(explain)
            return sql, explanation
        except (sqlite3.Error, sqlite3.Warning, ValueError) as ex:
            question = "Error: {}".format(str(ex))
    # If we get here we are going to give up, but we'll send the query anyway
    sql += f"\n-- Gave up after {max_retries} attempts"
    return sql, explanation


async def assistant(request, datasette):
    database = request.url_vars["database"]
    db = datasette.get_database(database)
    if not await has_permission(datasette, request.actor, database):
        raise Forbidden("You do not have execute-sql permission")

    if request.method == "POST":
        post_vars = await request.post_vars()
        question = (post_vars.get("question") or "").strip()
        sql = post_vars.get("sql") or None
        table = post_vars.get("table") or None
        if not question:
            datasette.add_message(request, "Question is required", datasette.ERROR)
            return Response.redirect(request.full_path)

        # Here we go
        schema = await get_schema(db, table)

        model = get_async_model("openai/gpt-4.1-mini")

        sql, explanation = await generate_sql_with_retries(
            model, db, question, schema, sql=sql
        )
        args = {"sql": sql}
        if explanation:
            args["explanation"] = datasette.sign(explanation, namespace="explanation")
        return Response.redirect(
            datasette.urls.database(database) + "?" + urllib.parse.urlencode(args)
        )

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
def top_query(request, datasette, database, sql):
    signed_explanation = request.args.get("explanation") or ""
    explanation = ""
    try:
        explanation_decoded = datasette.unsign(
            signed_explanation, namespace="explanation"
        )
        if explanation_decoded:
            explanation = '<div style="border: 2px solid #006400; background-color: #e8f5e9; margin-top: 1em; padding: 4px 10px 0px 10px; border-radius: 6px; max-width: 600px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); line-height: 1.5; color: #333;">{}</div>'.format(
                markdown2.markdown(explanation_decoded)
            )
    except itsdangerous.exc.BadSignature:
        explanation = ""
    return """
    <details><summary>AI query assistant</summary>
    <form class="core" action="{}/-/assistant" method="post">
    <p style="margin-top: 0.5em">
    <textarea placeholder="Describe a change to make to this query" name="question"
      style="width: 80%; height: 4em"></textarea></p>
    <p>
      <input type="submit" value="Update SQL">
      <input type="hidden" name="sql" value="{}">
      <input type="hidden" name="csrftoken" value="{}">
    </p>
    </form></details>
    {}
    """.format(
        datasette.urls.database(database),
        escape(sql),
        request.scope["csrftoken"](),
        explanation,
    )


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
