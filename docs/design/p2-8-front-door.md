# P2-8 - the Front Door graph

Code: `careflow/front_door.py`, `careflow/graph_structure.py`. Tests: `tests/test_front_door_structure.py` (HARD CI GATE, offline) and
`tests/test_front_door.py` (behaviour: crash/resume, rules-before-model, nothing approved by default). Branch `p2-8-graph`, stacked on `p2-3-docs`.

## Shape
`screen -> resolve_identity -> match_protocol -> propose_triage -> review (PAUSE) -> apply_review`. Routing is deterministic code (two functions that read
only the state). A red flag, an unassessable screening, a failed extraction or unverified facts END the run for a human. The only pause is `review`;
the only effect before it is `propose_triage` (the step immediately before the pause, SS7.2). `apply_review` records the decision after resume.

## The structural gate
Each node has a `NodeSpec` (effects, pauses, models). `graph_structure.violations` reads the node SOURCE: which `deps.<port>` it touches (every port
must be classified as side-effect, model or neutral), whether it calls `interrupt`. It fails on: a pause with an effect; a declared effect the source does not
show (and the reverse); an unclassified port; a node without a declaration. The test also pins the compiled topology edge-for-edge, the agent inventory
(one `StateGraph` in the whole package; a feature flag ON for an agent that is not built raises), and proves the checker catches deliberately bad nodes.

## What is checkpointed
Ids, fixed-word statuses, retrieval candidates as (card id, version), a proposal id. NO letter text, NO extracted name/DOB/phone, NO query (a test greps the
saved checkpoint bytes). The extractor, grounding and identity write share ONE node so extracted PII never needs to cross a checkpoint; the cost is that a crash
inside that node re-runs the (budgeted) model call, while the identity write is idempotent (`referral:<document id>`).

## Decisions (safety-neutral, noted)
- `resolve_identity` screens again before it will call the extractor: rules first on EVERY path including a resume after a crash; the screen is cheap and its escalation is idempotent.
- The run is invoked with `recursion_limit = 40` (LangGraph's default is 1000; SS7.3 says 40). Thread id `front_door:<document id>`.
- Hosted tracing is refused: `langgraph` pulls in `langsmith`, which would send graph inputs to a hosted service if `LANGSMITH_TRACING`/`LANGCHAIN_TRACING_V2` were on (rule 7). `build_front_door` raises if either is set.
- `langgraph` (1.2.17) was added: on the approved list, local library, no external call. It brought 23 transitive packages (langchain-core, langsmith, ormsgpack, ...), pure Python, a few tens of MB of disk, no model weights.
- LangGraph 1.2 raises `UnboundLocalError` for `Command(resume=None)`; the test only requires that nothing is recorded.

## The checkpointer driver (NOT added; for the owner to decide)
Finding (Context7, LangGraph docs): the official Postgres checkpointer `langgraph-checkpoint-postgres` (`PostgresSaver` / `AsyncPostgresSaver`) is built on
**psycopg 3** (`pip install "psycopg[binary,pool]" langgraph-checkpoint-postgres`), not asyncpg. Our stack is asyncpg + SQLAlchemy. Other facts from the docs: it
creates its own tables via `setup()` (outside our checksummed migration ledger, and with no RLS/origin/clinic scoping); a custom checkpointer must implement five
async methods (`aput`, `aput_writes`, `aget_tuple`, `alist`, `adelete_thread`) and must store metadata in full and use `self.serde`; durability modes are `exit`,
`async`, `sync` (only `sync` writes every checkpoint before the next step starts, which crash-resume needs).
Options:
1. **Add psycopg + the official saver.** New driver and a second connection stack to Supabase (session pooler, not the transaction pooler, or prepared statements must be off); tables created by the library, not by our migrations; no RLS unless we add it afterwards; least code.
2. **Write our own asyncpg `BaseCheckpointSaver`** on tables from our own migration (forced RLS, origin tag, checksummed ledger), about 300 lines plus tests; no new driver; we own the serde-compatibility risk across LangGraph upgrades.
3. **Official saver in a dedicated schema with grants limited to a dedicated role.** Fastest, but RLS still absent and a second role/connection.
Recommendation: option 2 (it keeps rules 8 and 10 intact), unless you prefer to accept option 1's deviations. The graph takes any `BaseCheckpointSaver`, so nothing here changes.

## Not here
P2-9 (the real `TriageProposer`: proposal, review task, assisted-mode rules, priorities blocked on clinicians), P2-10 (end-to-end), the checkpointer table/migration, a route or worker that starts a run (and its role-matrix case), the `TextSource` over P2-4.

## Review fixes (clinical-content-reviewer, BLOCKED then fixed)
- The structural checker now FAILS CLOSED (`graph_structure.py`): `deps` may appear only as `deps.<name>` (no alias, `getattr`, `vars`, or passing `deps` to a helper); no closure variable but `deps`; `interrupt` is found through an import alias or a helper function; `Command`/`GraphInterrupt` are refused; a node that pauses may touch NO port at all (not even `session`); routers are checked by `router_violations` (state only). Each has a negative test.
- `match_protocol` re-screens before the retriever (a resume after a crash lands there without passing `resolve_identity`); a red flag found at that point ends the run.
- Tracing is refused at every node as well as at build, and `LANGSMITH_TRACING`/`LANGCHAIN_TRACING_V2` are pinned `false` when unset; `langsmith-tracing-disabled` is in the data-flow register as retired.
- `abstain_reason` only takes a value from a fixed vocabulary.

## HARD PREREQUISITES for the route/worker packages (recorded, not built)
1. `held_unassessable`, `needs_front_desk`, `extraction_unavailable` end a run with a status only. Something must notice them (a review task, or a poller over thread state) before this graph is run for real; otherwise the loop closes silently (rule 3). Blocks P2-10 acceptance outside tests.
2. `reviewer_id` in the resume value is caller-asserted. The route that resumes must authenticate the reviewer, check clinic membership and the nurse/doctor role (with its role-matrix case, rule 8), and the real `ReviewRecorder` must take the reviewer from `auth.uid()`, not from the resume value (migration 0018 enforces `decided_by = auth.uid()`). `edit` carries no edited content yet.
3. The checkpointer decision above, before any non-test run.
