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

Review the fresh seeds, validation requirements, and comparison rule before
freezing the study. Confirmation copies the selected procedure's budget. Loading
a 45-second prototype does not turn it into a longer experiment. Define the desired
longer procedure first if the final study needs a different allocation. After the
study is frozen, **Schedule missing cells** queues its required experiments.

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
