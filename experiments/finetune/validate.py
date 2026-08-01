#!/usr/bin/env python3
"""Валидатор датасета классификации тикетов поддержки (experiments/finetune/data/).

Учебный сайдкар, не прод-код backend'а. Проверяет train.jsonl + eval.jsonl:
структуру сообщений (system/user/assistant по порядку), фиксированный system-промпт,
формат assistant.content (JSON с ключами category/priority из таксономии), длины
user.content, отсутствие дублей и стратификацию сплита 80/20. Печатает сводку и
возвращает ненулевой exit code при любой ошибке — используется как гейт качества
данных перед отправкой в Kaggle-ноутбук.

Без сторонних зависимостей (только stdlib) — можно гонять локально без установки пакетов.
"""
import json
import sys
from collections import Counter
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
TRAIN_PATH = DATA_DIR / "train.jsonl"
EVAL_PATH = DATA_DIR / "eval.jsonl"

CATEGORIES = {"indexing", "pr_edits", "chat_grounding", "repo_connect", "account", "other"}
PRIORITIES = {"high", "medium", "low"}
ROLES_ORDER = ("system", "user", "assistant")
MIN_USER_LEN = 15
MAX_USER_LEN = 600
SPLIT_TOLERANCE = 0.05  # допуск вокруг 80/20


class ValidationError(Exception):
    """Накопленная ошибка валидации одной строки/файла (не прерывает сбор остальных)."""


def _load_jsonl(path: Path, errors: list[str]) -> list[dict]:
    if not path.exists():
        errors.append(f"{path.name}: файл не найден")
        return []
    records = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.rstrip("\n")
            if not line.strip():
                errors.append(f"{path.name}:{lineno}: пустая строка")
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                errors.append(f"{path.name}:{lineno}: невалидный JSON ({exc})")
    return records


def _validate_record(path_name: str, lineno: int, record: dict, system_prompt: list[str],
                      seen_user_texts: set[str], errors: list[str]) -> tuple[str, str] | None:
    loc = f"{path_name}:{lineno}"
    messages = record.get("messages")
    if not isinstance(messages, list) or len(messages) != 3:
        errors.append(f"{loc}: ожидалось ровно 3 сообщения, получено {messages!r}")
        return None

    for msg, expected_role in zip(messages, ROLES_ORDER):
        if not isinstance(msg, dict) or msg.get("role") != expected_role:
            errors.append(f"{loc}: ожидалась роль '{expected_role}', получено {msg!r}")
            return None
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            errors.append(f"{loc}: пустой content у роли '{expected_role}'")
            return None

    system_content = messages[0]["content"]
    if not system_prompt:
        system_prompt.append(system_content)
    elif system_content != system_prompt[0]:
        errors.append(f"{loc}: system-промпт отличается от эталонного (первой строки файла)")

    user_text = messages[1]["content"]
    if not (MIN_USER_LEN <= len(user_text) <= MAX_USER_LEN):
        errors.append(
            f"{loc}: длина user.content={len(user_text)} вне диапазона "
            f"[{MIN_USER_LEN}, {MAX_USER_LEN}]"
        )
    dup_key = user_text.strip()
    if dup_key in seen_user_texts:
        errors.append(f"{loc}: дубликат user.content: {user_text[:60]!r}...")
    else:
        seen_user_texts.add(dup_key)

    assistant_raw = messages[2]["content"]
    try:
        assistant_obj = json.loads(assistant_raw)
    except json.JSONDecodeError:
        errors.append(f"{loc}: assistant.content не парсится как JSON: {assistant_raw!r}")
        return None

    if not isinstance(assistant_obj, dict) or set(assistant_obj.keys()) != {"category", "priority"}:
        errors.append(f"{loc}: assistant JSON должен иметь ровно ключи category,priority: {assistant_obj!r}")
        return None

    category = assistant_obj["category"]
    priority = assistant_obj["priority"]
    if category not in CATEGORIES:
        errors.append(f"{loc}: category={category!r} не из таксономии {sorted(CATEGORIES)}")
    if priority not in PRIORITIES:
        errors.append(f"{loc}: priority={priority!r} не из таксономии {sorted(PRIORITIES)}")

    keys = list(assistant_obj.keys())
    if keys != ["category", "priority"]:
        errors.append(f"{loc}: порядок ключей assistant JSON должен быть category,priority, получено {keys}")

    if category in CATEGORIES and priority in PRIORITIES:
        return category, priority
    return None


def _print_distribution(title: str, labels: list[tuple[str, str]]) -> None:
    cats = Counter(c for c, _ in labels)
    prios = Counter(p for _, p in labels)
    print(f"  {title}: n={len(labels)}")
    print(f"    категории:  {dict(sorted(cats.items()))}")
    print(f"    приоритеты: {dict(sorted(prios.items()))}")


def main() -> int:
    errors: list[str] = []
    system_prompt: list[str] = []
    seen_user_texts: set[str] = set()

    train_records = _load_jsonl(TRAIN_PATH, errors)
    eval_records = _load_jsonl(EVAL_PATH, errors)

    train_labels: list[tuple[str, str]] = []
    for i, rec in enumerate(train_records):
        label = _validate_record("train.jsonl", i, rec, system_prompt, seen_user_texts, errors)
        if label:
            train_labels.append(label)

    eval_labels: list[tuple[str, str]] = []
    for i, rec in enumerate(eval_records):
        label = _validate_record("eval.jsonl", i, rec, system_prompt, seen_user_texts, errors)
        if label:
            eval_labels.append(label)

    total = len(train_records) + len(eval_records)
    if total > 0:
        train_ratio = len(train_records) / total
        expected_train_ratio = 0.8
        if abs(train_ratio - expected_train_ratio) > SPLIT_TOLERANCE:
            errors.append(
                f"сплит train/eval={train_ratio:.2%}/{1 - train_ratio:.2%} "
                f"выходит за допуск ±{SPLIT_TOLERANCE:.0%} вокруг 80/20"
            )

    manifest_path = DATA_DIR / "manifest.json"
    real_or_faq_ratio = None
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            real_or_faq_ratio = manifest.get("real_or_faq_derived_ratio")
            if real_or_faq_ratio is not None and real_or_faq_ratio < 0.20:
                errors.append(
                    f"manifest.json: доля real+faq_derived={real_or_faq_ratio:.1%} < требуемых 20%"
                )
        except json.JSONDecodeError as exc:
            errors.append(f"manifest.json: невалидный JSON ({exc})")
    else:
        errors.append("manifest.json: файл не найден")

    print("=== Сводка датасета classification тикетов jWorkPlace ===")
    _print_distribution("train.jsonl", train_labels)
    _print_distribution("eval.jsonl", eval_labels)
    print(f"  всего примеров: {total}")
    if real_or_faq_ratio is not None:
        print(f"  доля real+faq_derived (manifest.json): {real_or_faq_ratio:.1%}")

    if errors:
        print(f"\n=== ОШИБКИ ({len(errors)}) ===")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("\nOK: датасет валиден.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
