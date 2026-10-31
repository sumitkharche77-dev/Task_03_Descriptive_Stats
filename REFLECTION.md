# Reflection: From Three Approaches to a Reusable System

Answers to a selection of the optional research questions, grounded in what actually happened while building
and running the system (numbers refer to the real runs in `results/`).

## 1. How much of the Milestone A code could be reused?

Less than I expected, and in a specific way. What carried over was the *structure*: read, classify columns,
summarise each class, group. Almost none of the *code* carried over, because Milestone A's classification was a
set of hand-written lists (`FLAG_COLUMNS`, `NUMERIC_COLUMNS`, `NESTED_DICT_COLUMNS`, ...) naming the ads
file's columns. The generalisation work was therefore mostly replacing those lists with rules that can be
computed from the data, and then deciding where each rule's boundaries are (99% threshold, which tokens count as
a flag, what counts as a date). The per-class summaries (numeric, flag rate, categorical top-5, list/dict
parsing) did transfer almost unchanged.

The biggest design change was Milestone A's `page_id` grouping. It worked because I knew `page_id` was the
right key. For a file I know nothing about, choosing the key is a decision the script has to make, and it
made a wrong one the first time (see 2).

## 2. What strategies make a script dataset-agnostic?

* **Detect types from values, in a fixed order, with a tolerance.** A 99% rule survives a few dirty cells
  (the numeric rule reports `unparseable` instead of failing the whole column).
* **Treat "missing" as a single, explicit definition** (empty cell). Letting each library apply its own default
  NA list would have made the three engines disagree.
* **Share the rules, not the implementation.** `common.py` holds the patterns and thresholds; each engine
  implements loading and statistics separately. When the engines agree, that is evidence; if one were calling
  the other's code it would be evidence of nothing.
* **Make heuristics overridable and visible.** `--group-by` exists because the key chooser is a heuristic, and
  the report prints which keys were chosen and when a grouping is a no-op.
* **Test on the messy real data early.** Two heuristics that looked fine on my reasoning failed on the real
  files and only showed up in the output:
  * `Total Interactions` in Facebook Posts holds values like `268,841`, so it was classed as categorical with 5,665 "categories" until the numeric rule learned thousands separators.
  * The first key rule chose `Video Length` as the Facebook Posts grouping key because it had the most distinct values under the cardinality cap, even though 82.8% of it is empty. Adding a coverage requirement (>= 95% filled) selected `Facebook_Id` instead.

  I only noticed both when reading the full-data output, not while testing on small samples, which is a
  reason to validate heuristics on the real files.

## 3. What stories emerge across the three platforms?

Within the limits of columns that can be compared (see `CROSS_DATASET.md`):

* **Paid and organic communication differ most in calls to action.** `cta_msg_type` is 57.3% of ads versus 13.3% of Facebook posts and 11.0% of tweets; fundraising CTAs are 22.9% versus 1.8% and 0.8%. Ads are built to get an action; posts are mostly advocacy and issue messaging.
* **Advocacy is stable across channels (54.9%, 54.9%, 56.4%)**, so the core message mix is similar even where the CTAs are not.
* **Twitter is the most negative of the three** by the attack flag (30.8% versus 27.2% ads and 21.7% Facebook posts) and has the largest counts on every engagement metric that can be paired, though the platforms do not measure shares/retweets or views the same way, so I would not rank them on that.
* **Volume is not engagement.** One Facebook page produces 47% of the posts yet has a mean of 172 likes per post; the third-largest page has 5,043. In ads, one advertiser is 22.5% of all rows.

I would treat these as descriptive leads, not findings: the flags are model-scored, the datasets cover different
populations (an ad library, a set of 21 pages, a set of tweets with no author column), and I did not test
anything statistically.

## 4. Could I hand this system to a colleague?

Mostly yes, with caveats I can name. Strengths: one command per file, the report states which dataset and which
grouping it used, results are machine-readable JSON plus CSV, and there is an automated cross-check
(`verify_agreement.py`). Weaknesses a colleague would hit:

* IDs that look numeric (`quoteId`, `inReplyToId`) get a meaningless mean. A schema override or an "ID-like" rule is the obvious next step.
* Mixed date formats are detected as dates but not normalised, so min/max of `Post Created` is unreliable.
* The thresholds (99%, 5%, 95%) are untested outside these three files.
* The Pandas and Polars versions read whole files into memory; the 500 MB Ads file ran on a 4 GB machine but a much larger file would need the streaming approach of the pure-Python version.

## 5. How did my opinion of the three tools evolve?

* **Pure Python** surprised me. Because I count distinct values per column once and compute statistics from
  those counts, it was as fast as Pandas on the 500 MB file (46.8 s vs 47.4 s; I did not measure memory). It is
  also the version where every rule is visible. The cost is that I had to write each statistic myself
  (weighted median, standard deviation from a count table).
* **Pandas** is the most convenient and the least predictable: its defaults (NA strings, dtype guessing,
  `low_memory`) are exactly what a dataset-agnostic tool must switch off, so the generalised script reads everything
  as text and does the typing itself, which removes most of what Pandas normally gives you.
* **Polars** was the fastest on every file (40.9 s, 0.14 s, 0.20 s) and its strictness was an advantage here: it
  refuses to guess, which fits type detection by explicit rules. Its semantics differ in small ways (for example
  `n_unique()` counts nulls), which I handled by computing the statistic from the non-null values in all three.
  The lazy `group_by` API read clearly.

My overall view: on a one-off analysis I would still reach for Pandas; for a reusable tool that must behave
identically on unseen files, the explicit rules matter more than the library, and Polars' strictness helped.
The result that mattered most is that three independent implementations produced identical numbers.

## 6. Use of AI tools

I used an AI assistant (Claude) to help write and debug the code and to draft this documentation, and I ran
every script myself on the real files. AI was most useful for generating the first version of each engine and
the verification script; it was not able to see the real data until the scripts were run, so the two
heuristic failures above (thousands separators, wrong grouping key) were caught by reading the real outputs,
not by the first draft. The AI's suggestions about tie-breaking and null handling also needed checking: the
engines only agreed after the rules were written down explicitly and verified by `verify_agreement.py`.

**Do AI tools produce dataset-specific code or code that generalizes?** In this project the AI was asked for a
dataset-agnostic design from the start, and it produced one: rules computed from the data instead of lists of
column names. I did not test what it would write if simply asked for "descriptive statistics for this file",
so I cannot say what its default is for Task 3; Milestone A (where the code was written for the one ads
file) suggests that without that instruction the output tends to name the file's columns. What I did observe is
that "generalized" code can still be wrong in a dataset-specific way. Both of its heuristics that failed
(thousands separators in `Total Interactions`; choosing a mostly-empty column as the grouping key) looked
reasonable in the code and only broke on a file with those particular properties. My conclusion is that AI tools
can produce the structure of a generalized solution quickly, but the thresholds and edge cases have to be
found by running it on every real dataset and reading the output.
