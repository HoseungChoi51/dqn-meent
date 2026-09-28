# Monitoring a confirmation study

Open **Studies** to follow the study's run roster. **Current study** identifies the
selected scientific scope; it does not mean experiments have started. The progress
panel distinguishes unscheduled, queued, running, paused, failed and finished work.

Before launching, review the frozen allocation and the campaign's remaining
budget. **Schedule missing cells** queues the declared method × instance × seed
runs. It does not change their limits. The budget is aggregate worker time, so
concurrent runs each consume their own allowance. The sum of their caps is not an
estimate of elapsed time. Validation and independent diagnostic jobs may require
additional resources.

The monitor refreshes through workspace events and polling. Each run shows its
request count, completion target, measured worker time, allocation and latest
worker-report timestamp. A delayed report is a signal to inspect the run, not
proof that it has failed. The displayed counters are the latest saved observations;
the interface does not manufacture progress between reports.

Scientific completion and execution status are separate. A stopped or finished
process may not have reached its required target. Actual evaluation requests may
also exceed confirmed trajectory progress after recovery or interrupted requests.
Required checks and evidence publication can remain pending after optimization
finishes. Review these separately before releasing confirmation evidence.

Follow an individual run link for its detail view. **Experiments** defaults to the
current study and offers an **All studies** filter. Run links can be bookmarked;
the detail view requests only the latest 20 observation records, preserving their
recorded order, rather than repeatedly downloading the entire trajectory.

To correct an allocation after freezing, create a linked study and preserve the
original record. Increase the campaign budget separately when the revised roster
requires more resources. Merely opening the monitoring interface never changes a
budget, schedules a run, or invokes a model.
