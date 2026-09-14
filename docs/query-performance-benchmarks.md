# Query Performance Benchmarks and Hypotheses

## Status

Recorded measurements are evidence from this machine and dataset. Unmeasured
optimization ideas remain hypotheses and are not project source of truth. Every
proposal must preserve query correctness and pass a benchmark before adoption.

The decision to adopt native DuckDB for the six analytical tables is recorded in
`docs/decisions/001-use-duckdb-for-analytics.md`.

## Current Evidence

- Both SQLite files are stored on the external SSD under
  `/Volumes/MyVolume/sts2-data/`.
- `spire.db` is about 16 GB and includes `raw_runs`.
- The frozen eval database is about 12 GB and excludes `raw_runs`, `sync_state`, and
  `sync_log`. The measured slow query already used this smaller database.
- The eval snapshot contains 659,515 runs, including 543,618 non-abandoned runs, and
  45,448,800 card-choice rows.
- Repeated card-offer/pick-rate baselines ranged from 273.442 to 340.383 seconds. It
  consistently exceeded the normal 75-second SQL timeout.
- Its query plan scans non-abandoned runs, repeatedly searches `run_card_choices` by
  `run_id`, joins cards by primary key, and creates temporary B-trees for grouping and
  ordering.
- The live database has no `sqlite_stat1`; the frozen eval database has 13 statistics
  rows and has already been analyzed.
- Current read-only connections use an approximately 2 MB SQLite page cache, disabled
  memory mapping, automatic temporary storage, and `DELETE` journal mode.

Low total CPU utilization is consistent with storage waits, but does not alone prove
that USB I/O is the only bottleneck. On a multi-core machine, one fully used core may
also appear as a small total CPU percentage.

## Constraints

- The external SSD has adequate capacity but is slower than the internal SSD.
- Internal SSD capacity is constrained, so moving the full 12–16 GB database is not a
  current option.
- The frozen eval database and its checksum must not be modified in place.
- Indexes over tens of millions of rows may consume several additional gigabytes and
  slow ingestion.

## Candidate Experiments

The seven optimization hypotheses discussed so far are:

1. Storage layout: internal versus external SSD and separating raw data.
2. Integer surrogate keys for run relationships.
3. Planner statistics through `ANALYZE` or `PRAGMA optimize`.
4. Composite and covering indexes matched to query shapes.
5. Denormalized analytical fact tables.
6. Pre-aggregated analytical tables.
7. Connection settings such as cache, memory mapping, and temporary storage.

DuckDB began as a separate engine-comparison experiment rather than one of these seven
SQLite optimization points. Its measured result and accepted decision appear below.

### Point 1: Storage Layout — Postponed Due to Internal SSD Capacity

Moving the analytical database to the internal SSD may reduce random-read latency, but
current internal capacity rules out moving the full 12 GB eval database. The eval
snapshot already separates ingestion-only data such as `raw_runs`; it is about 4 GB
smaller than the live database, yet the measured query still took five minutes.

A future hybrid could keep canonical/raw data on the external SSD and place only a
small aggregate serving database on the internal SSD.

### Point 2: Integer Run Keys

Replacing 64-character child-table run hashes with integer surrogate keys may shrink
indexes and speed joins:

```text
runs.internal_id INTEGER PRIMARY KEY
runs.run_hash TEXT UNIQUE
child tables reference internal_id
```

This requires a database rebuild and ingestion changes. It is a future schema design,
not an in-place quick fix.

### Point 3: Planner Statistics

The frozen eval database already has `sqlite_stat1`, so missing statistics do not
explain its measured five-minute query. The live database has not been analyzed.
Future sync/snapshot workflows should benchmark `PRAGMA optimize` or `ANALYZE` after
large data changes.

### Point 4: Covering and Composite Indexes

Test one candidate at a time. Possible indexes include:

```sql
CREATE INDEX idx_choices_run_card_picked
ON run_card_choices(run_id, card_id, was_picked);

CREATE INDEX idx_choices_card_picked_run
ON run_card_choices(card_id, was_picked, run_id);
```

