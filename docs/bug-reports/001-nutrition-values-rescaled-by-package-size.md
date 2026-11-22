# Nutrition values rescaled by package size

| | |
| --- | --- |
| Found by | `impossible_macros`, `implausible_energy` |
| Missed by | `energy_macro_consistency` |
| Date | 2026-11-22 |
| Severity | error |
| Records | `0041331029056`, `0044774108005`, `0044774401007`, `7501003101761` |

Four products carry nutrition figures that cannot describe food. Three of them
declare more than 100 g of macronutrients in 100 g of product, the worst at
318.89 g, and all four declare energy above the physical ceiling of roughly
900 kcal per 100 g. The figures are not random. In every case the declared
values are a plausible set multiplied by a factor that matches the product's
own `quantity` field exactly, which points at the per-100 g normalisation
rather than at a contributor typing numbers in wrong.

## What the checks reported

```
[ERROR] impossible_macros: 7/94 — macros must be non-negative and sum to at most 102 g per 100 g

| code          | name                   | proteins | carbohydrates | fat    | macro_sum |
| 0041331029056 | Mole                   | 17.294   | 103.794       | 60.559 | 181.65    |
| 0044774108005 | Teloloapan Red Mole    | 25.5     | 165.821       | 127.536| 318.86    |
| 0044774401007 | Mole Mexican Condiment | 63.786   | 89.286        | 165.821| 318.89    |

[ERROR] implausible_energy: 3/94 — energy per 100 g must be between 0 and 900 kcal

| code          | energy_kcal_100g |
| 0041331029056 | 1029.38235294118 |
| 0044774108005 | 1913.10714285714 |
| 0044774401007 | 2104.67857142857 |
```

The fourth record came from a different slice of the dataset.

```
[ERROR] implausible_energy: 1/96
| 7501003101761 | Salsa de cacahuate picante cremosa | 1688.0 |
```

## The arithmetic

Energy and macronutrients are related by the Atwater factors, 4 kcal per gram
of protein, 4 per gram of carbohydrate and 9 per gram of fat. Applying them to
the three mole records reproduces the declared energy to the last digit the
API returns.

| code | 4·P + 4·C + 9·F | declared energy |
| --- | --- | --- |
| `0041331029056` | 1029.38235294 | 1029.38235294118 |
| `0044774108005` | 1913.10714286 | 1913.10714285714 |
| `0044774401007` | 2104.67857143 | 2104.67857142857 |

An agreement that exact is not a coincidence between two independently entered
numbers. The energy was computed from the macronutrients, so whatever is wrong
with the macronutrients the energy has inherited.

What is wrong with them turns out to be a single multiplier, and it is the
product's own package size.

| code | `quantity` | declared ÷ plausible | 100 ÷ quantity |
| --- | --- | --- | --- |
| `0041331029056` | 34 g | 2.9411765 | 2.9411765 |
| `0044774108005` | 28 g | 3.5714286 | 3.5714286 |
| `0044774401007` | 28 g | 3.5714286 | 3.5714286 |

Dividing each declared value by that factor returns figures that fit inside
100 g of food.

| code | protein | carbohydrate | fat | sum | energy |
| --- | --- | --- | --- | --- | --- |
| `0041331029056` | 5.88 | 35.29 | 20.59 | 61.76 | 350.00 |
| `0044774108005` | 7.14 | 46.43 | 35.71 | 89.28 | 535.67 |
| `0044774401007` | 17.86 | 25.00 | 46.43 | 89.29 | 589.31 |

The fourth record runs the same mechanism backwards. Its macronutrients are
untouched and only the energy is scaled, this time upwards by `quantity ÷ 100`
rather than downwards.

```
4(12.667) + 4(26.667) + 9(60.667) = 703.33 kcal
703.33 × (240 ÷ 100)              = 1688.0   <- the declared value
```

With a 240 g jar, 1688 kcal is the energy of the whole container sitting in a
field that is supposed to hold the energy of 100 g.

The exact match between the observed factor and `quantity` is the verified
part. The interpretation below it, that figures already expressed per 100 g
were normalised to 100 g a second time, is the explanation that fits and has
not been confirmed against the edit history of these records.

## Why `energy_macro_consistency` says nothing

The three mole records pass the cross-field check, and they pass it because
they are wrong in a way that check cannot see. Comparing declared energy
against energy computed from the declared macronutrients tests a *ratio*, and
a multiplier applied to every field leaves the ratio alone. In the slice where
those three records live, `energy_macro_consistency` reported a single
offender and it was a different product entirely.

Only the absolute bounds caught them. A gram of food cannot contain more than
a gram of macronutrients and no food exceeds about 900 kcal per 100 g, so
`impossible_macros` and `implausible_energy` have nothing to cancel out.

The fourth record demonstrates the reverse. Its macronutrients sum to 100.0 g,
two grams inside the `MACRO_SUM_LIMIT` threshold, so `impossible_macros` lets
it through, while the scaled energy breaks the ratio and
`energy_macro_consistency` catches it.

Three rules, three different failure modes, and no two of them catch the same
set. That is the argument for absolute bounds and cross-field consistency
living side by side rather than one standing in for the other.

## How to reproduce

```bash
python scripts/run_checks.py --barcodes 0041331029056 0044774108005 0044774401007
python scripts/run_checks.py --barcodes 7501003101761
```

In the first run `impossible_macros` and `implausible_energy` fail while
`energy_macro_consistency` passes. In the second the pattern inverts, with
`impossible_macros` passing and `energy_macro_consistency` failing.

## Why it matters

A calorie tracker reading `energy_kcal_100g` for `0044774401007` would report
589 kcal for a 28 g sachet instead of 165. Nothing in the API response signals
a problem. Every field is a well-formed number of the right type, the record
is internally consistent, and a consumer that validates types, ranges per
field or cross-field ratios in isolation accepts all four records without
complaint.

The scaling also makes the error silent in aggregate. Any mean or total
computed over a category containing these products is pulled upwards by
records that look no different from the rest.

## Verified against the source

Values read from the Open Food Facts API on 2026-11-22 through this harness
and stored verbatim in the `raw_payload` table. The `quantity` figures come
from the same payloads. Product pages are at
`https://world.openfoodfacts.org/product/<barcode>`.

## Not investigated

A fifth record, `0850025393232` (Totopos chips), was flagged by
`impossible_macros` with a macronutrient sum of 103.57 g. Its package size was
not examined and it is not claimed here to share this root cause.
