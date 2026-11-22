# Label text stored as graph entities

| | |
| --- | --- |
| Found by | `unresolved_entities` |
| Date | 2026-11-22 |
| Severity | warn |
| Scope | 2513 of 12365 entity nodes across 18 slices |

One node in five in this graph is not an entity. Storage instructions,
marketing copy, nutrition-table headings, a manufacturer's postal address and
a health warning have all been loaded as ingredients, each becoming a node
that no other product will ever reference.

## What the check reported

```
[WARN] unresolved_entities: 102/579 — tags should resolve to the upstream taxonomy;
       one that kept a non-English prefix is raw contributor text
```

The rule fired in every one of the 18 slices that were swept. A sample of
what it surfaced, copied verbatim from the reports and truncated only where
marked.

| what it is | the node |
| --- | --- |
| storage instruction | `re close the container v onsumir the bread in a period of 2 or 3 days` |
| heating instruction | `10 segundos en microondas a mavima potena var en lugar fresco v seco` |
| marketing copy | `flora recomienda una alimentación variada y equilibrada en el marco de un estilo de vida saludable` |
| table heading | `información nutricional media por 100 g nutricional information average quantity per 100 g valeurs nutritionelles…` |
| company address | `cacia 3800 587 aveiro portugal geral fabridoce pt www fabridoce pt r ecoponto azul…` |
| health warning | `les personnes souffrant d'hypertension doivent éviter toute consommation excessive` |
| the word "ingredients" | `ingredientes` |
| allergen sentence | `contiene ingredientes de huevo` |
| two ingredients run together | `mono y digliceridos de acidos grasos dioxido de titanio` |
| a navigation breadcrumb, as a category | `condimentos y especias/salsas de cocina y bases > despensa/salsas` |

Alongside these sit shorter strings carrying character substitutions of the
kind text recognition produces, such as `casejnato-de-sqdio`,
`flsfato-de-potasio` and `azul-be-llante-fcf`. Nothing in the data records how
any of this was entered, so the mechanism is not claimed here.

## How the rule distinguishes them

Across every payload examined in this work, a tag that matched the upstream
taxonomy arrived prefixed `en:` while raw contributor text kept its original
language prefix. The rule reads that prefix. A node still carrying `es:`,
`fr:`, `de:` or `ar:` is one the source appears not to have resolved.

That pattern is inferred from the payloads sampled here rather than from
documented behaviour, which is why the rule carries `warn` severity and a
tolerance instead of failing a build.

A second signal corroborates it without relying on the first. Every offending
row in every report shows `referenced_by = 1`. A real ingredient is shared
between products, and sugar appears in hundreds. A sentence lifted off one
package appears exactly once, forever.

## Scale

| slice | unresolved | examined | share |
| --- | --- | --- | --- |
| `turron` | 284 | 718 | 39.6% |
| `caldo de pollo` | 161 | 439 | 36.7% |
| `pan sin gluten` | 186 | 701 | 26.5% |
| `protein bar` | 252 | 953 | 26.4% |
| `infusion` | 144 | 554 | 26.0% |
| `botana` | 207 | 823 | 25.2% |
| `energy drink` | 171 | 730 | 23.4% |
| `totopos` | 100 | 444 | 22.5% |
| `mole` | 166 | 743 | 22.3% |
| `salsa picante` | 123 | 551 | 22.3% |
| `especias` | 102 | 579 | 17.6% |
| `yogurt` | 106 | 624 | 17.0% |
| `aceite` | 130 | 780 | 16.7% |
| `jarabe` | 87 | 521 | 16.7% |
| `chiles` | 118 | 957 | 12.3% |
| `crisps` | 87 | 730 | 11.9% |
| `soup` | 75 | 967 | 7.8% |
| `breakfast cereal` | 14 | 551 | 2.5% |

Across all 18 slices, 2513 of 12365 entity nodes are unresolved text, a fifth
of the vocabulary. The spread between categories is a factor of sixteen.

## A control that did not support its hypothesis

The first twelve slices were searched with Spanish terms and showed 1808
unresolved nodes out of 7810, or 23.2%. Six further slices were run with
English search terms as a control, on the hypothesis that non-English
ingredient panels resolve worse against an English-keyed taxonomy. Those
returned 705 of 4555, or 15.5%.

The gap is real but the hypothesis does not survive the detail. `protein bar`
reached 26.4%, higher than nine of the twelve Spanish slices, and the two
ranges overlap almost completely.

The experiment was also confounded. The language of a search term does not
determine the language of the products it returns, so the two groups were
never cleanly separated in the first place. A sound version of this test would
partition products by their own language rather than by the query used to
find them.

What the data does support is narrower and still useful. The unresolved rate
varies enormously between categories, and the lowest figures belong to large,
heavily curated categories while the highest belong to niche and regional
ones. Whether curation effort is what drives that is a hypothesis this work
has not tested.

## A second defect in the same rows

The `lang` column frequently disagrees with the language of the text it is
attached to.

| `lang` | the text | actual language |
| --- | --- | --- |
| `ar` | `información nutricional media por 100 g…` | Spanish |
| `ar` | `in the beginning of the 21st century this delicious desert obtained the igp…` | English |
| `fr` | `sin gluten este litro de caldo natural de pollo ha sido elaborado…` | Spanish |
| `fr` | `benzoato de sodio al 0 02141 agitese bien antes de uso…` | Spanish |
| `es` | `felbontás után a csomagolás visszazárandó és az előírt körülmények közt tárolandó…` | Hungarian |

This is independent of the first finding and does not weaken it. The rule asks
whether a tag resolved to the taxonomy, and a tag that kept any non-English
prefix did not, whichever prefix it kept. A consumer trying to filter or
display by language, however, cannot rely on this field.

## How to reproduce

```bash
python scripts/hunt.py -n 100 --terms "turron" "caldo de pollo" "pan sin gluten"
cat reports/hunt/FINDINGS.md
```

Look under `unresolved_entities`. Rows are ordered by `referenced_by`
ascending, so the nodes referenced by a single product come first.

## Why it matters

These nodes are not inert. They inflate the apparent size of the ingredient
vocabulary by a fifth, so any count of distinct ingredients, any ranking by
frequency and any attempt to find products sharing an ingredient is computed
over a vocabulary that is one part noise. Nothing errors, and the noise looks
exactly like signal until someone reads an individual node.

For an entity-resolution pipeline the cost compounds. Matching this graph
against another source means attempting to match 2513 strings that correspond
to nothing, and a fuzzy matcher will eventually find partners for some of
them.

## Verified against the source

Read from the Open Food Facts API on 2026-11-22 through this harness. The
strings quoted above are node names as the loader stores them, which is the
tag with its language prefix removed and its separators turned back into
spaces. They appear in the per-slice reports under `reports/hunt/`, and the
original tags behind them are in the `raw_payload` rows of the same runs. The
counts come from the consolidated `FINDINGS.md`.