The first favors filtering choices by run. The second favors card-level grouping and
pick-rate analysis. SQLite chooses indexes automatically; the model should not emit
`INDEXED BY`. Confirm the chosen plan with `EXPLAIN QUERY PLAN`, run `ANALYZE` or
`PRAGMA optimize`, and remove superseded indexes only after measurement.

Suggested indexes from other workloads, such as composite run filters or covering
indexes for final-deck and relic analyses, require their own representative queries.
There is no single best column order for every question.

`WITHOUT ROWID` is not ready for adoption because the current child tables have no
validated natural primary key and repeated card/relic events may be meaningful.

#### Supporting Query-Shape Experiment

Benchmark direct joins, subqueries, and aggregate-before-dimension-join forms against
the same expected rows. Rewriting `IN` as `JOIN` is not inherently faster because the
SQLite optimizer may produce the same plan.

Prompt examples may encourage better SQL, but cannot guarantee performance. A future
deterministic stage could inspect `EXPLAIN QUERY PLAN`, identify known expensive plans,
and return performance feedback to SQL generation. General automatic SQL optimization
is harder than SQL safety validation.

### Point 5: Denormalized Analytical Facts

Denormalized analytical fact tables could copy compact run dimensions such as
character, ascension, win, and abandonment status into card/relic facts. This removes
joins and simplifies generated SQL but increases storage and synchronization work.
Use compact identifiers rather than repeating long text values where possible.

### Point 6: Pre-Aggregated Analytical Tables

Consider small pre-aggregated tables maintained during ingestion, for example:

```text
card_choice_stats(
    build_id,
    character,
    ascension,
    card_id,
    offer_count,
    pick_count
)
```

These are analytical facts, not cached natural-language answers. They can serve common
questions while raw tables remain available for uncommon analyses. The aggregation
grain must be designed carefully to avoid incorrect results or uncontrolled dimension
growth.

### Point 7: Connection Settings

Benchmark fixed, program-controlled connection settings such as a larger
`cache_size`, non-zero `mmap_size`, and `temp_store=MEMORY`. These require no schema
change. Values must be selected against available RAM rather than copied blindly.

`WAL` may help concurrent reads and ingestion on the live database, but it is not
expected to materially accelerate one query against the frozen read-only snapshot.

### Optional: DuckDB Benchmark

DuckDB is a possible analytical-engine experiment because it uses vectorized,
multi-core execution. It may read the existing SQLite database without replacing the
ingestion system. Performance claims such as sub-second cold queries remain hypotheses
until measured on this external SSD and dataset.

### Experiment: DuckDB Engine and Storage on Q1

The exact fixed Q1 SQL was run once in three configurations. Query setup and native
table-import time were excluded from query execution time.

| Configuration | Storage read by query | Query time | Result hash matched? |
|---|---|---:|---|
| Python SQLite, vanilla PRAGMAs | Existing SQLite file | 477.869 s | Yes |
| DuckDB 1.5.5 with SQLite extension | Existing SQLite file | 27.001 s | Yes |
| DuckDB 1.5.5 with native tables | Native DuckDB file | 0.462 s | Yes |

The native test copied the full `runs`, `run_card_choices`, and `cards` tables. The
one-time import took 165.595 seconds and produced a 263,991,296-byte DuckDB file. All
three runs produced SHA-256 result hash
`e155af61c556a065394a5ac3cfe7af2179bfb4e9b6ae90002ea035583727c57d`.

This is a first-pass result, not a stable performance claim. SQLite ran first, so the
SQLite-extension run may have benefited from operating-system caching. The native query
ran immediately after import and may also have benefited from warm pages. Repeated,
controlled trials are required before selecting an engine.

The same single-run comparison was then completed for Q2–Q8. Each question ran in the
fixed order SQLite, DuckDB with the SQLite extension, then native DuckDB. Connections
were fresh, connection setup was excluded, and the per-query safety ceiling was 600
seconds. `REAL` casts were translated to `DOUBLE` for DuckDB because SQLite `REAL` is
64-bit while DuckDB `REAL` is 32-bit.

