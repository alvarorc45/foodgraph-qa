# Two defects in this pipeline, found by its own checks

| | |
| --- | --- |
| Found by | `duplicate_nodes`, `implausible_energy` via `--barcodes` |
| Date | 2026-11-22 |
| Severity | warn, then error |
| Status | both fixed, both with regression tests |

The other three reports describe defects in data this harness consumes. These
two were in the harness itself. They are written up for the same reason the
others are, and arguably a better one. A validation suite that has never
caught out the pipeline running it has not been tested in the case that
matters most.

---

## One ingredient stored as two nodes

`duplicate_nodes` fired in six of the seven slices completed in the first
sweep. It reported pairs of node ids that differ only in their separators.

```
| label      | name                    | copies | ids                                             |
| category   | queijos fantasia        | 2      | queijos fantasia | queijos-fantasia             |
| ingredient | hidrolizado de levadura | 2      | hidrolizado de levadura | hidrolizado-de-levadura |
```

Reading the raw payloads shows where each spelling came from.

```
7501011111028  "es:HIDROLIZADO DE LEVADURA"
7501011127012  "es:hidrolizado-de-levadura"
```

Two products, one substance, two spellings from the same source. Open Food
Facts emits the taxonomy slug for ingredients it recognises and the
contributor's raw text for the ones it does not, so both forms are legitimate
things for the API to send.

### Root cause

`_clean_tag` stripped the language prefix and lowercased the remainder, then
used the result directly as the node id. Lowercasing collapses `HIDROLIZADO`
and `hidrolizado`, but nothing collapsed the space against the hyphen, so the
loader produced `hidrolizado de levadura` and `hidrolizado-de-levadura` as two
distinct primary keys.

Neither node is invalid. Both have a name, both have edges, no constraint is
violated and nothing errors. The damage is that the ingredient's edges are
split between two nodes, so every count, every aggregate and every "which
products share this ingredient" query returns half the answer.

### Fix

Separators are canonicalised before the id is formed. Whitespace, underscores
and runs of hyphens all collapse to a single hyphen.

```python
slug = re.sub(r"[\s_:-]+", "-", tag.strip().lower()).strip("-")
```

The regression test is built from the pair above rather than from an invented
example, in `tests/test_ingest.py::test_separator_variants_collapse_to_one_id`.

After the fix, re-running the sweep over the same slices leaves
`duplicate_nodes` silent.

### A second defect found while fixing the first

The initial version of the fix treated everything before a colon as a
language prefix, which turned the tag `vitamin b:12` into the node `12`. The
check that caught it was the parsing test written minutes earlier in the same
change. The prefix is now only stripped when it is short and alphabetic.

### What the rule still earns

Separator canonicalisation removes this class of duplicate at the source, so
on live data the rule has become a regression sentinel. If a future change
breaks `_clean_tag` again, it fires across every slice at once.

It also does not catch everything. Accents, singular against plural and
genuine synonyms all still produce two nodes for one thing, since the rule
compares separators and case and nothing more. Closing that gap needs accent
folding and a synonym table, which is an entity-resolution step rather than a
string comparison.

---

## A single-barcode run broke the build

```
$ python scripts/run_checks.py --barcodes 7501003101761
Loaded 1/1 products (0 unusable) from barcodes

[ERROR] schema_drift: 1/8 — every requested field should still arrive;
        a field absent from 90%+ of payloads has been dropped upstream
```

Nothing was wrong with that product. `schema_drift` compares the fields the
pipeline requested against the fields the payloads actually carry, and reports
a field as dropped once it is missing from 90% or more of them. With one
payload in the run, any field that one product happens to lack is missing from
100%. This particular salsa contains no additives, so Open Food Facts omitted
`additives_tags` entirely, and the check concluded the API had stopped sending
it.

### Why it mattered more than it looks

`--barcodes <code>` is the reproduction step every report in this directory
tells a reader to run. Someone following report 002 would have seen a
build-breaking error that had nothing to do with the defect being
demonstrated, and the natural conclusion would have been that the harness is
unreliable.

### Fix

Below `DRIFT_MIN_PAYLOADS`, set at 20, the check declines to answer rather
than guessing.

```
[PASS] schema_drift: 0/8 — not checked: 1 payload(s) is below the 20 needed
       to tell a dropped field from one a product simply has nothing to put in
```

The threshold sits where a 90% absence stops being something a handful of
sparse records can produce between them. The description carries the reason,
because a green tick meaning "this run cannot tell you" reads as "the contract
holds" if it stays quiet about the difference.

The regression test uses the barcode that exposed it, in
`tests/test_checks_detect_defects.py::test_a_tiny_run_does_not_report_drift`.

---

## What these two have in common

Both defects produced output that looked reasonable. Two nodes where one
belonged is indistinguishable from two genuinely different ingredients unless
something compares their names, and a red build is indistinguishable from a
real failure unless someone checks the data behind it. Neither raised an
exception, and no amount of type checking or null checking would have found
either.

What found them was a rule written to be suspicious of a specific shape of
wrongness, run against data messy enough to contain it. That is also the
argument for `tests/test_checks_detect_defects.py`, where every rule has a
defect built to trip it. A rule nobody has ever seen fail is not evidence of
anything, and the day it needs to catch the pipeline that runs it is the day
that distinction stops being academic.
