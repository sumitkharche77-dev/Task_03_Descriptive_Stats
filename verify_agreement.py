"""
verify_agreement.py - check that the three approaches produced the same results.

Usage:
    python verify_agreement.py [results_dir]

For every dataset found in results_dir it loads <dataset>__pure_python.json,
<dataset>__pandas.json and <dataset>__polars.json, compares every column
statistic and every group summary (floats within a relative tolerance), and
compares the grouped CSVs cell by cell. Exits non-zero if anything differs.
"""

import csv
import glob
import json
import math
import os
import sys

REL_TOL = 1e-9
ABS_TOL = 1e-9
APPROACHES = ["pure_python", "pandas", "polars"]


def same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def diff(path, a, b, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: present in only one approach")
            else:
                diff(f"{path}/{k}", a[k], b[k], out)
    elif not same(a, b):
        out.append(f"{path}: {str(a)[:80]} != {str(b)[:80]}")


def compare_csv(pa, pb):
    with open(pa, newline="", encoding="utf-8") as fa, open(pb, newline="", encoding="utf-8") as fb:
        ra, rb = list(csv.reader(fa)), list(csv.reader(fb))
    if len(ra) != len(rb) or ra[0] != rb[0]:
        return [f"shape/header differ ({len(ra)} vs {len(rb)} rows)"]
    bad = []
    for i, (x, y) in enumerate(zip(ra[1:], rb[1:]), start=2):
        for j, (u, v) in enumerate(zip(x, y)):
            if u == v:
                continue
            try:
                if math.isclose(float(u), float(v), rel_tol=REL_TOL, abs_tol=ABS_TOL):
                    continue
            except ValueError:
                pass
            bad.append(f"row {i} col {ra[0][j]}: {u} != {v}")
            if len(bad) >= 5:
                return bad
    return bad


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else "results"
    datasets = sorted({os.path.basename(p).split("__")[0] for p in glob.glob(os.path.join(d, "*__pandas.json"))})
    if not datasets:
        raise SystemExit(f"no results found in {d}/")
    failures = 0
    for ds in datasets:
        res = {}
        for a in APPROACHES:
            p = os.path.join(d, f"{ds}__{a}.json")
            if os.path.exists(p):
                res[a] = json.load(open(p, encoding="utf-8"))
        if len(res) < 2:
            print(f"{ds}: only {len(res)} approach(es) available, skipped")
            continue
        base = "pure_python" if "pure_python" in res else next(iter(res))
        print(f"\n{ds}: comparing {', '.join(res)}")
        for a, r in res.items():
            if a == base:
                continue
            problems = []
            for part in ("n_rows", "n_cols", "group_keys", "columns", "groups"):
                diff(part, res[base][part], r[part], problems)
            for g in res[base]["groups"]:
                pa = os.path.join(d, f"{ds}__{base}__{g}.csv")
                pb = os.path.join(d, f"{ds}__{a}__{g}.csv")
                if os.path.exists(pa) != os.path.exists(pb):
                    problems.append(f"{g}.csv exists for only one approach")
                elif os.path.exists(pa):
                    problems += [f"{g}.csv: {m}" for m in compare_csv(pa, pb)]
            if problems:
                failures += 1
                print(f"  {base} vs {a}: {len(problems)} difference(s)")
                for m in problems[:15]:
                    print("    ", m)
            else:
                print(f"  {base} vs {a}: IDENTICAL "
                      f"({len(res[a]['columns'])} columns, {len(res[a]['groups'])} grouping(s), tol={REL_TOL})")
    print("\nRESULT:", "all approaches agree" if not failures else f"{failures} comparison(s) FAILED")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
