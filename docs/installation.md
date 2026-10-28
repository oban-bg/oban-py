# Installation

Oban requires Python 3.12+ and PostgreSQL 14.0+. Ensure both are available and that PostgreSQL is
running before installation.

## Installing the Package

Install `oban` using your preferred package manager:

```bash
uv add oban
# or
pip install oban
```

## Configuration

Create an `oban.toml` file in your project root with your database connection string:

```toml
dsn = "postgresql://user:password@localhost/mydb"
```

## Installing the Schema

After the `oban` package and sub-dependencies are installed, you must install the necessary
tables, indexes, and functions to your database. Installation can be done through the CLI, with a
migration tool like Alembic, or programmatically.

`````{tab-set}

````{tab-item} CLI
The simplest approach is to use the CLI with your configuration file:

```bash
oban install
```

Or specify the connection string directly (if you didn't create the `oban.toml` config):

```bash
oban install --dsn "postgresql://user:password@localhost/mydb"
```
````

````{tab-item} Migrations
If you're using a migration framework like Alembic or Django, use the `install_sql` function to
get the raw SQL:

```python
from oban.schema import install_sql

sql = install_sql()
```

For Alembic:

```python
from alembic import op
from oban.schema import install_sql

def upgrade():
    op.execute(install_sql())
```

For Django:

```python
from django.db import migrations
from oban.schema import install_sql

class Migration(migrations.Migration):
    operations = [
        migrations.RunSQL(install_sql()),
    ]
```
````

````{tab-item} Python
You can also install the schema programmatically:

```python
import asyncio
from oban import Oban
from oban.schema import install

async def setup():
    pool = await Oban.create_pool()
    await install(pool)
    await pool.close()

asyncio.run(setup())
```
````

`````

## Verification

Verify the installation by starting Oban:

```bash
oban start
```

You should see output indicating Oban has started and is ready to process jobs. It's time to
create your first worker, schedule periodic jobs, and configure queues!
