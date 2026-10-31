"""
polars_stats.py - dataset-agnostic descriptive statistics with Polars.

Usage:
    python polars_stats.py <file.csv> [--group-by col1,col2] [--out-dir results]

The file is read with every column as text (infer_schema_length=0) so type
detection follows the shared rules in common.py. Grouped aggregation uses the
lazy API: df.lazy().group_by(...).agg(...).collect().
"""

import time

import polars as pl

import common as C


def size_summary(sizes):
    s = pl.Series(sizes)
    return {"min": int(s.min()), "median": float(s.median()), "max": int(s.max())} if len(s) else {}


def profile_column(s, n_rows):
    nn = s.drop_nulls()
    count = len(nn)
    info = {"missing": n_rows - count, "missing_pct": C.pct(n_rows - count, n_rows), "count": count}
    if count == 0:
        info["type"] = "empty"
        return info, None

    if set(nn.unique().to_list()) <= C.FLAG_TOKENS:
        flag = nn.is_in(list(C.FLAG_TRUE))
        ones = int(flag.sum())
        info.update(type="flag", ones=ones, rate=ones / count)
        return info, None

    is_num = nn.str.contains(f"^(?:{C.NUM_PATTERN})$")
    if C.passes(int(is_num.sum()), count):
        x = nn.filter(is_num).str.replace_all(",", "", literal=True).cast(pl.Float64)
        info.update(type="numeric", count=len(x), unparseable=count - len(x),
                    mean=float(x.mean()), min=float(x.min()), max=float(x.max()),
                    stdev=float(x.std(ddof=1)) if len(x) > 1 else 0.0, median=float(x.median()))
        return info, None

    vc = nn.value_counts()
    counts = dict(zip(vc[nn.name].to_list(), vc["count"].to_list()))
    if C.passes(int((nn.str.starts_with("[") & nn.str.ends_with("]")).sum()), count):
        info.update(type="list", **C.summarize_list_counts(counts))
        return info, None
    if C.passes(int((nn.str.starts_with("{") & nn.str.ends_with("}")).sum()), count):
        info.update(type="dict", **C.summarize_dict_counts(counts))
        return info, None

    is_date = C.passes(int(nn.str.contains(f"^(?:{C.DATE_PATTERN})$").sum()), count)
    top = C.top_n(counts)
    info.update(type="date" if is_date else "categorical", nunique=len(counts),
                mode=top[0][0], mode_freq=top[0][1], top5=top)
    if is_date:
        info.update(min=nn.min(), max=nn.max())
    return info, None


def main():
    args = C.parse_args("Dataset-agnostic descriptive statistics (Polars).")
    t0 = time.perf_counter()
    print(f"Analyzing dataset: {args.csv_path}  (approach: polars)")

    df = pl.read_csv(args.csv_path, infer_schema_length=0, null_values=[""],
                     encoding="utf8-lossy", truncate_ragged_lines=True)
    df = df.rename({c: (c.strip() or f"unnamed_{i}") for i, c in enumerate(df.columns)})
    n_rows = df.height

    columns = {name: profile_column(df[name], n_rows)[0] for name in df.columns}
    group_keys = C.choose_group_keys(columns, n_rows, args.group_by)

    num_cols = [n for n, i in columns.items() if i["type"] == "numeric"]
    flag_cols = [n for n, i in columns.items() if i["type"] == "flag"]
    num_exprs = {c: pl.when(pl.col(c).str.contains(f"^(?:{C.NUM_PATTERN})$"))
                 .then(pl.col(c).str.replace_all(",", "", literal=True)).otherwise(None).cast(pl.Float64)
                 for c in num_cols}
    flag_exprs = {c: pl.when(pl.col(c).is_null()).then(None)
                  .otherwise(pl.col(c).is_in(list(C.FLAG_TRUE)).cast(pl.Float64)) for c in flag_cols}

    groups = {}
    for gname, ks in group_keys.items():
        if not ks:
            continue
        valid = df.filter(pl.all_horizontal([pl.col(k).is_not_null() for k in ks]))
        null_keys = n_rows - valid.height
        if gname == "primary_plus_id":
            sizes = valid.lazy().group_by(ks).agg(pl.len().alias("n")).collect()
            recs = sorted((tuple(str(v) for v in r[:-1]) + (int(r[-1]),) for r in sizes.iter_rows()),
                          key=lambda r: (-r[-1], r[:-1]))
            groups[gname] = {"n_groups": len(recs), "rows_with_null_key": null_keys,
                             "size_distribution": size_summary(sizes["n"].to_list()),
                             "top5_by_n_rows": [[list(r[:-1]), r[-1]] for r in recs[:C.TOP_N]],
                             "note": "per-column aggregates skipped (one row per group)"}
            continue
        aggs = [pl.len().alias("n_rows")]
        for c in num_cols:
            aggs += [num_exprs[c].sum().alias(f"{c}__sum"), num_exprs[c].mean().alias(f"{c}__mean")]
        for c in flag_cols:
            aggs += [flag_exprs[c].mean().alias(f"{c}__rate")]
        res = valid.lazy().group_by(ks).agg(aggs).collect()
        header = C.agg_header(ks, columns)
        nk = len(ks)
        rows = []
        for r in res.select(header).iter_rows():
            rows.append([str(v) for v in r[:nk]] + [int(r[nk])] +
                        [None if v is None or v != v else float(v) for v in r[nk + 1:]])
        rows.sort(key=lambda r: (-r[nk], tuple(r[:nk])))
        groups[gname] = {"n_groups": len(rows), "rows_with_null_key": null_keys,
                         "size_distribution": size_summary([r[nk] for r in rows]),
                         "header": header, "rows": rows}

    C.finish("polars", args.csv_path, n_rows, columns, group_keys, groups,
             time.perf_counter() - t0, args.out_dir)


if __name__ == "__main__":
    main()
