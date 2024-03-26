# datasette-assistant

[![PyPI](https://img.shields.io/pypi/v/datasette-assistant.svg)](https://pypi.org/project/datasette-assistant/)
[![Changelog](https://img.shields.io/github/v/release/datasette/datasette-assistant?include_prereleases&label=changelog)](https://github.com/datasette/datasette-assistant/releases)
[![Tests](https://github.com/datasette/datasette-assistant/actions/workflows/test.yml/badge.svg)](https://github.com/datasette/datasette-assistant/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/datasette/datasette-assistant/blob/main/LICENSE)

Query databases and tables with AI assistance

**Early alpha**.

## Installation

Install this plugin in the same environment as Datasette.
```bash
datasette install datasette-assistant
```
## Usage

Users with `execute-sql` permission will gain a database action menu item for "Query this database with AI assistance" which will let them ask a question and be redirected to SQL that will hopefully answer it.

## Development

To set up this plugin locally, first checkout the code. Then create a new virtual environment:
```bash
cd datasette-assistant
python3 -m venv venv
source venv/bin/activate
```
Now install the dependencies and test dependencies:
```bash
pip install -e '.[test]'
```
To run the tests:
```bash
pytest
```
