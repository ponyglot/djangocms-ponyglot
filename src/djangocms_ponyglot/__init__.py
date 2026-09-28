"""django CMS connector for Ponyglot: pages and plugins with glossary, translation memory and
delta sync, delivered as djangocms-versioning drafts. See https://ponyglot.app.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("djangocms-ponyglot")
except PackageNotFoundError:  # running from a source checkout without installing
    __version__ = "0.0.0"
