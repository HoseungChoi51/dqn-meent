# Comparing methods and choosing finalists

Use **Compare results** to review experiments within the same problem, fidelity,
and study. Sort the results by their observed objective, measured cost, method,
seed, or completion status. Objective sorting respects whether the problem is
minimized or maximized; missing measurements stay last.

The best observed objective is the endpoint of that run. A method's matched-cost
summary uses observations at the shared cost shown for its comparison group.
These answer different questions, particularly when some runs have larger budgets
or stop early. Neither descriptive summary establishes statistical superiority.

Select runs for a side-by-side comparison of their algorithm parameters,
allocation, schedule, implementation, runtime, and inputs. A random seed identifies
a replicate; it does not by itself define a new method. The abbreviated method ID
identifies the comparison definition, and the complete procedure additionally pins
the allocation and completion requirements used for confirmation.

Mark individual runs or an entire method group as finalists. Saving records a
manual shortlist for that development study. It does not nominate a winner under
a statistical selection rule, freeze a confirmation protocol, or launch work.
You can revise or clear the shortlist. The command history preserves previous
choices, and stale edits must refresh before replacing a newer revision.

In **Studies → Define a new study → Fixed confirmation procedure**, use the saved
finalists in **Prototype experiments**. The picker shows their parameters and
budgets and allows additional controls. Multiple selected seed replicates of an
identical procedure supply one prototype. Procedures with different budgets or
other frozen settings remain distinct, even if their comparison method IDs match.

Set each finalist's main-run allocation before freezing: wall-clock cap per run,
evaluation-request limit, schedule horizon, and completion target. For DQN, the
schedule horizon controls exploration decay; increasing the time cap alone does
not lengthen this schedule. Evaluation-request completion defaults to the main
request limit. Other completion units retain their own explicit targets. The
form previews the maximum time across methods, problem instances, and fresh seeds
against the remaining campaign budget. Editing allocations does not reserve time
or run experiments. Increase the campaign budget separately if needed.

Review the fresh seeds, validation requirements, and comparison rule, then freeze
the study. **Schedule missing cells** queues its required experiments. Each starts
fresh with the prototype's captured code, parameters, runtime, and declared input
assets; it does not resume the prototype's checkpoint. No longer intermediate
prototype is required. The original experimental evidence and shortlist stay
unchanged. A saved shortlist whose source was extended must be refreshed in
**Compare results**, or the current source can be selected directly in the picker.

Frozen confirmation allocations cannot be edited. Create another study if the
main procedure must change. Rule-based development nominations also retain their
frozen procedures; use a manual finalist selection to define a different main-run
allocation. Differing budgets establish an explicitly allocated comparison, not
an equal-compute comparison. Report the budgets with the results.

An immutable allocation binding records the original source procedure and the
resource changes that define each final procedure. Result evidence bundles carry
this binding and source prototypes, and campaign memory exposes its compact
provenance to the manager.

For example, in the grating development campaign, comparison method `386c732e3a`
uses hill climbing with `restart_patience=16`, and `0cb4fd1ed0` uses
`restart_patience=32`. Their other comparison settings match. With strict
improvement acceptance, the first restarts after fewer consecutive unsuccessful
candidates; the second allows a longer local search. Exhausting the permuted
neighborhood can also cause a restart.

Developer note: shortlist revisions and their confirmation bindings are stored
locally as immutable records. The current portable evidence bundle exporter does
not include these records; exporting this selection provenance requires a future
extension to the bundle dependency graph.
