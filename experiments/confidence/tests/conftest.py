"""Делает `confidence_harness` (лежит в родительской папке, не пакет) импортируемым из tests/."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