| Question | SQLite | DuckDB over SQLite | Native DuckDB | Native vs SQLite | Results matched? |
|---|---:|---:|---:|---:|---|
| Q2 | 4.166 s | 0.909 s | 0.012 s | 347x faster | Yes |
| Q3 | 71.376 s | 7.484 s | 0.145 s | 492x faster | Yes |
| Q4 | 5.201 s | 0.259 s | 0.004 s | 1,300x faster | Yes |
| Q5 | 235.233 s | 26.847 s | 0.610 s | 386x faster | Yes |
| Q6 | 0.468 s | 0.037 s | 0.004 s | 117x faster | Yes |
| Q7 | 70.995 s | 15.044 s | 0.086 s | 826x faster | Yes |
| Q8 | 42.635 s | 14.594 s | 0.150 s | 284x faster | Yes |

The native benchmark database now contains all six analytical tables: `runs`,
`run_cards`, `run_relics`, `run_card_choices`, `cards`, and `relics`. Importing the
three additional tables took 97.106 seconds. The resulting six-table file is
445,132,800 bytes. Combined with the initial Q1 setup, importing all six tables took
approximately 262.701 seconds.

These large differences establish that native DuckDB storage is materially better for
this workload. The fixed run order still prevents these values from being treated as
precise stable latency estimates.

## Benchmark Method

1. Use a disposable or newly versioned snapshot on the external SSD; never modify the
   frozen eval database in place.
2. Keep SQL semantics and expected result rows fixed.
3. Record the original `EXPLAIN QUERY PLAN`, elapsed time, CPU time, database size, and
   result rows.
4. Change one variable: query shape, one index, connection settings, analytical table,
   or engine.
5. Run multiple trials and label cold versus warm-cache conditions where possible.
6. Keep a change only when it preserves results and improves representative workloads,
   not only one hand-picked query.

## Connection-Setting Benchmark Evidence

### Environment

- Date: 2026-08-30.
- Dataset: `sts2-2026-07-29`.
- Database checksum:
  `0dbc74b8908636de7fb6fa12d0753d6666df61001f175ca3a96a151b78251fb3`.
- Database location: external SSD.
- Host memory: 16 GB.
- Baseline connection settings: `cache_size=-2000`, `mmap_size=0`,
  `temp_store=0`, `journal_mode=delete`.
- Timing includes SQLite execution and fetching all result rows. It excludes model
  calls, LangGraph, connection creation, and settings inspection.
- Every comparison checks a SHA-256 hash of the complete result rows.

These measurements are sensitive to macOS file caching and concurrent disk activity.
They are evidence from this machine, not universal SQLite performance claims.

### Fixed Benchmark Questions

| ID | Question | Main workload |
|---|---|---|
| Q1 | Which five cards were offered at least 500 times but had the lowest pick rate among completed, non-abandoned runs? | `run_card_choices` aggregation, join, grouping, sorting |
| Q2 | Which encounters killed the most Silent players at Ascension 10? | Indexed run filter, grouping, sorting |
| Q3 | Which relics appear in at least 500 completed Ironclad runs and have the highest win rate? | Run/relic joins, distinct, grouping, sorting |
| Q4 | How has Silent's non-abandoned win rate changed across game builds? | Indexed run filter and grouping |
| Q5 | Which cards are picked much more often in winning runs than losing runs? | Full card-choice/run join and conditional aggregation |
| Q6 | Do winning runs finish faster on average than completed losing runs? | Run scan and two-group aggregation |
| Q7 | Which cards are most commonly added before floor 10, excluding starter cards? | Final-deck/card join, filter, grouping, sorting |
| Q8 | Which rare Silent cards appear most often in winning final decks? | Run/final-deck/card joins and distinct counting |

Interpretation rules are fixed across profiles: killed runs exclude abandoned runs; Q3
uses distinct run/relic pairs; Q5 requires at least 500 winning and 500 losing offers;
Q7 uses `0 < floor_added < 10`; Q8 counts distinct winning runs.

### Fixed SQL Definitions

#### Q1

```sql
SELECT c.card_id, c.name,
       COUNT(*) AS offer_count,
       SUM(rc.was_picked) AS pick_count,
       CAST(SUM(rc.was_picked) AS REAL) / COUNT(*) AS pick_rate
FROM run_card_choices AS rc
JOIN cards AS c ON rc.card_id = c.card_id
WHERE rc.run_id IN (
    SELECT run_id FROM runs WHERE was_abandoned = 0
)
GROUP BY c.card_id, c.name
HAVING COUNT(*) >= 500
ORDER BY pick_rate ASC, offer_count DESC
LIMIT 5;
```

