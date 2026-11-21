-- 0101_spend_ledger_attempts (owner decision 2026-11-21 on the outage drill report; BUILD-SPEC.md 6.1, 15.2; CLAUDE.md rules 5, 8, 10).
--
-- A model call that FAILED costs us nothing we can know (the provider's own bill is unknown to us), so it records no money. But the attempt itself is a fact: how many times the gateway called a provider on a route, and
-- whether the route answered. ops.spend_ledger is append-only and already carries one row per call; two columns let a failed route leave a row with `usd_actual = 0` and a count:
--
--   attempts  how many calls the gateway made on that route for this request (1 for a call that answered at once; 3 for a route that timed out twice and then answered, or failed all three tries)
--   outcome   'answered' (the route returned an answer) or 'failed' (every try failed; no money is recorded)
--
-- Every existing row, and every writer that does not name the columns (the session spend function of 0075), gets attempts = 1 and outcome = 'answered': what they have always meant. `usd_actual >= 0` already allows the zero of a failed
-- row, and the day's SUM is therefore unchanged by a failed row. The append-only triggers, the forced row-level security and the worker-only insert policy are untouched.
--
-- NEW GRANTS (ledger): column-level INSERT on ops.spend_ledger (attempts, outcome) to careflow_app, the same shape as the existing column grants. No new table, policy or function. Not an edit of a merged migration: a new one.

alter table ops.spend_ledger add column attempts integer not null default 1 check (attempts between 1 and 20);
alter table ops.spend_ledger add column outcome text not null default 'answered' check (outcome in ('answered', 'failed'));
-- a failed try has no cost and no shadow cost: the database refuses a failed row that claims money
alter table ops.spend_ledger add constraint spend_ledger_failed_has_no_cost check (outcome = 'answered' or (usd_actual = 0 and usd_shadow = 0));

-- failed attempts per clinic and day are read by the cost canvas and the drills: a small partial index
create index spend_ledger_failed_idx on ops.spend_ledger (clinic_id, day) where outcome = 'failed';

grant insert (attempts, outcome) on ops.spend_ledger to careflow_app;

do $$
begin
    if not has_column_privilege('careflow_app', 'ops.spend_ledger', 'attempts', 'INSERT') or not has_column_privilege('careflow_app', 'ops.spend_ledger', 'outcome', 'INSERT') then
        raise exception '0101: the application role must be able to insert the attempts and outcome columns';
    end if;
    if has_column_privilege('careflow_app', 'ops.spend_ledger', 'attempts', 'UPDATE') or has_table_privilege('careflow_app', 'ops.spend_ledger', 'UPDATE') then
        raise exception '0101: the spend ledger must stay append-only for the application role';
    end if;
end $$;
