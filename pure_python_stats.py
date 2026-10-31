"""
pure_python_stats.py - dataset-agnostic descriptive statistics, standard library only.

Usage:
    python pure_python_stats.py <file.csv> [--group-by col1,col2] [--out-dir results]

Pass 1 streams the file once and keeps one value->count table per column; types
and statistics are derived from those tables (weighted by count), which keeps
memory far below "load every cell". Pass 2 streams the file again and
aggregates the groups chosen from the pass-1 profile.
"""

import csv
import statistics
import sys
import time
from collections import Counter

import common as C

csv.field_size_limit(2**31 - 1)


def open_csv(path):
    # utf-8-sig removes a BOM if present; undecodable bytes become U+FFFD instead of crashing.
    return open(path, newline="", encoding="utf-8-sig", errors="replace")


def clean_header(raw):
    seen, out = {}, []
    for i, h in enumerate(raw):
        h = h.strip() or f"unnamed_{i}"
        if h in seen:
            seen[h] += 1
            h = f"{h}.{seen[h]}"
        else:
            seen[h] = 0
        out.append(h)
    return out


def rows_of(path, ncols):
    """Yield rows padded / truncated to ncols (ragged rows are tolerated and counted)."""
    with open_csv(path) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if not row:
                continue
            if len(row) != ncols:
                rows_of.ragged += 1
                row = (row + [""] * ncols)[:ncols]
            yield row


rows_of.ragged = 0


# --------------------------------------------------------------------------- profiling
def weighted_median(pairs, n):
    pairs = sorted(pairs)
    lo_i, hi_i = (n - 1) // 2, n // 2
    acc, lo, hi = 0, None, None
    for v, c in pairs:
        acc += c
        if lo is None and acc > lo_i:
            lo = v
        if hi is None and acc > hi_i:
            hi = v
            break
    return (lo + hi) / 2.0


def profile_column(name, cnt, n_rows):
    nonnull_counts = {v: c for v, c in cnt.items() if v != ""}
    count = sum(nonnull_counts.values())
    info = {"missing": n_rows - count, "missing_pct": C.pct(n_rows - count, n_rows), "count": count}
    if count == 0:
        info["type"] = "empty"
        return info

    distinct = set(nonnull_counts)
    if distinct <= C.FLAG_TOKENS:
        ones = sum(c for v, c in nonnull_counts.items() if v in C.FLAG_TRUE)
        info.update(type="flag", ones=ones, rate=ones / count)
        return info

    num_n = sum(c for v, c in nonnull_counts.items() if C.NUM_RE.fullmatch(v))
    if C.passes(num_n, count):
        pairs = [(C.to_float(v), c) for v, c in nonnull_counts.items() if C.NUM_RE.fullmatch(v)]
        total = sum(c for _, c in pairs)
        mean = sum(v * c for v, c in pairs) / total
        var = (sum(c * (v - mean) ** 2 for v, c in pairs) / (total - 1)) if total > 1 else 0.0
        info.update(type="numeric", count=total, unparseable=count - total, mean=mean,
                    min=min(v for v, _ in pairs), max=max(v for v, _ in pairs),
                    stdev=var ** 0.5, median=weighted_median(pairs, total))
        info["_numeric_nonnull"] = count
        return info

    def frac(pred):
        return C.passes(sum(c for v, c in nonnull_counts.items() if pred(v)), count)

    if frac(lambda v: v.startswith("[") and v.endswith("]")):
        info.update(type="list", **C.summarize_list_counts(nonnull_counts))
        return info
    if frac(lambda v: v.startswith("{") and v.endswith("}")):
        info.update(type="dict", **C.summarize_dict_counts(nonnull_counts))
        return info

    is_date = frac(lambda v: C.DATE_RE.fullmatch(v) is not None)
    top = C.top_n(nonnull_counts)
    info.update(type="date" if is_date else "categorical", nunique=len(nonnull_counts),
                mode=top[0][0], mode_freq=top[0][1], top5=top)
    if is_date:
        info.update(min=min(distinct), max=max(distinct))
    return info


# --------------------------------------------------------------------------- grouping
def size_summary(sizes):
    sizes = sorted(sizes)
    return {"min": sizes[0], "median": statistics.median(sizes), "max": sizes[-1]} if sizes else {}