#### Q2

```sql
SELECT killed_by_encounter, COUNT(*) AS death_count
FROM runs
WHERE character = 'SILENT'
  AND ascension = 10
  AND win = 0
  AND was_abandoned = 0
  AND killed_by_encounter IS NOT NULL
GROUP BY killed_by_encounter
ORDER BY death_count DESC, killed_by_encounter ASC
LIMIT 10;
```

#### Q3

```sql
WITH relic_runs AS (
    SELECT DISTINCT rr.run_id, rr.relic_id, r.win
    FROM run_relics AS rr
    JOIN runs AS r ON r.run_id = rr.run_id
    WHERE r.character = 'IRONCLAD'
      AND r.was_abandoned = 0
)
SELECT rel.relic_id, rel.name,
       COUNT(*) AS run_count,
       AVG(CAST(rr.win AS REAL)) AS win_rate
FROM relic_runs AS rr
JOIN relics AS rel ON rel.relic_id = rr.relic_id
GROUP BY rel.relic_id, rel.name
HAVING COUNT(*) >= 500
ORDER BY win_rate DESC, run_count DESC, rel.relic_id ASC
LIMIT 10;
```

#### Q4

```sql
SELECT build_id,
       COUNT(*) AS run_count,
       AVG(CAST(win AS REAL)) AS win_rate
FROM runs
WHERE character = 'SILENT'
  AND was_abandoned = 0
GROUP BY build_id
ORDER BY build_id ASC;
```

#### Q5

```sql
SELECT c.card_id, c.name,
       SUM(CASE WHEN r.win = 1 THEN 1 ELSE 0 END) AS winning_offers,
       SUM(CASE WHEN r.win = 1 THEN rc.was_picked ELSE 0 END) AS winning_picks,
       SUM(CASE WHEN r.win = 0 THEN 1 ELSE 0 END) AS losing_offers,
       SUM(CASE WHEN r.win = 0 THEN rc.was_picked ELSE 0 END) AS losing_picks,
       CAST(SUM(CASE WHEN r.win = 1 THEN rc.was_picked ELSE 0 END) AS REAL)
           / SUM(CASE WHEN r.win = 1 THEN 1 ELSE 0 END) AS winning_pick_rate,
       CAST(SUM(CASE WHEN r.win = 0 THEN rc.was_picked ELSE 0 END) AS REAL)
           / SUM(CASE WHEN r.win = 0 THEN 1 ELSE 0 END) AS losing_pick_rate,
       CAST(SUM(CASE WHEN r.win = 1 THEN rc.was_picked ELSE 0 END) AS REAL)
           / SUM(CASE WHEN r.win = 1 THEN 1 ELSE 0 END)
       - CAST(SUM(CASE WHEN r.win = 0 THEN rc.was_picked ELSE 0 END) AS REAL)
           / SUM(CASE WHEN r.win = 0 THEN 1 ELSE 0 END) AS pick_rate_gap
FROM run_card_choices AS rc
JOIN runs AS r ON r.run_id = rc.run_id
JOIN cards AS c ON c.card_id = rc.card_id
WHERE r.was_abandoned = 0
GROUP BY c.card_id, c.name
HAVING SUM(CASE WHEN r.win = 1 THEN 1 ELSE 0 END) >= 500
   AND SUM(CASE WHEN r.win = 0 THEN 1 ELSE 0 END) >= 500
ORDER BY pick_rate_gap DESC, c.card_id ASC
LIMIT 10;
```

#### Q6

```sql
SELECT CASE win WHEN 1 THEN 'win' ELSE 'loss' END AS outcome,
       COUNT(*) AS run_count,
       AVG(CAST(run_time AS REAL)) AS average_run_time_seconds
FROM runs
WHERE was_abandoned = 0
  AND run_time IS NOT NULL
  AND run_time > 0
GROUP BY win
ORDER BY win DESC;
```

#### Q7

