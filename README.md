# foodgraph-qa

A data-quality and graph-integrity harness for the [Open Food Facts](https://world.openfoodfacts.org) API.

It pulls products from a public REST API, loads them into a small knowledge
graph, and then tries to prove the result is wrong: broken relationships,
duplicated entities, impossible nutrition figures, fields the API quietly
stopped sending. Sixteen checks, run by `pytest` and by a CLI, with a report
at the end.

```
$ python scripts/run_checks.py --from-fixtures

Loaded 8/8 products (0 unusable) from fixtures

[PASS]  orphan_edges: 0/45 — every edge endpoint must resolve to an existing node
[PASS]  invalid_relationship_type: 0/45 — relationships must exist in the ontology …
[ALLOW] completeness: 8/50 — products should have a name and an energy value (tolerated: 16% ≤ 20%)
[ERROR] impossible_macros: 1/44 — macros must be non-negative and sum to at most 102 g per 100 g
…
```

Three states, not two. `PASS` means the rule found nothing. `ALLOW` means it
found real broken rows and decided there were too few to fail on — the build
stays green and the rows are still listed in the report. Collapsing those two
into one green tick is how a validation suite ends up hiding its own findings:
`0/1841` and `8/50` would print identically, and nobody re-runs the query to
find out which one they are looking at.

## Why

I wrote an Android calorie tracker that reads nutrition data from Open Food
Facts. It is a crowd-sourced database, so some of what comes back is wrong:
products with no category, the kilojoule figure pasted into the kilocalorie
field, the same ingredient filed under two slightly different names. None of
that raises an error. It just produces a calorie estimate that is quietly off
by a factor of four.

This is the harness I wanted for that: something that finds the bad data
*before* it reaches a user, and that can prove it still works.

## What it checks

Each rule is a SQL query returning the rows that violate it, so a failure
arrives with evidence attached rather than as a boolean.

### Graph integrity — is the graph well formed?

| Check | Severity | Catches |
| --- | --- | --- |
| `orphan_edges` | error | an edge whose source or target node was never loaded |
| `invalid_relationship_type` | error | `CONTAINS_INGREDIENT` pointing at a *brand* — both nodes real, the fact meaningless |
| `unknown_node_label` | error | an endpoint using a label the ontology never declared |
| `self_referencing_edges` | error | a node related to itself |
| `duplicate_nodes` | warn | `palm-oil` and `palm_oil` as two nodes, splitting every aggregate in half |
| `unreachable_nodes` | warn | nodes nothing points at — the fingerprint of a half-finished load |
| `missing_expected_relationship` | warn | products with no category or no brand |
| `unresolved_entities` | warn | raw contributor text entering the graph as if it were an entity |

### Data quality — is the data believable?

| Check | Severity | Catches |
| --- | --- | --- |
| `implausible_energy` | error | 3000 kcal per 100 g, which no food can physically be |
| `impossible_macros` | error | negative grams, or macros summing past 100 g in 100 g |
| `volume` | error | the run that requested 500 products, loaded 40 and committed anyway |
| `orphan_products` | error | a product with no record of the payload it came from |
| `energy_macro_consistency` | warn | declared energy that contradicts declared macros |
| `completeness` | warn | missing name or missing energy |
| `freshness` | warn | records nobody upstream has touched in years |
| `schema_drift` | error | a field the pipeline depends on that the API stopped sending |

The one worth singling out is **`energy_macro_consistency`**. Every column
passes on its own; only the relationship between them is wrong. It compares
the declared energy against `4·protein + 4·carbs + 9·fat` and flags the ones
that disagree, which is how you catch a kJ value in a kcal field, a
per-portion figure stored as per-100 g, or a misplaced decimal point — all of
which survive every single-column validation and then corrupt everything
computed downstream.

## What it found

Both of these came out of running the harness over live data, not out of
designing it, and they are two of the four written up with their evidence and
a reproduction in [`docs/bug-reports/`](docs/bug-reports/).

**A record that contradicts itself.** Barcode 7501011127012 declares
1143 kcal/100 g. Its own kJ field says 2002.8, which is 478.7 kcal, and
Atwater over its declared macros — 4(6.9) + 4(60.2) + 9(23.3) — gives 478.1.
Two independent figures inside the same record agree, and the kcal field is
out by a factor of 2.39. `implausible_energy` and `energy_macro_consistency`
flagged it separately. The record gives both `serving_size` and `quantity`
as 62 g, so one serving is the whole pack: 708.6 kcal read instead of 296.4,
an overstatement of 412 kcal. What produced the figure is not established —
it is not the package-size error above, whose factor does not fit here.

**A bug in this loader, found by its own check.** `duplicate_nodes` fired in
6 of 7 slices. Open Food Facts emits the taxonomy slug for ingredients it
recognises and the contributor's raw text for those it does not, so the same
substance arrived as `es:HIDROLIZADO DE LEVADURA` from one product and
`es:hidrolizado-de-levadura` from another. Keyed on the raw slug, the loader
made two nodes for one thing and split its edges between them. Nothing
errored. The fix canonicalises separators in `_clean_tag`; the regression
test is built from that real pair.

That second one is why the check exists. A validator that only ever
confirms other people's data is clean has not been tested in the one case
that matters — the case where the pipeline running it is the thing at fault.

## Proving the checks actually work

A validator that always returns PASS looks exactly like a validator that
works. So every rule has a test that builds a defect specifically to trip it
and asserts that the right check fails *and* reports the offending row —
[`tests/test_checks_detect_defects.py`](tests/test_checks_detect_defects.py).

To show that is not circular, sabotage a check and watch the suite catch it:

```diff
# src/foodgraph/checks/graph.py
- WHERE s.id IS NULL OR d.id IS NULL
+ WHERE 1 = 0
```

```
FAILED tests/test_checks_detect_defects.py::test_orphan_edge_is_detected
E   AssertionError: orphan_edges passed despite an injected defect — the check is blind.
E   [PASS] orphan_edges: 0/46 — every edge endpoint must resolve to an existing node

1 failed, 104 passed, 3 skipped
```

One blind check, one failing test, a message that says what went wrong.

## Running it

```bash
pip install -e ".[dev]"

pytest                                        # offline suite, no network
pytest -m live                                # contract tests against the real API
python scripts/run_checks.py --from-fixtures  # validate the curated set
python scripts/run_checks.py --search chocolate -n 50   # validate live data

# record real payloads to exercise the parser against them
python scripts/record_fixtures.py --search chocolate -n 10

python scripts/hunt.py                        # survey many slices, rank by defects
#   -> reports/hunt/FINDINGS.md: everything that fired, grouped by rule
```

`run_checks.py` validates one pull and gates on it. `hunt.py` answers the
other question — *where* in the dataset the bad data lives — by sweeping
several slices and ranking them. They are separate because popular products
are the best-curated ones: a clean report for "chocolate" says the community
looks after its flagship records, not that the dataset is sound.

`run_checks.py` exits non-zero when an **error**-severity check fails, so it
can gate a pipeline. Warnings are reported and do not break the build: on
crowd-sourced data there is always a tail of implausible records, and a job
that goes red every morning for something nobody can fix is a job whose alerts
get ignored within a week.

## Design decisions worth explaining

**The offline suite never touches the network.** Tests that can fail because
someone else's server is slow stop being evidence of anything, and a suite
people re-run until it goes green has no value. Live tests exist, carry the
`live` marker and are excluded by default; CI runs them on a schedule, where a
failure means upstream changed rather than that someone broke the build.

**The edge table has no foreign keys.** That looks like a mistake until you
remember what it is for: if the database refused to store a broken edge, the
orphan-edge check could never find one and would be untestable. Real pipelines
load from upstream systems that offer no such guarantee. The loader is
permissive; the checks are strict.

**Raw payloads are kept.** Schema drift is detected against what the API
actually sent, not against the parsed columns — a field vanishing upstream and
a field the loader never read look identical once parsing has happened.

**The language prefix is kept, not discarded.** `en:sugar` becomes the node
`sugar`, but the `en` is stored alongside it. Across the payloads sampled
here, every tag that matched the taxonomy arrived as `en:` and every piece of
raw contributor text kept its original prefix, so the surviving prefix reads
as a verdict from upstream on whether that entity resolved. That pattern is
inferred from observed payloads, not from documented behaviour, and the rule
is a WARN for exactly that reason. `unresolved_entities` reads it, and what it surfaces is not ingredients at
all: storage and heating instructions, marketing copy, nutrition-table
headings, a manufacturer's postal address and website, and health warnings —
each one a node nothing will ever join to. Many carry character
substitutions (`casejnato-de-sqdio`, `flsfato-de-potasio`) of the kind text
recognition produces, though nothing in the data says how they were
entered. An earlier version of the
loader dropped the prefix as noise, which threw the signal away and left free
text indistinguishable from a canonical entity.

**Schema drift declines to answer on a small run.** Below twenty payloads
the check reports "not checked" rather than a verdict. The API omits a field
when a product has nothing to put in it, so in a one-product run any field
that product lacks is absent from 100% of payloads — which made a
single-barcode reproduction fail the build on sound data. A green tick that
means "cannot tell" says so in its own description, because one that stays
silent reads as "the contract holds".

**Schema drift is counted per field, not per product.** Open Food Facts omits
a field entirely when it is empty for a product, so one product without
`additives_tags` is a product with no additives — not a schema change. Counting
offending *products* mixes the two cases, and the only way to keep the common
one quiet is a tolerance so wide that real drift fits inside it. Counting
fields separates them: scattered absences are empty values, a field missing
from 90%+ of payloads has been dropped upstream, and that fails the build.

**The thresholds are asserted too.** `MAX_KCAL_100G` and friends are the
judgement in this harness, so `test_thresholds_are_physically_sensible` pins
them down. Relaxing a limit to silence an alert should fail a build, not pass
review as a config tweak.

**One `CheckResult` shape.** pytest asserts on it, the report renders it, CI
gates on it. Writing the rules once is the point: if the tests and the
monitoring kept separate definitions, they would drift, and the monitoring
would be the one that stops being trusted.

## Layout

```
src/foodgraph/
  client.py        REST client: retries, Retry-After, rate limiting
  ontology.py      which relationships are legal, and between which labels
  schema.sql       nodes, typed edges, raw payloads, run history
  ingest.py        payload -> nodes + edges -> SQLite
  checks/graph.py    structural integrity
  checks/quality.py  completeness, volume, freshness, drift, anomalies
  report.py        markdown + HTML + exit code
tests/             offline suite, defect-injection suite, live contract tests
scripts/           run_checks.py, hunt.py, record_fixtures.py
docs/bug-reports/  defects found in live data, written up and reproducible
```

## Two kinds of fixture, and why they are kept apart

| File | What it is | What it answers |
| --- | --- | --- |
| `products.json` | curated, clean by construction | does a *good* load pass every check? |
| `recorded_sample.json` | real API payloads, warts and all | does the parser survive what the API *actually* sends? |

Mixing them would break both. If real-world records went into the curated
set, the suite would go red the moment someone in the Open Food Facts
community saved a product with a typo — a red build that says nothing about
this code. And if the curated set stood in for real payloads, the parser
would only ever be proven against data shaped exactly the way it expects.

So the recorded sample is only ever asserted on for *parsing and loading*,
never for being clean. Finding defects in it is the point of the project, not
a test failure. `recorded_sample.json` is optional and its tests skip when it
is absent, so a fresh clone is green.

Neither file is re-fetched by the test run. Refreshing is a deliberate act
with a visible diff: if tests silently re-fetched their own inputs, a
breaking upstream change would rewrite the expectations and the suite would
stay green straight through it.

The fixtures are themselves validated, in `tests/test_fixture_contract.py`.
Several tests only mean something if the data has certain properties — you
cannot prove a shared ingredient becomes one node if no ingredient is shared
— so shrinking the fixture set produces a clear failure instead of tests that
still pass while checking nothing.

## Notes

Open Food Facts is volunteer-run and asks callers to identify themselves and
not to hammer it. The client sends a `User-Agent`, honours `Retry-After` and
rate-limits itself; please keep that if you fork this.

Data from Open Food Facts is available under the
[Open Database License](https://opendatacommons.org/licenses/odbl/1-0/).
