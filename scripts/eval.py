"""Tüm projelerde kurgu kalitesini ölçer (kurgu.qa). Bir değişikliğin genel olarak iyileştirip
iyileştirmediğini tek videoya bakmadan görmek için: değişiklikten önce ve sonra çalıştır, puanları karşılaştır.

Kullanım:
  uv run python scripts/eval.py                 # tüm projeler, yalnızca ölç
  uv run python scripts/eval.py --fix           # düzeltilebilir hataları da düzelt (kartlar yeniden render edilir)
  uv run python scripts/eval.py <proje-id> ...  # seçili projeler
Sonuçlar eval/history.jsonl dosyasına eklenir.
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kurgu.core.project_store import Project  # noqa: E402
from kurgu.qa import auto_fix, check  # noqa: E402


def main(argv: list[str]) -> None:
    fix = "--fix" in argv
    ids = [a for a in argv if not a.startswith("--")] or [p.id for p in Project.list_all()]
    rows, kinds = [], Counter()
    for pid in ids:
        try:
            project = Project.open(pid)
            tl = project.timeline()
            before = check(project, tl)
            after = before
            if fix:
                rep: dict = {}
                after = auto_fix(project, tl, rep)
                if rep.get("qa_fixes"):
                    project.save_timeline(tl, note=f"QA düzeltmeleri: {len(rep['qa_fixes'])}")
            kinds.update(i["kind"] for i in after["issues"])
            rows.append({"project": pid, "before": before["score"], "after": after["score"], **after["metrics"]})
            print(f"{pid[:48]:48}  {before['score']:3} -> {after['score']:3}  "
                  f"hata {after['metrics']['errors']}  uyarı {after['metrics']['warnings']}")
        except Exception as e:  # tek proje hatası değerlendirmeyi durdurmasın
            print(f"{pid[:48]:48}  atlandı: {str(e)[:80]}")
    if rows:
        avg = sum(r["after"] for r in rows) / len(rows)
        print(f"\nOrtalama puan: {avg:.1f}  |  sorun türleri: {dict(kinds)}")
        out = ROOT / "eval"
        out.mkdir(exist_ok=True)
        with open(out / "history.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M"), "fix": fix, "avg": round(avg, 1),
                                "kinds": dict(kinds), "rows": rows}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
