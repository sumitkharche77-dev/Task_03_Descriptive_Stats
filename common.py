"""
common.py - shared, standard-library-only helpers for the Task 3 system.

What lives here (deliberately small):
  * the detection RULES (patterns / thresholds) so all three engines apply
    identical definitions of "numeric", "flag", "date", ...
  * the grouping-key selection rule (operates on a finished column profile)
  * parsing of string-encoded lists / dicts (always ast.literal_eval)
  * report printing and JSON/CSV output, so the three engines are comparable

What does NOT live here: loading, type detection, statistics and grouped
aggregation. Each engine (pure Python / Pandas / Polars) implements those on
its own, which is what makes cross-checking their outputs meaningful.
"""

import argparse
import ast
import csv
import json
import os
import re

# --- Detection rules (identical in every engine) ------------------------------
THRESHOLD_PCT = 99                 # % of non-null values that must match to assign a type
GROUP_CARDINALITY_FRAC = 0.05      # a grouping key may have at most 5% of n_rows distinct values
GROUP_MAX_MISSING_PCT = 5.0        # ...and must be filled in for at least 95% of rows
TOP_N = 5

NUM_PATTERN = r"-?(\d+|\d{1,3}(,\d{3})+)(\.\d+)?([eE][-+]?\d+)?"   # allows 1,234,567 style
DATE_PATTERN = r"\d{4}-\d{2}(-\d{2})?([ T].*)?"
NUM_RE = re.compile(NUM_PATTERN)


def to_float(v):
    """Parse a value already known to match NUM_PATTERN (thousands separators removed)."""
    return float(v.replace(",", ""))

DATE_RE = re.compile(DATE_PATTERN)

FLAG_TRUE = {"1", "1.0", "True"}
FLAG_FALSE = {"0", "0.0", "False"}
FLAG_TOKENS = FLAG_TRUE | FLAG_FALSE

# Null definition: exactly the empty string (nothing else is silently treated as missing).


def passes(matching, total):
    """True when `matching` is at least THRESHOLD_PCT percent of `total` (integer math)."""
    return total > 0 and matching * 100 >= THRESHOLD_PCT * total


def pct(part, whole):
    return (part / whole * 100.0) if whole else 0.0


def sort_counts(items):
    """Deterministic ordering: highest count first, ties broken by value."""
    return sorted(items, key=lambda kv: (-kv[1], kv[0]))


def top_n(counts, n=TOP_N):
    return [[k, v] for k, v in sort_counts(counts.items())[:n]]


# --- Nested string-encoded values ------------------------------------------------

def _hashable(x):
    return x if isinstance(x, str) else str(x)


def summarize_list_counts(counts):
    """counts: {raw_string: number_of_rows}. Parses each distinct string once."""
    parsed = unparsed = total_len = 0
    elements = {}
    for raw, c in counts.items():
        try:
            p = ast.literal_eval(raw)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            p = None
        if isinstance(p, list):
            parsed += c
            total_len += len(p) * c
            for x in p:
                k = _hashable(x)
                elements[k] = elements.get(k, 0) + c
        else:
            unparsed += c
    return {"parsed": parsed, "unparsed": unparsed,
            "avg_len": (total_len / parsed) if parsed else None,
            "element_nunique": len(elements), "element_top5": top_n(elements)}


def summarize_dict_counts(counts):
    parsed = unparsed = total_keys = 0
    for raw, c in counts.items():
        try:
            p = ast.literal_eval(raw)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            p = None
        if isinstance(p, dict):
            parsed += c
            total_keys += len(p) * c
        else:
            unparsed += c
    return {"parsed": parsed, "unparsed": unparsed,
            "avg_keys": (total_keys / parsed) if parsed else None}


# --- Command line -------------------------------------------------------------------

