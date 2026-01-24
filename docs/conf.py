# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys

from importlib import metadata
from pathlib import Path

# Add the project root to the path so we can import oban
sys.path.insert(0, str(Path(__file__).parent.parent))

# -- Project information -----------------------------------------------------

project = "oban"
author = "Soren LLC"
copyright = f"2026 {author}"

release = metadata.version("oban")
version = release.rsplit(".", 1)[0]

# -- General configuration ---------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",  # Auto-generate docs from docstrings
    "sphinx.ext.napoleon",  # Support for Google-style docstrings
    "sphinx.ext.viewcode",  # Add links to highlighted source code
    "sphinx.ext.intersphinx",  # Link to other project's documentation
    "sphinx_autodoc_typehints",  # Better type hint rendering
    "sphinx_click",  # Auto-generate CLI documentation
    "sphinx_design",  # Tabs, cards, and other UI components
    "myst_parser",  # Markdown support
]

templates_path = ["_templates"]
exclude_patterns = [".DS_Store"]

# -- Options for HTML output -------------------------------------------------

html_theme = "sphinx_book_theme"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_logo = "_static/oban-logo.svg"

html_sidebars = {
    "**": [
        "navbar-logo.html",
        "version-switcher-sidebar.html",
        "search-button-field.html",
        "sbt-sidebar-nav.html",
    ]
}

html_theme_options = {
    "repository_url": "https://github.com/oban-bg/oban-py",
    "use_repository_button": True,
    "use_edit_page_button": False,
    "use_fullscreen_button": False,
    "show_toc_level": 2,
    "navigation_with_keys": True,
    "show_navbar_depth": 1,
    "switcher": {
        "json_url": os.environ.get(
            "DOCS_SWITCHER_URL", "https://oban.pro/docs/py/switcher.json"
        ),
        "version_match": release,
    },
}

# -- Extension configuration -------------------------------------------------

# Napoleon settings for Google-style docstrings
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = True
napoleon_use_admonition_for_notes = True
napoleon_use_admonition_for_references = False
napoleon_use_ivar = False
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_preprocess_types = False
napoleon_type_aliases = None
napoleon_attr_annotations = True

# Autodoc settings
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "special-members": "__init__",
    "undoc-members": True,
    "exclude-members": "__weakref__",
}
autodoc_typehints = "description"
autodoc_class_signature = "separated"

# Intersphinx mapping to link to other projects
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "psycopg": ("https://www.psycopg.org/psycopg3/docs/", None),
}

# MyST parser settings
myst_enable_extensions = [
    "colon_fence",  # ::: fences
    "deflist",  # Definition lists
    "fieldlist",  # Field lists
    "substitution",  # Substitutions
]
