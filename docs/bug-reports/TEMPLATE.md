# <short description of the defect>

- **Found by:** `<check name>`
- **Date:** YYYY-MM-DD
- **Severity:** error | warn
- **Affected record(s):** barcode(s), with links to the product page

## What the check reported

```
<the row the harness printed, verbatim>
```

## Expected vs actual

| Field | Expected | Actual |
| --- | --- | --- |
|  |  |  |

## How to reproduce

```bash
python scripts/run_checks.py --barcodes <code>
```

<State what to look for in the output.>

## Why it matters

<What breaks downstream. For a calorie tracker: the estimate is wrong by X,
and nothing errors, so the user never finds out.>

## Verified against the source

<Link to the Open Food Facts product page and what it shows, so the finding
can be confirmed independently of this harness.>
