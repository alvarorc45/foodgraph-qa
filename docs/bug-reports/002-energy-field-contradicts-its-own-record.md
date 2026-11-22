# Energy field contradicts the rest of its own record

| | |
| --- | --- |
| Found by | `implausible_energy`, `energy_macro_consistency` |
| Date | 2026-11-22 |
| Severity | error |
| Record | `7501011127012` (Tostitos salsa verde) |

This record declares 1142.86 kcal per 100 g. Two other figures in the same
record both say the real value is around 478, and they agree with each other
to within six tenths of a kilocalorie. The energy field is wrong and the
record carries its own evidence of it.

## What the checks reported

```
[ERROR] implausible_energy: 1/47 — energy per 100 g must be between 0 and 900 kcal
| 7501011127012 | Tostitos salsa verde | 1142.85714285714 |

[WARN] energy_macro_consistency: 6/32 — declared energy should be within 30% of 4·protein + 4·carbs + 9·fat
| code          | energy_kcal_100g | kcal_from_macros | relative_error |
| 7501011127012 | 1142.85714285714 | 478.1            | 0.582          |
```

The product surfaced in three separate slices of the sweep, under the search
terms `especias`, `totopos` and `chiles`, with identical figures each time.

## Two witnesses inside one record

| source | kcal per 100 g |
| --- | --- |
| `energy-kcal_100g` | 1142.86 |
| `energy-kj_100g`, 2002.8 kJ ÷ 4.184 | 478.68 |
| 4(6.9) + 4(60.2) + 9(23.3) | 478.10 |

The kilojoule field and the macronutrients were entered independently of each
other and of the kilocalorie field, and they land half a kilocalorie apart.
For tortilla chips a figure near 478 kcal per 100 g is what the ingredients
would produce, since 23.3 g of fat and 60.2 g of carbohydrate cannot add up to
much else.

No external label is needed to show the defect. A single record contradicting
itself by a factor of 2.39 is sufficient on its own, which is what makes this
one easy to act on.

## Root cause not determined

The multiplier between the declared value and the two witnesses is 2.390. The
record gives both `quantity` and `serving_size` as 62 g, and neither 100 ÷ 62
(1.613) nor 62 ÷ 100 (0.62) comes near 2.390, so the package-size rescaling
described in report 001 does not explain this one.

One detail is worth recording without drawing a conclusion from it. The stored
value is exactly 8000 ÷ 7, a clean fraction rather than a rounded decimal,
which suggests a division by a basis of 7, 70 or 700 somewhere upstream. The
declared serving size rules out the obvious readings of that and nothing else
in the data settles it.

The energy figure is demonstrably wrong. How it came to be wrong is unknown,
and this report does not guess.

## A related oddity

`serving_size` and `quantity` both read 62 g, so the whole package is declared
as a single serving. That has no bearing on the energy defect and is noted
only because it is unusual.

## How to reproduce

```bash
python scripts/run_checks.py --barcodes 7501011127012
```

`implausible_energy` fails and `energy_macro_consistency` fails alongside it.
The second is the interesting one, since it prints the computed figure next to
the declared one.

## Why it matters

A serving is the entire 62 g pack, so a consumer reading this field reports
708.6 kcal where the record's own kilojoule value and macronutrients give
296.4, overstating the pack by 412 kcal.

The shape of the error also makes it survive the obvious defences. Range
checks on protein, carbohydrate and fat pass, because those three fields are
fine. A null check passes, because nothing is missing. Only a rule that reads
two fields together and asks whether they agree finds it.

## Verified against the source

The declared energy, the macronutrients and the package quantity were read
from the Open Food Facts API on 2026-11-22 through this harness and stored in
`raw_payload`. The kilojoule figure and the serving size were read from the
product page at `https://world.openfoodfacts.org/product/7501011127012` on the
same day, since `serving_size` is not among the fields this harness
requests.
