# Bug reports

Defects found in **live** Open Food Facts data by running this harness, each
with the query that surfaced it and steps to reproduce.

These must be real findings. Run the harness against live data, take what it
flags, verify it by hand against the product page, and write it up. A report
describing a defect nobody confirmed is worse than no report at all — it is
the one thing an interviewer can check in thirty seconds.

Use `TEMPLATE.md`. One file per finding.

```bash
python scripts/run_checks.py --search "chocolate" -n 50
python scripts/run_checks.py --search "aceite de oliva" -n 50
cat reports/report.md
```

`energy_macro_consistency`, `implausible_energy` and `duplicate_nodes` are the
ones that tend to produce real findings on a sample of a few dozen products.
