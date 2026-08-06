"""Прогон демо: 3 вектора × {без защиты, с защитой} + Bing-репро → отчёт (stdout + markdown).

Использование:
  python run.py --mock          # детерминированный симулятор (бесплатно, воспроизводимо)
  python run.py --real          # настоящая DeepSeek через project-адаптер (нужен ключ в backend/.env)
  python run.py --real --md report.md   # + сохранить markdown-отчёт

Итог печатает матрицу PASS(атака прошла)/BLOCKED и сохраняет отчёт для report.py (HTML).
"""
import argparse
import json
import sys
from pathlib import Path

from famous_cases import run_bing
from llm_backend import get_backend
from scenarios import SCENARIOS, run_agent


def _trim(s: str, n: int = 200) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n] + "…"


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--mock", action="store_const", dest="mode", const="mock")
    g.add_argument("--real", action="store_const", dest="mode", const="real")
    ap.add_argument("--md", type=str, default=None, help="путь для markdown-отчёта")
    ap.add_argument("--json", type=str, default=None, help="путь для JSON-результатов (для report.py)")
    ap.set_defaults(mode="mock")
    args = ap.parse_args()

    backend, backend_name = get_backend(args.mode)
    print(f"=== Indirect Prompt Injection demo — backend: {backend_name} ===\n")

    results = []
    for sc in SCENARIOS:
        und, und_note = run_agent(backend, sc, defended=False)
        dfd, dfd_note = run_agent(backend, sc, defended=True)
        row = {
            "key": sc.key, "title": sc.title, "carrier": sc.carrier,
            "undefended": {"answer": und, "note": und_note, "attacked": sc.succeeded(und)},
            "defended": {"answer": dfd, "note": dfd_note, "attacked": sc.succeeded(dfd)},
        }
        results.append(row)
        print(f"[{sc.key}] {sc.title}")
        print(f"   носитель: {sc.carrier}")
        u = "ПРОШЛА ✗" if row["undefended"]["attacked"] else "не прошла"
        d = "ПРОШЛА ✗" if row["defended"]["attacked"] else "BLOCKED ✓"
        print(f"   без защиты: {u:12} | {_trim(und, 90)}")
        print(f"   с защитой : {d:12} | {_trim(dfd, 90)}  ({dfd_note})")
        print()

    bing = run_bing(backend)
    print(f"[famous] {bing['title']}")
    bu = "ПРОШЛА ✗" if bing["undefended"]["attacked"] else "не прошла"
    bd = "ПРОШЛА ✗" if bing["defended"]["attacked"] else "BLOCKED ✓"
    print(f"   без защиты: {bu:12} | {_trim(bing['undefended']['answer'], 90)}")
    print(f"   с защитой : {bd:12} | {_trim(bing['defended']['answer'], 90)}\n")

    # Сводка.
    total = len(results) + 1
    passed_und = sum(r["undefended"]["attacked"] for r in results) + bing["undefended"]["attacked"]
    passed_dfd = sum(r["defended"]["attacked"] for r in results) + bing["defended"]["attacked"]
    print("=== СВОДКА ===")
    print(f"атак всего: {total}")
    print(f"прошло без защиты: {passed_und}/{total}")
    print(f"прошло со всеми 3 слоями защиты: {passed_dfd}/{total}")

    payload = {"backend": backend_name, "scenarios": results, "famous": bing,
               "summary": {"total": total, "passed_undefended": passed_und, "passed_defended": passed_dfd}}
    if args.json:
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"\nJSON → {args.json}")
    if args.md:
        Path(args.md).write_text(_markdown(payload), encoding="utf-8")
        print(f"markdown → {args.md}")
    return 0


def _markdown(p: dict) -> str:
    L = [f"# Indirect Prompt Injection — отчёт (backend: {p['backend']})\n"]
    s = p["summary"]
    L.append(f"**Прошло без защиты: {s['passed_undefended']}/{s['total']} · "
             f"после 3 слоёв защиты: {s['passed_defended']}/{s['total']}**\n")
    L.append("| вектор | носитель | без защиты | с защитой |")
    L.append("|---|---|---|---|")
    rows = p["scenarios"] + [{"title": p["famous"]["title"], "carrier": "OCR+comment",
                              "undefended": p["famous"]["undefended"], "defended": p["famous"]["defended"]}]
    for r in rows:
        u = "ПРОШЛА ✗" if r["undefended"]["attacked"] else "не прошла ✓"
        d = "ПРОШЛА ✗" if r["defended"]["attacked"] else "BLOCKED ✓"
        L.append(f"| {r['title']} | {r.get('carrier','')} | {u} | {d} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
