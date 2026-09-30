---
name: new-migration
description: Create a new checksummed, append-only database migration the correct way. Use when adding, changing, or fixing schema — never by editing an existing migration file.
disable-model-invocation: true
argument-hint: "short description of the change"
---

Create a new migration for: $ARGUMENTS

Follow BUILD-SPEC.md §17.1 exactly:

1. Find the next migration number: !`ls db/migrations 2>/dev/null | sort | tail -1`
2. Create a new file `db/migrations/<next-number>_<short-name>.sql` — never edit an existing one,
   even to fix a typo in it. If an earlier migration was wrong, this new migration corrects it.
3. If the change touches a clinic-scoped table, include the row-level security policy in the same
   migration that creates the table — never add a table now and RLS later.
4. If the change touches a table with no `clinic_id` column, add the parent-scoped RLS policy per
   BUILD-SPEC.md §16.2/§17.1 (child inherits isolation through its parent), not a bare grant.
5. If the change adds or modifies clinical domain data, include `domain_tag`, `version_id`,
   `is_current`, `valid_from`, `valid_to` per §9.5 — never a plain overwritable column for
   anything in the clinical record.
6. Run the migration checksummer/ledger tool against the new file before considering this done.
7. Add or update the origin-tag column/constraint (`live`/`test`/`eval`/`canary`) if this table
   holds durable state, per the rule in BUILD-SPEC.md §0.
