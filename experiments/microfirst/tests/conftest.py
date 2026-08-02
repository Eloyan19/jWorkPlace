import sys
from pathlib import Path

# micro.py / pipeline.py лежат на уровень выше tests/ — в path, чтобы импорты работали
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