def parse_args(description):
    p = argparse.ArgumentParser(description=description)
    p.add_argument("csv_path", help="path to any well-formed CSV file")
    p.add_argument("--group-by", default=None,
                   help="comma-separated column(s) to use as the primary grouping key "
                        "(overrides auto-detection)")
    p.add_argument("--out-dir", default="results", help="where JSON/CSV results are written")
    return p.parse_args()


def dataset_name(path):
    return os.path.splitext(os.path.basename(path))[0]


# --- Grouping key selection (applied to a finished column profile) ---------------------

def choose_group_keys(columns, n_rows, override=None):
    """
    Returns {"primary": [...], "primary_plus_id": [...] | None, "time": [...] | None}.

    primary         categorical column with the MOST distinct values that is still at
                    most 5% of n_rows (the finest grouping that still aggregates) and
                    that is filled in for at least 95% of rows.
    primary_plus_id primary + the first categorical column that is unique per row.
    time            date-typed column with the most distinct values <= 5% of n_rows.
    """
    cap = max(2, int(GROUP_CARDINALITY_FRAC * n_rows))

    def best(kind):
        cands = [(info["nunique"], name) for name, info in columns.items()
                 if info["type"] == kind and 2 <= info.get("nunique", 0) <= cap
                 and info["missing_pct"] <= GROUP_MAX_MISSING_PCT]
        if not cands:
            return None
        top = max(n for n, _ in cands)
        for name in columns:                      # first in file order among ties
            if any(name == c[1] and c[0] == top for c in cands):
                return name

    if override:
        primary = [c.strip() for c in override.split(",") if c.strip()]
        missing = [c for c in primary if c not in columns]
        if missing:
            raise SystemExit(f"--group-by column(s) not found: {missing}")
    else:
        p = best("categorical")
        primary = [p] if p else None

    row_id = None
    for name, info in columns.items():
        if (info["type"] == "categorical" and info["count"] == n_rows
                and info["nunique"] == n_rows):
            row_id = name
            break

    time_key = best("date")
    return {
        "primary": primary,
        "primary_plus_id": (primary + [row_id]) if primary and row_id and row_id not in primary else None,
        "time": [time_key] if time_key else None,
    }


def agg_header(keys, columns):
    header = list(keys) + ["n_rows"]
    for name, info in columns.items():
        if info["type"] == "numeric":
            header += [f"{name}__sum", f"{name}__mean"]
    for name, info in columns.items():
        if info["type"] == "flag":
            header += [f"{name}__rate"]
    return header


# --- Output -------------------------------------------------------------------------------

def _clean(v):
    if isinstance(v, float) and v != v:
        return None
    return v


