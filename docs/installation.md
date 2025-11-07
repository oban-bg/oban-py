# Installation

## Requirements

- Python 3.12 or higher
- PostgreSQL database
- `uv` package manager (recommended) or `pip`

## Installing Oban

### Using uv (Recommended)

```bash
uv add oban
```

### Using pip

```bash
pip install oban
```

## Database Setup

Oban requires PostgreSQL. Make sure you have a PostgreSQL database available.

### Installing the Database Schema

Before using Oban, you need to install the required database schema:

```bash
oban install --dsn "postgresql://user:password@localhost/mydb"
```

Or using Python:

```python
import asyncio
from oban import Oban

async def setup():
    oban = Oban(dsn="postgresql://user:password@localhost/mydb")
    await oban.install()

asyncio.run(setup())
```

### Integrating with Migration Systems

If you're using a migration system like Alembic or Django migrations, you can generate the schema SQL:

```python
from oban import schema

# Get the SQL for creating the schema
sql = schema.get_install_sql()
# Add this to your migration file
```

## Development Installation

To install Oban for development with all dependencies:

```bash
# Clone the repository
git clone https://github.com/sorentwo/oban.git
cd oban

# Install with development dependencies
uv sync --group dev

# Install with documentation dependencies
uv sync --group docs
```

## Verification

Verify your installation by running:

```bash
oban --help
```

Or in Python:

```python
import oban
print(oban.__version__)
```

## Next Steps

Continue to the [Quickstart](quickstart.md) guide to learn how to use Oban.