```sql
SELECT c.card_id, c.name, COUNT(*) AS copies_added
FROM run_cards AS rc
JOIN cards AS c ON c.card_id = rc.card_id
WHERE rc.floor_added > 0
  AND rc.floor_added < 10
GROUP BY c.card_id, c.name
ORDER BY copies_added DESC, c.card_id ASC
LIMIT 10;
```

#### Q8

```sql
SELECT c.card_id, c.name,
       COUNT(DISTINCT rc.run_id) AS winning_deck_count
FROM run_cards AS rc
JOIN runs AS r ON r.run_id = rc.run_id
JOIN cards AS c ON c.card_id = rc.card_id
WHERE r.character = 'SILENT'
  AND r.win = 1
  AND r.was_abandoned = 0
  AND c.color = 'silent'
  AND c.rarity = 'Rare'
GROUP BY c.card_id, c.name
ORDER BY winning_deck_count DESC, c.card_id ASC
LIMIT 10;
```

### Experiment 1: Memory Mapping on Q1

| Pair | Requested setting | Effective `mmap_size` | Runtime | Result |
|---|---:|---:|---:|---|
| A baseline | `mmap_size=0` | 0 | 340.383 s | Correct |
| A candidate | `mmap_size=2,000,000,000` | 2.000 GB | 311.492 s | Same rows |
| B baseline | `mmap_size=0` | 0 | 285.639 s | Correct |
| B candidate | `mmap_size=4,000,000,000` | 2.147 GB | 274.650 s | Same rows |

The Python SQLite build reports `MAX_MMAP_SIZE=0x7fff0000`, so the 4 GB request was
capped at about 2.147 GB. Baseline variation was larger than the candidate gains.
Conclusion: memory mapping remains inconclusive and is not adopted from this evidence.

### Experiment 2: 512 MB Page Cache

Candidate setting: `cache_size=-524288`. All other settings remained at baseline. A
fresh read-only connection was used for each trial. Test order alternated by question,
but each profile ran only once, so warm-cache order remains a major confounder.

| Question | Order | Baseline | 512 MB cache | Candidate change | Same result? |
|---|---|---:|---:|---:|---|
| Q1 | baseline → candidate | 273.442 s | 310.612 s | 13.6% slower | Yes |
| Q2 | candidate → baseline | 0.042 s | 0.943 s | 2135.2% slower | Yes |
| Q3 | baseline → candidate | 22.593 s | 4.886 s | 78.4% faster | Yes |
| Q4 | candidate → baseline | 0.061 s | 0.083 s | 35.9% slower | Yes |
| Q5 | baseline only | >1,200 s | Not run | Baseline exceeded safety boundary | N/A |
| Q6 | candidate → baseline | 0.196 s | 2.005 s | 920.8% slower | Yes |
| Q7 | baseline → candidate | 55.958 s | 38.693 s | 30.9% faster | Yes |
| Q8 | candidate → baseline | 2.692 s | 17.128 s | 536.2% slower | Yes |

Q5 was stopped after 20 minutes. Its candidate partner was skipped because a page-cache
change cannot plausibly bring a workload already above 1,200 seconds under the 75-second
production timeout.

The large apparent wins occurred when the candidate ran second; the large apparent
losses occurred when it ran first. Q1 was the exception: the candidate ran second and
was still slower. Conclusion: this single-trial experiment mostly measured warm-cache
ordering. A 512 MB page cache is not adopted from this evidence.

### Experiment 3: Repeated Warm-Cache Connection Profiles

Q3, Q7, and Q8 were warmed once, then each profile ran three times in a fixed,
pseudorandom order. Every trial opened a fresh read-only SQLite connection, matching
the production executor lifecycle. Values below are medians; percentages compare with
the baseline median for the same question.

| Profile | Changed setting |
|---|---|
| Baseline | None |
| 64 MB cache | `cache_size=-65536` |
| 512 MB cache | `cache_size=-524288` |
| 2 GB mapping | `mmap_size=2000000000` |
| Memory temp | `temp_store=MEMORY` |

