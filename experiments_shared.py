"""Compatibility shim for the moved :mod:`experiments.shared` package."""

from pathlib import Path

_TARGET_PACKAGE_DIR = Path(__file__).resolve().parent / "experiments" / "shared"
__path__ = [str(_TARGET_PACKAGE_DIR)]
__package__ = __name__
__file__ = str(_TARGET_PACKAGE_DIR / "__init__.py")
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__

exec(
    compile(
        (_TARGET_PACKAGE_DIR / "__init__.py").read_text(encoding="utf-8"),
        str(_TARGET_PACKAGE_DIR / "__init__.py"),
        "exec",
    )
)
