# Bug reports

Defects found in **live** Open Food Facts data by running this harness, each
with the query that surfaced it and steps to reproduce.

These must be real findings. Run the harness against live data, take what it
flags, verify it by hand against the product page, and write it up. A report
describing a defect nobody confirmed is worse than no report at all — it is
the one thing an interviewer can check in thirty seconds.

## The findings so far

| report | what it is | found by |
| --- | --- | --- |
| [001](001-nutrition-values-rescaled-by-package-size.md) | four records whose nutrition figures are scaled by their own package size | `impossible_macros`, `implausible_energy` |
| [002](002-energy-field-contradicts-its-own-record.md) | an energy value contradicted by two other fields in the same record | `implausible_energy`, `energy_macro_consistency` |
| [003](003-label-text-stored-as-graph-entities.md) | a fifth of the entity vocabulary is label text rather than entities | `unresolved_entities` |
| [004](004-two-defects-in-this-pipeline.md) | two defects in this harness, caught by its own rules | `duplicate_nodes`, a single-barcode run |

Use `TEMPLATE.md`. One file per finding.

```bash
python scripts/hunt.py                     # survey many slices, ranked by defects
cat reports/hunt/<term>/report.md          # the offending rows for one slice
python scripts/run_checks.py --barcodes <code>   # reproduce a single finding
```

Two kinds of report belong here, and the second is the more valuable one.

**A defect in the data.** `implausible_energy`, `energy_macro_consistency`
and `unresolved_entities` are the rules that surface these. Verify every one
against the product page before writing it up.

**A defect in this pipeline, found by its own checks.** `duplicate_nodes`
caught one: the loader keyed nodes on the raw tag slug, so one ingredient
became two nodes. The fix and its regression test are in the repository. A
harness that has never caught its own pipeline out has not been tested in
the case that matters most, so when it happens, write it down.