| Question | Baseline | 64 MB cache | 512 MB cache | 2 GB mapping | Memory temp |
|---|---:|---:|---:|---:|---:|
| Q3 | 5.216 s | 4.714 s (-9.6%) | 5.307 s (+1.7%) | 4.534 s (-13.1%) | 3.771 s (-27.7%) |
| Q7 | 17.281 s | 22.710 s (+31.4%) | 19.293 s (+11.6%) | 11.448 s (-33.8%) | 19.542 s (+13.1%) |
| Q8 | 0.649 s | 0.700 s (+7.9%) | 0.774 s (+19.3%) | 0.632 s (-2.7%) | 0.648 s (-0.2%) |

Raw trial times:

| Question/profile | Trial times |
|---|---|
| Q3 baseline | 5.216, 8.317, 4.726 s |
| Q3 64 MB cache | 4.687, 4.786, 4.714 s |
| Q3 512 MB cache | 5.157, 5.307, 5.560 s |
| Q3 2 GB mapping | 4.524, 4.534, 6.350 s |
| Q3 memory temp | 3.771, 2.970, 4.042 s |
| Q7 baseline | 38.118, 17.281, 15.687 s |
| Q7 64 MB cache | 22.879, 22.710, 20.185 s |
| Q7 512 MB cache | 19.836, 19.283, 19.293 s |
| Q7 2 GB mapping | 11.791, 11.448, 9.907 s |
| Q7 memory temp | 19.542, 17.902, 20.124 s |
| Q8 baseline | 0.635, 0.828, 0.649 s |
| Q8 64 MB cache | 0.663, 0.700, 0.772 s |
| Q8 512 MB cache | 0.792, 0.701, 0.774 s |
| Q8 2 GB mapping | 0.625, 0.632, 0.642 s |
| Q8 memory temp | 0.648, 0.673, 0.639 s |

All result hashes matched within each question. Larger SQLite page caches provided no
consistent benefit for fresh per-request connections and are rejected for now. Memory
mapping helped Q7 consistently and was neutral-to-helpful on Q3/Q8. In-memory temporary
storage helped Q3, hurt Q7, and was neutral on Q8. The next candidate combines only
`mmap_size=2000000000` and `temp_store=MEMORY`, leaving the page cache at its default.

### Experiment 4: Combined Mapping and Memory-Temp Profile

Candidate settings:

```sql
PRAGMA mmap_size = 2000000000;
PRAGMA temp_store = MEMORY;
```

The default approximately 2 MB SQLite page cache remained unchanged. Q3, Q7, and Q8
ran three times each. Their medians are compared with the warm-cache baseline medians
from Experiment 3.

| Question | Candidate trials | Candidate median | Baseline median | Change |
|---|---|---:|---:|---:|
| Q3 | 19.442, 3.282, 2.979 s | 3.282 s | 5.216 s | 37.1% faster |
| Q7 | 25.956, 9.930, 10.001 s | 10.001 s | 17.281 s | 42.1% faster |
| Q8 | 13.491, 0.703, 0.643 s | 0.703 s | 0.649 s | 8.3% slower |

The first trial for each question was cold relative to that table set, while later
trials benefited from macOS file caching. All result hashes matched.

The expensive Q1 confirmation ran the candidate first and baseline second:

| Question | Order | Combined candidate | Baseline | Candidate change | Same result? |
|---|---|---:|---:|---:|---|
| Q1 | candidate → baseline | 354.714 s | 303.788 s | 16.8% slower | Yes |

### Connection-Setting Decision

Do not change the production connection settings from this experiment:

- Larger page caches were inconsistent and often slower for fresh connections.
- Memory mapping substantially helped Q7 but did not reliably improve Q1.
- In-memory temporary storage helped Q3 but did not produce a safe global profile.
- The combined profile improved warm Q3/Q7 medians but made the most expensive measured
  Q1 trial slower.
- Q5 remained above 20 minutes before any candidate test.

Connection settings alone cannot make the current raw analytical schema meet the
75-second production timeout across representative questions. The next useful
experiments are query-shape changes, targeted indexes on a disposable copy, or
pre-aggregated analytical tables. No benchmark setting was persisted to the database
or application configuration.

## Initial Experiment Order

1. Connection settings.
2. Query-shape benchmarks.
3. One covering index at a time.
4. Small pre-aggregated tables.
5. Integer-key and denormalized schema prototype.
6. DuckDB comparison.

This order is a working proposal, not a committed architecture decision.
