"""Добавляем каталог гейтвея в sys.path, чтобы модули (guards, gateway, …) импортировались
плоско, как в остальном треке (`from payloads import …`)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
