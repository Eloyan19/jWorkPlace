import sys
from pathlib import Path

# router.py лежит на уровень выше tests/ — добавляем в path, чтобы `import router` работал
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
