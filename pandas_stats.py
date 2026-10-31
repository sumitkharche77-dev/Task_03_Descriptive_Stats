"""
pandas_stats.py - dataset-agnostic descriptive statistics with Pandas.

Usage:
    python pandas_stats.py <file.csv> [--group-by col1,col2] [--out-dir results]

Everything is read as text first (dtype=str, low_memory=False) so that type
detection is done by the rules in common.py rather than by pandas' guessing;
numeric/flag columns are then converted with pd.to_numeric.
"""

import sys
import time

import pandas as pd

import common as C


def summarize_group_sizes(sizes):
    s = pd.Series(sizes)
    return {"min": int(s.min()), "median": float(s.median()), "max": int(s.max())} if len(s) else {}


def profile_column(s, n_rows):
    nn = s.dropna()
    count = int(len(nn))
    info = {"missing": n_rows - count, "missing_pct": C.pct(n_rows - count, n_rows), "count": count}
    if count == 0:
        info["type"] = "empty"
        return info, None

    distinct = set(nn.unique())
    if distinct <= C.FLAG_TOKENS:
        ones = int(nn.isin(C.FLAG_TRUE).sum())
        info.update(type="flag", ones=ones, rate=ones / count)
        return info, nn.isin(C.FLAG_TRUE).astype(float)

    is_num = nn.str.fullmatch(C.NUM_PATTERN)
    if C.passes(int(is_num.sum()), count):
        x = pd.to_numeric(nn[is_num].str.replace(",", "", regex=False), errors="coerce")
        info.update(type="numeric", count=int(x.count()), unparseable=count - int(x.count()),
                    mean=float(x.mean()), min=float(x.min()), max=float(x.max()),
                    stdev=float(x.std(ddof=1)) if x.count() > 1 else 0.0, median=float(x.median()))
        return info, x

    counts = nn.value_counts().to_dict()
    if C.passes(int((nn.str.startswith("[") & nn.str.endswith("]")).sum()), count):
        info.update(type="list", **C.summarize_list_counts(counts))
        return info, None
    if C.passes(int((nn.str.startswith("{") & nn.str.endswith("}")).sum()), count):
        info.update(type="dict", **C.summarize_dict_counts(counts))
        return info, None

    is_date = C.passes(int(nn.str.fullmatch(C.DATE_PATTERN).sum()), count)
    top = C.top_n(counts)
    info.update(type="date" if is_date else "categorical", nunique=len(counts),
                mode=top[0][0], mode_freq=top[0][1], top5=top)
    if is_date:
        info.update(min=nn.min(), max=nn.max())
    return info, None


def main():
    args = C.parse_args("Dataset-agnostic descriptive statistics (Pandas).")
    t0 = time.perf_counter()
    print(f"Analyzing dataset: {args.csv_path}  (approach: pandas)")

    df = pd.read_csv(args.csv_path, dtype=str, keep_default_na=False, na_values=[""],
                     low_memory=False, encoding="utf-8-sig", encoding_errors="replace",
                     on_bad_lines="skip")
    df.columns = [str(c).strip() or f"unnamed_{i}" for i, c in enumerate(df.columns)]
    n_rows = len(df)

    columns, numeric, flags = {}, {}, {}
    for name in df.columns:
        info, series = profile_column(df[name], n_rows)
        columns[name] = info
        if info["type"] == "numeric":
            numeric[name] = series.reindex(df.index)
        elif info["type"] == "flag":
            flags[name] = series.reindex(df.index)

    group_keys = C.choose_group_keys(columns, n_rows, args.group_by)
    groups = {}
    num_df = pd.DataFrame(numeric)
    flag_df = pd.DataFrame(flags)
    for gname, ks in group_keys.items():
        if not ks:
            continue
        valid = df[ks].notna().all(axis=1)
        null_keys = int((~valid).sum())
        if gname == "primary_plus_id":
            sizes = df[valid].groupby(ks, sort=False).size().reset_index(name="n")
            recs = sorted((tuple(map(str, r[:-1])) + (int(r[-1]),) for r in sizes.itertuples(index=False)),
                          key=lambda r: (-r[-1], r[:-1]))
            groups[gname] = {"n_groups": len(recs), "rows_with_null_key": null_keys,
                             "size_distribution": summarize_group_sizes(sizes["n"].tolist()),
                             "top5_by_n_rows": [[list(r[:-1]), r[-1]] for r in recs[:C.TOP_N]],
                             "note": "per-column aggregates skipped (one row per group)"}
            continue
        key_df = df.loc[valid, ks]
        parts = {"n_rows": key_df.iloc[:, 0].groupby([key_df[k] for k in ks], sort=False).size()}
        gb_keys = [key_df[k] for k in ks]
        for c in numeric:
            g = num_df.loc[valid, c].groupby(gb_keys, sort=False)
            parts[f"{c}__sum"] = g.sum()
            parts[f"{c}__mean"] = g.mean()
        for c in flags:
            parts[f"{c}__rate"] = flag_df.loc[valid, c].groupby(gb_keys, sort=False).mean()
        res = pd.DataFrame(parts).reset_index()
        header = C.agg_header(ks, columns)
        res = res[header]
        nk = len(ks)
        rows = []
        for r in res.itertuples(index=False):
            rows.append([str(v) for v in r[:nk]] + [int(r[nk])] +
                        [None if (isinstance(v, float) and v != v) else float(v) for v in r[nk + 1:]])
        rows.sort(key=lambda r: (-r[nk], tuple(r[:nk])))
        groups[gname] = {"n_groups": len(rows), "rows_with_null_key": null_keys,
                         "size_distribution": summarize_group_sizes([r[nk] for r in rows]),
                         "header": header, "rows": rows}

    C.finish("pandas", args.csv_path, n_rows, columns, group_keys, groups,
             time.perf_counter() - t0, args.out_dir)


if __name__ == "__main__":
    main()
