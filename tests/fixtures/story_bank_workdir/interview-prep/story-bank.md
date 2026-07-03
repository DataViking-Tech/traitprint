# Story bank

One STAR block per story.

### [cost] Warehouse migration under deadline

**Situation:** Redshift costs were ballooning as pipeline volume grew.
**Task:** I owned migrating the warehouse to BigQuery with zero downtime.
**Action:** I designed dual-writes, verified parity nightly, and cut over
column by column.
**Result:** Spend dropped 45 percent with zero downtime across six weeks.
**Reflection:** Dual-writes made the cutover boring — repeat that.
**Source:** Staff Engineer — Acme
**Best for questions about:** cost, migration, Python

### [incident] The silent pipeline failure

**Situation:** A schema change upstream silently nulled a revenue column.
**Task:** I had to find the failure and restore trust in the dashboard.
**Action:** I traced lineage, wrote column-level checks, and backfilled.
**Result:** Detection time for schema drift went from days to minutes.
**Source:** Staff Engineer — Acme
**Best for questions about:** debugging, data quality