def group_pass(path, header, columns, key_defs, n_rows):
    idx = {n: i for i, n in enumerate(header)}
    num_cols = [n for n, i in columns.items() if i["type"] == "numeric"]
    flag_cols = [n for n, i in columns.items() if i["type"] == "flag"]
    num_idx = [idx[n] for n in num_cols]
    flag_idx = [idx[n] for n in flag_cols]

    state = {}      # gname -> {key: [n, (sum,cnt)*numeric, (ones,cnt)*flags]}
    sizes_only = {}  # gname -> {key: n}
    null_keys = {g: 0 for g in key_defs}
    full = [g for g in key_defs if g != "primary_plus_id"]
    for g in key_defs:
        (state if g in full else sizes_only)[g] = {}
    key_idx = {g: [idx[k] for k in ks] for g, ks in key_defs.items()}

    for row in rows_of(path, len(header)):
        for g, kidx in key_idx.items():
            key = tuple(row[i] for i in kidx)
            if "" in key:
                null_keys[g] += 1
                continue
            if g in sizes_only:
                d = sizes_only[g]
                d[key] = d.get(key, 0) + 1
                continue
            st = state[g].get(key)
            if st is None:
                st = [0] + [0.0, 0] * len(num_idx) + [0, 0] * len(flag_idx)
                state[g][key] = st
            st[0] += 1
            p = 1
            for i in num_idx:
                v = row[i]
                if v and C.NUM_RE.fullmatch(v):
                    st[p] += C.to_float(v)
                    st[p + 1] += 1
                p += 2
            for i in flag_idx:
                v = row[i]
                if v:
                    st[p + 1] += 1
                    if v in C.FLAG_TRUE:
                        st[p] += 1
                p += 2

    groups = {}
    for g, ks in key_defs.items():
        if g in sizes_only:
            d = sizes_only[g]
            ranked = sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))
            groups[g] = {"n_groups": len(d), "rows_with_null_key": null_keys[g],
                         "size_distribution": size_summary(list(d.values())),
                         "top5_by_n_rows": [[list(k), n] for k, n in ranked[:C.TOP_N]],
                         "note": "per-column aggregates skipped (one row per group)"}
            continue
        rows = []
        for key, st in state[g].items():
            out = list(key) + [st[0]]
            p = 1
            for _ in num_idx:
                out += [st[p], (st[p] / st[p + 1]) if st[p + 1] else None]
                p += 2
            for _ in flag_idx:
                out += [(st[p] / st[p + 1]) if st[p + 1] else None]
                p += 2
            rows.append(out)
        nk = len(ks)
        rows.sort(key=lambda r: (-r[nk], tuple(r[:nk])))
        groups[g] = {"n_groups": len(rows), "rows_with_null_key": null_keys[g],
                     "size_distribution": size_summary([r[nk] for r in rows]),
                     "header": C.agg_header(ks, columns), "rows": rows}
    return groups


# --------------------------------------------------------------------------- main
def main():
    args = C.parse_args("Dataset-agnostic descriptive statistics (pure Python).")
    t0 = time.perf_counter()
    print(f"Analyzing dataset: {args.csv_path}  (approach: pure_python)")

    with open_csv(args.csv_path) as f:
        header = clean_header(next(csv.reader(f)))
    counters = [Counter() for _ in header]
    n_rows = 0
    for row in rows_of(args.csv_path, len(header)):
        n_rows += 1
        for c, v in zip(counters, row):
            c[v] += 1

    columns = {}
    for name, cnt in zip(header, counters):
        columns[name] = profile_column(name, cnt, n_rows)
    for info in columns.values():
        info.pop("_numeric_nonnull", None)
    del counters

    group_keys = C.choose_group_keys(columns, n_rows, args.group_by)
    key_defs = {g: ks for g, ks in group_keys.items() if ks}
    groups = group_pass(args.csv_path, header, columns, key_defs, n_rows)
    if rows_of.ragged:
        print(f"WARNING: {rows_of.ragged:,} ragged row-reads were padded/truncated", file=sys.stderr)
    C.finish("pure_python", args.csv_path, n_rows, columns, group_keys, groups,
             time.perf_counter() - t0, args.out_dir)


if __name__ == "__main__":
    main()