def finish(approach, path, n_rows, columns, group_keys, groups, elapsed, out_dir):
    """Write JSON + CSV outputs and print the human-readable report."""
    os.makedirs(out_dir, exist_ok=True)
    stem = dataset_name(path)
    result = {
        "approach": approach, "dataset": stem, "file": os.path.basename(path),
        "n_rows": n_rows, "n_cols": len(columns), "elapsed_seconds": round(elapsed, 3),
        "group_keys": group_keys, "columns": columns, "groups": {},
    }
    for gname, g in groups.items():
        rows = g.pop("rows", None)
        header = g.pop("header", None)
        entry = dict(g)
        entry["keys"] = group_keys[gname]
        if rows is not None:
            nk = len(group_keys[gname])
            entry["top5_by_n_rows"] = [[list(r[:nk]), r[nk]] for r in rows[:TOP_N]]
            csv_path = os.path.join(out_dir, f"{stem}__{approach}__{gname}.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(header)
                for r in rows:
                    w.writerow(["" if _clean(v) is None else v for v in r])
        result["groups"][gname] = entry
    json_path = os.path.join(out_dir, f"{stem}__{approach}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, default=str)
    print_report(result)
    print(f"\nWrote {json_path} (+ grouped CSVs) in {out_dir}/")
    return result


def fmt(v):
    return 'n/a' if v is None else f'{v:.2f}'


def _short(v, n=48):
    s = str(v)
    return s if len(s) <= n else s[: n - 3] + "..."


def print_report(r):
    cols = r["columns"]
    bar = "=" * 72
    print(bar)
    print(f"DATASET: {r['file']}   APPROACH: {r['approach']}")
    print(bar)
    print(f"Rows: {r['n_rows']:,}   Columns: {r['n_cols']}   Elapsed: {r['elapsed_seconds']}s")
    kinds = {}
    for info in cols.values():
        kinds[info["type"]] = kinds.get(info["type"], 0) + 1
    print("Detected types: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    miss = sorted(((i["missing"], n) for n, i in cols.items() if i["missing"]), reverse=True)
    print("Missing values (columns with any, top 10):")
    for m, n in miss[:10]:
        print(f"  {n}: {m:,} ({cols[n]['missing_pct']:.2f}%)")
    if not miss:
        print("  none")

    print("\n--- Numeric columns ---")
    for n, i in cols.items():
        if i["type"] == "numeric":
            print(f"{n}: count={i['count']:,} mean={i['mean']:.4f} min={i['min']:.4g} "
                  f"max={i['max']:.4g} stdev={i['stdev']:.4f} median={i['median']:.4g}"
                  + (f" unparseable={i['unparseable']:,}" if i["unparseable"] else ""))

    flags = sorted(((i["rate"], n) for n, i in cols.items() if i["type"] == "flag"), reverse=True)
    print(f"\n--- Flag (0/1, True/False) columns: {len(flags)} detected; top 8 by rate ---")
    for rate, n in flags[:8]:
        print(f"{n}: rate={rate:.4f} ({cols[n]['ones']:,} of {cols[n]['count']:,})")

    print("\n--- Categorical / date columns ---")
    for n, i in cols.items():
        if i["type"] in ("categorical", "date"):
            extra = f" range={i['min']} .. {i['max']}" if i["type"] == "date" else ""
            print(f"{n} [{i['type']}]: count={i['count']:,} nunique={i['nunique']:,} "
                  f"mode={_short(i['mode'])!r} (freq={i['mode_freq']:,}){extra}")

    print("\n--- List-valued columns ---")
    for n, i in cols.items():
        if i["type"] == "list":
            print(f"{n}: parsed={i['parsed']:,} unparsed={i['unparsed']:,} avg_len={fmt(i['avg_len'])} "
                  f"nunique_elements={i['element_nunique']:,} top5={[tuple(t) for t in i['element_top5']]}")

    print("\n--- Dict-valued columns ---")
    for n, i in cols.items():
        if i["type"] == "dict":
            print(f"{n}: parsed={i['parsed']:,} unparsed={i['unparsed']:,} avg_keys={fmt(i['avg_keys'])}")

    empties = [n for n, i in cols.items() if i["type"] == "empty"]
    if empties:
        print(f"\n--- Entirely empty columns: {empties}")

    print("\n" + bar)
    print("GROUPED ANALYSIS")
    print(bar)
    for gname, g in r["groups"].items():
        print(f"\n[{gname}] keys={g['keys']}  groups={g['n_groups']:,}  rows_with_null_key={g['rows_with_null_key']:,}")
        if "top5_by_n_rows" in g:
            print("  top 5 groups by row count:")
            for key, n in g["top5_by_n_rows"]:
                print(f"    {_short(key, 40)}  n_rows={n:,}")
        if "size_distribution" in g:
            print(f"  group size distribution: {g['size_distribution']}")
            if g["n_groups"] == r["n_rows"]:
                print("  NOTE: every group has exactly one row (the id column is unique),")
                print("        so this grouping is a no-op aggregation.")
    for gname in ("primary", "primary_plus_id", "time"):
        if gname not in r["groups"]:
            print(f"\n[{gname}] not applicable: no suitable column found for this dataset")
