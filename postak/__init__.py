from importlib.metadata import PackageNotFoundError, version

from postak.access import AccessPolicy, AccessScope, CanAnswer
from postak.app import Postak
from postak.generation import ModelConfigurable, OpenAIGenerator
from postak.registry import AdminRegistry, ChannelRegistry
from postak.store import InMemoryDialogStore, SqliteDialogStore, create_store

__all__ = [
    "AccessPolicy",
    "AccessScope",
    "AdminRegistry",
    "CanAnswer",
    "ChannelRegistry",
    "InMemoryDialogStore",
    "ModelConfigurable",
    "OpenAIGenerator",
    "Postak",
    "SqliteDialogStore",
    "create_store",
]

try:
    __version__ = version("postak")
except PackageNotFoundError:
    __version__ = "0.0.0"
