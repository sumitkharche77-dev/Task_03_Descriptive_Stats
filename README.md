# Task 3: A Reusable Descriptive-Statistics System (Pure Python, Pandas, Polars)

Milestone B of Research Tasks 2 and 3. Task 2 produced three scripts that each analysed **one** hard-coded
dataset (the Facebook political-ads file). Here the same three approaches are generalised so that **any
well-formed CSV** can be analysed without editing code, and the system is run on three datasets:

| Short name | File | Rows x Columns |
|---|---|---|
| Facebook Ads | `2024_fb_ads_president_scored_anon.csv` | 246,745 x 41 |
| Facebook Posts | `2024_fb_posts_president_scored_anon.csv` | 19,009 x 56 |
| Twitter/X Posts | `2024_tw_posts_president_scored_anon.csv` | 27,304 x 47 |

## Data (not in this repository)

Datasets are **not committed**. Download them from
[Google Drive: 2024 Election Social Media Data](https://drive.google.com/file/d/1Jq0fPb-tq76Ee_RtM58fT0_M3o-JDBwe/view?usp=sharing)
(Facebook Posts and Twitter/X Posts) and
[Google Drive: 2024 Facebook Political Ads](https://drive.google.com/file/d/1UPo11lH2Mlk2cnLtjv8P9XqlKitms-gp/view?usp=sharing)
(Facebook Ads). Put the three CSVs in one folder, e.g. `data/` (ignored by git).

## Files

| File | Purpose |
|---|---|
| `common.py` | Shared **rules** and I/O only: type-detection patterns/thresholds, grouping-key rule, `ast.literal_eval` parsing of list/dict cells, report printing, JSON/CSV output. No loading, statistics or aggregation. |
| `pure_python_stats.py` | Standard library only (`csv`, `statistics`, `collections`, `re`, `ast`). |
| `pandas_stats.py` | Pandas implementation. |
| `polars_stats.py` | Polars implementation (lazy `group_by` for the grouped analysis). |
| `verify_agreement.py` | Compares the three approaches' results, column by column and group by group. |
| `compare_datasets.py` | Generates [`CROSS_DATASET.md`](CROSS_DATASET.md) from the results. |
| `run_all.sh` | Runs all 3 scripts x 3 datasets, then the verification. |
| `results/` | Output of the real runs (JSON, grouped CSVs, console reports, `verification.txt`). |
| `REFLECTION.md` | Answers to the optional research questions. |

Each engine implements loading, type detection, statistics and grouping **itself**; only the rules are shared.
That keeps the three-way comparison honest: agreement means three independent implementations of the same
specification, not one implementation called three times.

## How to run

```bash
pip install -r requirements.txt          # pandas, polars (pure Python needs nothing)

# any one script on any one CSV
python pure_python_stats.py data/2024_fb_ads_president_scored_anon.csv
python pandas_stats.py      data/2024_fb_posts_president_scored_anon.csv
python polars_stats.py      data/2024_tw_posts_president_scored_anon.csv

# optional: choose the grouping column(s) yourself instead of auto-detection
python polars_stats.py data/2024_tw_posts_president_scored_anon.csv --group-by lang
python polars_stats.py data/2024_fb_ads_president_scored_anon.csv   --group-by page_id,currency

# everything, then check agreement and rebuild the comparison
./run_all.sh data results
python compare_datasets.py results CROSS_DATASET.md
```

Arguments: `csv_path` (required), `--group-by col[,col]`, `--out-dir` (default `results`). Every run prints
`Analyzing dataset: <path>` first and a `DATASET: <file> APPROACH: <name>` banner on the report.
Outputs per run: `<dataset>__<approach>.json` (all statistics + elapsed seconds) and one CSV per grouping.
The pure-Python version streams the 500 MB file twice (one value-count table per column, then a grouping pass).
Pandas and Polars read the whole file; it ran on a 4 GB machine.

## How one script handles any dataset

**Types are detected from the data, not declared in code.** "Missing" means an empty cell, nothing else.
For each column, in this order (a rule fires when >= 99% of non-missing values satisfy it):

1. **empty**: no non-missing values.
2. **flag**: every distinct value is one of `0 1 0.0 1.0 True False`. Reported as a rate.
3. **numeric**: matches `-?(digits | 1,234 style)(.digits)?(e+N)?`. Count, mean, min, max, sample stdev, median, and the number of unparseable cells.
4. **list**: starts with `[` and ends with `]`; parsed with `ast.literal_eval`: average length, distinct elements, top 5 elements.
5. **dict**: starts with `{` and ends with `}`; parsed the same way: average number of keys.
6. **date**: matches `YYYY-MM[-DD][ T...]`: nunique, mode, min, max.
7. otherwise **categorical**: count, nunique, mode, top 5 (ties broken by value so all engines agree).

**Grouping keys are chosen automatically** (override with `--group-by`):

* *primary*: the categorical column with the most distinct values that is still <= 5% of the rows and is filled in for >= 95% of rows. This yields `page_id` (Ads), `Facebook_Id` (Posts) and `source` (Twitter).
* *primary + id*: the primary key plus the first column that is unique per row (`ad_id`, `post_id`, `id`).
* *time*: the date column with the most distinct values that is <= 5% of the rows (`ad_creation_time`, `Post Created Date`, `month_year`).

Group output: row count, and for every numeric column the sum and mean, and for every flag the rate.
Rows with an empty grouping key are excluded and counted (`rows_with_null_key`).

**Messy-data handling that mattered on these files**

* `Total Interactions` (Facebook Posts) stores large values as text with thousands separators (`268,841`). A plain numeric rule called it categorical (5,665 "categories"); the numeric rule now accepts that form and strips the commas.
* The first version picked `Video Length` (82.8% empty) as the Facebook Posts grouping key. Requiring >= 95% coverage fixed it, and the choice is now `Facebook_Id`.
* Four Facebook Posts columns are 100% empty, including one with a corrupt header (`illuminating_scored_messageelection_integrity_Truth_illuminating`, two names fused). They are reported as `empty`, not crashed on.
* Other robustness: UTF-8 BOM removed, undecodable bytes replaced rather than raising, ragged rows padded/truncated, blank or duplicate header names made unique.

## Findings per dataset

### Facebook Ads (246,745 ads, 41 columns)
* **Spend is extremely skewed:** `estimated_spend` mean 1,061.29 vs median 49 (max 474,999); impressions mean 45,601 vs median 3,499; audience size mean 556,463 vs median 300,000.
* **4,475 advertisers; one dominates:** the largest page_id placed 55,503 ads (22.5% of all rows) with about $82.8 million estimated spend; the median page placed 3 ads.
* **Content mix:** `cta` 57.28%, `advocacy` 54.86%, `issue` 38.16%, `attack` 27.19% of ads. Attack rate varies by advertiser: 27.4% for the largest page, 10.8% for the second largest.
* **Time:** 547 distinct creation dates between 2021-07-06 and 2024-11-05; the busiest day is 2024-10-27 (8,619 ads).
* **Missing data is rare:** only `bylines` (1,009 rows, 0.41%). Currency: 18 values, USD on 246,599 rows.
* **Grouping on `page_id` + `ad_id` is a no-op:** `ad_id` is unique, so all 246,745 groups have one row. The system detects and says so.
* The two nested columns parse fully (0 unparsed): `delivery_by_region` averages 21.97 regions per ad, `demographic_distribution` 13.01 buckets.

### Facebook Posts (19,009 posts, 56 columns)
* **21 pages; one produces 47% of posts** (9,013 posts), but it is far from the most engaged: mean likes 172 vs 5,043 for the third-largest page. Volume and engagement are different stories.
* **Engagement is heavy-tailed:** `Likes` mean 2,378 vs median 139 (max 351,979); `Total Interactions` mean 4,190 vs median 283 (after the thousands-separator fix).
* **Views are mostly zero or absent:** `Total Views` is missing for 2,465 posts (12.97%, exactly the rows with no `Type`) and has median 0 where present. Video fields are 82.79% missing (no video).
* **Content mix:** advocacy 54.93%, issue 46.03%, attack 21.67%, cta 13.28%.
* 425 distinct post dates (busiest 2024-10-31, 103 posts). The `Post Created` column mixes at least two formats (`2023-09-01 00:50:59 EDT` and `2024-11-06T20:00:35.000Z`), so its min/max are not trustworthy; the system's time key uses `Post Created Date` instead.

### Twitter/X Posts (27,304 tweets, 47 columns)
* **No author column exists.** The detected grouping key is `source` (the client app, 14 values): Twitter Web App 14,930 tweets (54.7%), Twitter for iPhone 8,494, Sprout Social 2,933.
* **Per-client behaviour differs a lot:** mean likes 18,173 for Sprout Social vs 6,670 for Web App; attack rate 37.1% on Web App vs 23.4% on iPhone.
* **Engagement is extreme:** `likeCount` mean 6,914 vs median 1,406; `viewCount` mean 507,085, median 70,942, max 333.5 million.
* **Reply/quote structure:** 12.36% are replies, 11.87% quotes, 0 retweets (`isRetweet` rate 0). `quoteId` and `inReplyToId` are about 88% empty.
* **Time:** 15 months (2023-09 to 2024-11); October 2024 is the busiest (3,586) and April 2024 the quietest full month (804).

## Cross-dataset comparison

Full tables are in [`CROSS_DATASET.md`](CROSS_DATASET.md), generated by `compare_datasets.py`. Headlines:

* **27 columns share an exact name in all three datasets** (the `*_illuminating` message-type, topic and CTA flags); Ads and Twitter additionally share `election_integrity_Truth_illuminating` and `illuminating_scored_message`. Facebook Ads and Facebook Posts share nothing that Twitter lacks.
* **The biggest platform difference is calls to action:** `cta_msg_type` is 57.28% of ads but 13.28% of Facebook posts and 10.97% of tweets (46-point gap); fundraising CTAs 22.85% vs 1.84% vs 0.79%. That is what paid persuasion looks like next to organic posting.
* **Organic channels look alike on message type:** advocacy is 54.9% / 54.9% / 56.4% (Ads / Posts / Twitter). Issue messages are most common on Twitter (50.8%); attack messages are highest on Twitter (30.8%), lowest on Facebook Posts (21.7%).
* **Topics diverge less:** most topic flags are within 1-3 points; the exceptions are health (10.9% in ads vs 4.9% / 5.6%) and covid (5.2% in Facebook Posts vs 2.5% / 0.8%).
* **Engagement metrics have analogues** (likes / comments-replies / shares-retweets / views) but are measured differently per platform. These pairings are an analyst's mapping, labeled as such in `CROSS_DATASET.md`; tweets have larger counts across all four, and views are missing for 13% of Facebook posts.

## Verification and benchmark

`verify_agreement.py` compares every statistic and every grouped CSV across the three approaches (relative
tolerance 1e-9). On the real files: **all 9 comparisons are identical** (`results/verification.txt`).

| Dataset | Pure Python | Pandas | Polars |
|---|---|---|---|
| Facebook Ads (500 MB) | 46.8 s | 47.4 s | 40.9 s |
| Facebook Posts (5.8 MB) | 0.58 s | 0.53 s | 0.14 s |
| Twitter/X Posts (11 MB) | 0.73 s | 0.66 s | 0.20 s |

Wall-clock on one MacBook Air (4 GB Linux VM), single run each, file reading included. Treat the small-file
differences as indicative and the 500 MB gap as modest. The three finish within about 15% of each other on
the big file, plausibly because all three parse the nested list/dict columns with the same `ast.literal_eval`
(not profiled separately).

## Limitations

* `quoteId` / `inReplyToId` are long identifiers but look numeric, so they are typed `numeric` and get a (meaningless) mean. A real system needs a "looks like an ID" rule or a user-supplied schema.
* Dates are detected by pattern only; mixed date formats (as in `Post Created`) are not normalised.
* Thresholds (99% for type, 5% cardinality, 95% coverage) are judgement calls; they worked on these three files but are untested elsewhere. `--group-by` exists for when they are wrong.
* The grouped analysis excludes rows with an empty key rather than creating a "missing" group.
* No visualisations were added.

See [`REFLECTION.md`](REFLECTION.md) for the research questions.
