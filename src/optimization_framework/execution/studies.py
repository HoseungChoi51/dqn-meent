"""Expand frozen study cells into jobs owned by the ordinary workspace scheduler."""
import time

from optimization_framework.analysis.rules import check_design, evaluate, freeze as freeze_rule
from optimization_framework.analysis.studies import experiment_evidence
from optimization_framework.assets.references import validate_input
from optimization_framework.contracts.assets import ReuseDecision
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.experiments import StudySpec
from optimization_framework.contracts.problems import ProblemInstance
from optimization_framework.contracts.requests import RecipeInput, TrialInput
from optimization_framework.contracts.resources import ExecutionGrant
from optimization_framework.contracts.templates import DeclaredInput, PrefixInput, StudyTemplateVersion, TemplateFreezeInput
from optimization_framework.evaluation.recipes import validation_policy
from optimization_framework.execution.provenance import archive, invoke_archive
from optimization_framework.execution.resources import ACTIVE
from optimization_framework.storage.sqlite import identifier, now


class StudyExecutionService:
    def __init__(self, workspace=None, *, store=None):
        self.workspace, self.store = workspace, workspace.store if workspace else store

    def freeze(self, campaign_id, request, *, authority="researcher"):
        request = request if isinstance(request, TemplateFreezeInput) else TemplateFreezeInput(**request)
        template = request.template
        with self.workspace.lock, self.store.transaction():
            campaign = self.store.get(campaign_id, "campaign")
            tasks = {row["id"]: row for row in self.workspace.current_tasks(campaign_id) if row["id"] in request.task_ids}
            if len(tasks) != len(request.task_ids):
                raise ValueError("Select distinct current problem instances from this campaign")
            for group in template.groups:
                if set(group.task_ids) - set(tasks):
                    raise ValueError("A template group selects a task outside this frozen execution")
                if group.scope == "development" and any(tasks[key]["split"] == "test" for key in group.task_ids or tasks):
                    raise ValueError("Protected test instances cannot supply development selection evidence")
            instances = [ProblemInstance(**row["problem"]) for row in tasks.values()]
            if set(request.asset_bindings) != set(template.input_requirements):
                raise ValueError("Bind every declared input requirement to a specific asset before freezing")
            inputs = {}
            for name, requirement in template.input_requirements.items():
                asset = self.store.get(request.asset_bindings[name], "asset")
                validate_input(self.workspace.assets, name, requirement, asset)
                inputs[name] = asset
            if template.confirmation_kind == "unseen_instance":
                targets = {tasks[key]["problem"]["scientific_identity"] for group in template.groups if group.scope == "confirmation"
                           for key in group.task_ids or tasks}
                if any(asset["exposure_status"] != "known" or targets.intersection(asset["exposed_instance_ids"]) for asset in inputs.values()):
                    raise ValueError("Declared historical inputs prevent this unseen-instance confirmation claim")
            source = archive(self.store, problem_ids=[task["problem"]["definition_id"] for task in tasks.values() if not task.get("evaluator_manifest")],
                recipe_ids={identity for task in tasks.values() for identity in (task.get("evaluator_manifest") or {}).get("recipe_ids", [])}, purpose="worker")
            normalized = {}
            for slot_id, slot in template.methods.items():
                if not slot.procedure:
                    continue
                procedure = slot.procedure
                definitions = []
                used_tasks = {key for group in template.groups if any(key == slot_id or slot_id in template.methods[key].select_from for key in group.slots)
                              for key in (group.task_ids or list(tasks))}
                if not used_tasks:
                    raise ValueError("A literal method must be used by a declared group or selected slot")
                for task_id in sorted(used_tasks):
                    task = tasks[task_id]
                    values = self._request(procedure.model_dump(mode="json"), campaign_id, task["id"], 0)
                    placeholder = []
                    if isinstance(procedure.input_binding, DeclaredInput):
                        placeholder = [inputs[procedure.input_binding.slot]]
                    elif isinstance(procedure.input_binding, PrefixInput):
                        instance = ProblemInstance(**task["problem"])
                        if procedure.algorithm != "refinement" or instance.candidate_schema.representation != "binary":
                            raise ValueError("This prefix input requires a registered solution-input optimizer")
                        placeholder = [{"kind": "solution", "payload": {"candidate": [1] * instance.candidate_schema.dimensions}}]
                    prepared = invoke_archive(self.store, source["id"], "trial.prepare",
                        {"request": values.model_dump(mode="json"), "problem": task["problem"], "assets": placeholder,
                            "evaluator_manifest": task.get("evaluator_manifest")})
                    parameters = dict(prepared["algorithm_config"])
                    if procedure.input_binding:
                        parameters.pop("initial_design", None)
                    input_binding = procedure.input_binding.model_dump(mode="json") if procedure.input_binding else None
                    if isinstance(procedure.input_binding, DeclaredInput):
                        asset = inputs[procedure.input_binding.slot]
                        input_binding.update(asset_id=asset["id"], asset_digest=asset["content_hash"])
                    definitions.append({"method_contract": 3, "algorithm": procedure.algorithm,
                        "implementation_version_id": procedure.implementation_version_id,
                        "algorithm_config": parameters, "training": {k: v for k, v in prepared["training"].items() if k != "seed"},
                        "max_steps": procedure.max_steps, "schedule_steps": procedure.schedule_steps or procedure.max_steps,
                        "completion": prepared["completion"], "recovery": procedure.recovery.model_dump(mode="json"),
                        "diagnostics": prepared["diagnostics"],
                        "input_binding": input_binding,
                        "scientific_source_hash": source["manifest"]["scientific_digest"], "runtime": source["manifest"]["runtime"]})
                if any(value != definitions[0] for value in definitions):
                    raise ValueError("A logical procedure must normalize consistently across its declared instances")
                normalized[slot_id] = definitions[0]
            execution_id, design_id, protocol_id = identifier("execution"), identifier("design"), identifier("protocol")
            study_ids = {scope: identifier("study") for scope in {group.scope for group in template.groups}}
            task_ids_by_scope = {scope: list(dict.fromkeys(task_id for group in template.groups if group.scope == scope
                for task_id in (group.task_ids or list(tasks)))) for scope in study_ids}
            selection = freeze_rule(template.selection, "selection", [ProblemInstance(**tasks[key]["problem"]) for key in task_ids_by_scope["development"]], store=self.store)
            check_design(selection, {"instances": [tasks[key]["problem"] for key in task_ids_by_scope["development"]]}, store=self.store)
            analysis = freeze_rule(template.analysis, "verdict", instances, store=self.store) if template.analysis else {}
            if analysis:
                check_design(analysis, {"conditional": True, "template": template.model_dump(mode="json"),
                    "task_ids": list(tasks), "logical_methods": normalized, "instances": [item.model_dump(mode="json") for item in instances]}, store=self.store)
            policies = {scope: validation_policy([tasks[key] for key in keys], template.validation_policies.get(scope, {}),
                            registry_resolver=self.workspace.evaluators.registry_for)
                        for scope, keys in task_ids_by_scope.items()}
            template_record = self.store.put_immutable("study_template_version", {"id": "template_" + template.digest(),
                "definition": template.model_dump(mode="json")}, "study.template_frozen")
            for scope, study_id in study_ids.items():
                study = StudySpec(id=study_id, campaign_id=campaign_id, parent_study_id=campaign.get("active_study_id"),
                    goal=f"{template.goal} ({scope})", scope="exploratory" if scope == "development" else "confirmation",
                    instance_ids=[tasks[key]["problem_instance_id"] for key in task_ids_by_scope[scope]],
                    method_roster=[content_hash(method) for method in normalized.values()],
                    selection=selection if scope == "development" else {}, validation_policy=policies[scope],
                    confirmation={"id": protocol_id, "design_id": design_id} if scope != "development" else {},
                    created_at=now(), authority=authority)
                self.store.put_immutable("study", study.model_dump(mode="json"), "study.created")
            cells = []
            for group in template.groups:
                pairs = [(slot, seed) for slot in group.slots for seed in group.seeds]
                if group.cell_order == "seed_then_slot":
                    pairs = [(slot, seed) for seed in group.seeds for slot in group.slots]
                for slot_id, seed in pairs:
                    for task_id in group.task_ids or list(tasks):
                        cell = {"id": "cell_" + content_hash([design_id, group.id, slot_id, task_id, seed]),
                                "campaign_id": campaign_id, "design_id": design_id, "study_id": study_ids[group.scope],
                                "group_id": group.id, "scope": group.scope, "slot_id": slot_id, "task_id": task_id,
                                "seed": seed, "instance_digest": ProblemInstance(**tasks[task_id]["problem"]).digest(),
                                "problem": tasks[task_id]["problem"], "admission": group.admission, "priority": group.priority,
                                "reference_role": group.reference_role}
                        cells.append(self.store.put_immutable("protocol_cell", cell, "study.cell_declared"))
            self._check_freshness(campaign_id, protocol_id, cells, template.confirmation_kind)
            for cell in cells:
                procedure = template.methods[cell["slot_id"]].procedure
                if procedure and isinstance(procedure.input_binding, PrefixInput):
                    binding = procedure.input_binding
                    if not any(other["group_id"] == binding.group and other["slot_id"] == binding.slot and
                               other["seed"] == cell["seed"] and other["instance_digest"] == cell["instance_digest"] for other in cells):
                        raise ValueError("A bound prefix has no matching instance/seed producer cell")
            design = self.store.put_immutable("confirmation_design", {"id": design_id, "campaign_id": campaign_id,
                "execution_id": execution_id, "protocol_id": protocol_id, "template_id": template_record["id"],
                "template": template.model_dump(mode="json"), "study_ids": study_ids, "source_id": source["id"],
                "input_bindings": {name: {"asset_id": asset["id"], "asset_digest": asset["content_hash"]} for name, asset in inputs.items()},
                "selection": selection, "analysis": analysis, "logical_methods": normalized,
                "cell_ids": [cell["id"] for cell in cells], "authority": authority, "created_at": now()}, "confirmation.design_frozen")
            for slot_id, method in normalized.items():
                self._bind(design, slot_id, method, source_slot=slot_id, nomination_id=None)
            self.store.put_immutable("confirmation_protocol", {"id": protocol_id, "contract_version": 2,
                "campaign_id": campaign_id, "study_id": study_ids["confirmation"], "design_id": design_id,
                "kind": template.confirmation_kind, "analysis": analysis, "created_at": now(),
                "selection_rule": "Resolve each declared slot using its literal procedure or frozen development selection"}, "confirmation.frozen")
            execution = {"id": execution_id, "campaign_id": campaign_id, "design_id": design_id, "protocol_id": protocol_id,
                "status": "frozen", "created_at": now(), "authority": authority}
            self.store.put("study_execution", execution, "study.execution_frozen")
            campaign.update(active_study_id=study_ids["development"], version=campaign["version"]+1, updated_at=now())
            self.store.put("campaign", campaign, "campaign.study_changed")
            return execution

    @staticmethod
    def _request(procedure, campaign_id, task_id, seed, **overrides):
        values = {key: value for key, value in procedure.items() if key not in {"schema_version", "input_binding"}}
        if values.get("completion"):
            values["completion"] = {key: value for key, value in values["completion"].items() if key != "schema_version"}
        return TrialInput(campaign_id=campaign_id, task_id=task_id, seed=seed, **{**values, **overrides})

    def activate(self, execution_id, *, authority="researcher"):
        with self.workspace.lock, self.store.transaction():
            execution = self.store.get(execution_id, "study_execution")
            if execution["status"] != "frozen":
                return execution
            design = self.store.get(execution["design_id"], "confirmation_design")
            if any(row["protocol_id"] == design["protocol_id"] for row in self.store.list("confirmation_release", design["campaign_id"])):
                raise ValueError("This frozen cohort is closed and cannot be activated")
            template = StudyTemplateVersion(**design["template"])
            start = time.time()
            grant = self.workspace.resources.create(ExecutionGrant(id="grant_"+execution_id, campaign_id=execution["campaign_id"],
                owner_id=execution_id, worker_seconds=template.worker_seconds, starts_at=start, deadline_at=start+template.total_seconds,
                max_workers=template.max_workers, stop_grace_seconds=self.workspace.stop_grace_seconds, authority=authority))
            activation = {"id": "activation_"+execution_id, "campaign_id": execution["campaign_id"], "execution_id": execution_id,
                "grant_id": grant["id"], "starts_at": start, "development_deadline": start+template.development_seconds,
                "deadline_at": grant["deadline_at"], "authority": authority}
            self.store.put_immutable("study_activation", activation, "study.activated")
            execution.update(status="running", activation_id=activation["id"], grant_id=grant["id"])
            return self.store.put("study_execution", execution, "study.execution_started")

    def _bind(self, design, slot_id, method, *, source_slot, nomination_id):
        return self.store.put_immutable("method_binding", {"id": "binding_"+content_hash([design["id"], slot_id]),
            "campaign_id": design["campaign_id"], "design_id": design["id"], "slot_id": slot_id,
            "method_id": content_hash(method), "logical_method": method, "source_slot": source_slot,
            "nomination_id": nomination_id, "derivation": "nomination" if nomination_id else "literal"}, "study.method_bound")

    def _bindings(self, design):
        return {row["slot_id"]: row for row in self.store.list("method_binding", design["campaign_id"]) if row["design_id"] == design["id"]}

    def _cells(self, design):
        return [self.store.get(key, "protocol_cell") for key in design["cell_ids"]]

    def _launch(self, cell_id):
        try:
            return self.store.get("launch_"+cell_id, "cell_launch")
        except KeyError:
            return None

    def _canonical(self, cell, cells, bindings):
        binding = bindings.get(cell["slot_id"])
        if not binding:
            return cell
        peers = [other for other in cells if other["scope"] == cell["scope"] and other["seed"] == cell["seed"] and
                 other["instance_digest"] == cell["instance_digest"] and
                 bindings.get(other["slot_id"], {}).get("method_id") == binding["method_id"]]
        # Existing cells retain identity when a selected alias becomes bound.
        preferred = sorted(peers, key=lambda row: (row["admission"] == "nomination", bindings[row["slot_id"]]["derivation"] != "literal"))
        return next((other for other in peers if self._launch(other["id"])), preferred[0])

    def _check_freshness(self, campaign_id, protocol_id, cells, kind):
        outside = [row for row in self.store.list("trial", campaign_id) if not row.get("recipe") and
                   row.get("protected_cohort_id") != protocol_id]
        cohort = next((row for row in self.store.list("confirmation_design", campaign_id) if row["protocol_id"] == protocol_id), None)
        cohort_studies = {value for key, value in (cohort or {}).get("study_ids", {}).items() if key != "development"}
        for cell in cells:
            if cell["scope"] != "confirmation":
                continue
            self.check_external_exposure(campaign_id, cell["problem"], cell["seed"], cohort_id=protocol_id)
            same = [row for row in outside if row.get("problem", {}).get("scientific_identity") == cell["problem"]["scientific_identity"]]
            if kind == "seed_replication" and any(row["seed"] == cell["seed"] for row in same):
                raise ValueError("Confirmation seeds were exposed outside the predeclared cohort")
            if kind == "seed_replication" and any(other["scope"] == "development" and other["seed"] == cell["seed"] and
                    other["problem"]["scientific_identity"] == cell["problem"]["scientific_identity"] for other in cells):
                raise ValueError("Development and confirmation cannot share a seed on the same instance")
            if kind == "unseen_instance":
                if same or any(other["scope"] == "development" and other["problem"]["scientific_identity"] == cell["problem"]["scientific_identity"] for other in cells):
                    raise ValueError("Unseen-instance confirmation cannot use development-exposed instances")
                for decision in self.store.list("reuse_decision", campaign_id):
                    if decision["decision"] == "decline" or decision["study_id"] in cohort_studies:
                        continue
                    asset = self.store.get(decision["asset_id"], "asset")
                    if asset["exposure_status"] == "unknown" or cell["problem"]["scientific_identity"] in asset["exposed_instance_ids"]:
                        raise ValueError("Prior reference or input exposure prevents an unseen-instance claim")
                from optimization_framework.evaluation.registry import problems
                instance = ProblemInstance(**cell["problem"])
                adapter = problems.get(instance.definition_id)
                if hasattr(adapter, "check_unseen_instance"):
                    adapter.check_unseen_instance(self.store, campaign_id, instance)

    def check_external_exposure(self, campaign_id, problem, seed, *, cohort_id=None):
        closed = {row["protocol_id"] for row in self.store.list("confirmation_release", campaign_id)}
        for design in self.store.list("confirmation_design", campaign_id):
            if design["protocol_id"] in closed or design["protocol_id"] == cohort_id:
                continue
            for cell in self._cells(design):
                if cell["scope"] == "confirmation" and cell["problem"]["scientific_identity"] == problem["scientific_identity"] and (
                        design["template"]["confirmation_kind"] == "unseen_instance" or cell["seed"] == seed):
                    raise ValueError("This work would expose an instance or seed reserved by an open frozen confirmation cohort")

    def _input(self, design, cell, binding, cells):
        procedure = design["template"]["methods"][binding["source_slot"]]["procedure"]
        rule = procedure.get("input_binding")
        if not rule:
            return [], []
        if rule["kind"] == "declared_asset:v1":
            selected = design["input_bindings"][rule["slot"]]
            asset = self.store.get(selected["asset_id"], "asset")
            if asset["content_hash"] != selected["asset_digest"]:
                raise ValueError("A declared input differs from the frozen reference binding")
            return [asset["id"]], []
        producer = next(other for other in cells if other["group_id"] == rule["group"] and other["slot_id"] == rule["slot"] and
                        other["seed"] == cell["seed"] and other["instance_digest"] == cell["instance_digest"])
        launched = self._launch(producer["id"])
        if not launched:
            return None, [producer["id"]]
        grants = [row for row in self.store.list("diagnostic_grant", design["campaign_id"]) if row["parent_trial_id"] == launched["trial_id"] and
                  row["count"] == rule["count"] and row["schedule"]["unit"] == rule["unit"] and row["status"] == "dispatched"]
        if len(grants) > 1:
            raise ValueError("The declared prefix has ambiguous milestone snapshots")
        assets = [self.store.get(key, "asset") for grant in grants for key in grant.get("asset_ids", [])]
        solutions = [row for row in assets if row["kind"] == rule["asset_kind"]]
        if not solutions:
            return None, [producer["id"]]
        # Output ingestion orders the archive by rank; the worker's top solution
        # is the declared prefix input, not a later or differently seeded result.
        return [solutions[0]["id"]], []

    def reconcile(self):
        for execution in self.store.list("study_execution"):
            if execution["status"] not in {"running", "closing"}:
                continue
            try:
                self._reconcile(execution["id"])
            except (ValueError, KeyError, OSError) as exc:
                self.workspace.memory.issue(execution["campaign_id"], "study_execution_requires_attention", str(exc), affected=execution["id"])

    def _reconcile(self, execution_id):
        with self.workspace.lock, self.store.transaction():
            execution = self.store.get(execution_id, "study_execution")
            design = self.store.get(execution["design_id"], "confirmation_design")
            activation = self.store.get(execution["activation_id"], "study_activation")
            if any(row["protocol_id"] == design["protocol_id"] for row in self.store.list("confirmation_release", design["campaign_id"])):
                self.workspace.resources.release(activation["grant_id"], rationale="The researcher closed this frozen cohort")
                execution.update(status="closed", finished_at=now())
                self.store.put("study_execution", execution, "study.execution_closed")
                return
            cells = self._cells(design)
            self._schedule_checks(design, activation)
            self._select(design, activation, cells)
            bindings = self._bindings(design)
            for cell in cells:
                canonical = self._canonical(cell, cells, bindings)
                if canonical["id"] != cell["id"]:
                    self.store.put_immutable("cell_alias", {"id": "alias_"+cell["id"], "campaign_id": design["campaign_id"],
                        "design_id": design["id"], "cell_id": cell["id"], "canonical_cell_id": canonical["id"],
                        "method_id": bindings[cell["slot_id"]]["method_id"]}, "study.cell_coalesced")
            trials = [row for row in self.store.list("trial", design["campaign_id"]) if row.get("study_execution_id") == execution_id]
            free = design["template"]["max_workers"] - sum(row["status"] in ACTIVE for row in trials)
            for cell in sorted(cells, key=lambda row: -row["priority"]):
                if free <= 0:
                    break
                if self._launch(cell["id"]) or cell["slot_id"] not in bindings:
                    continue
                canonical = self._canonical(cell, cells, bindings)
                if canonical["id"] != cell["id"]:
                    continue
                deadline = activation["development_deadline"] if cell["scope"] == "development" else activation["deadline_at"]
                nomination = self._nomination(design)
                if time.time() >= deadline or (cell["admission"] == "nomination" and not (nomination and nomination["selected_method_ids"])):
                    continue
                if cell["scope"] == "confirmation":
                    self._check_freshness(design["campaign_id"], design["protocol_id"], [cell], design["template"]["confirmation_kind"])
                assets, waiting = self._input(design, cell, bindings[cell["slot_id"]], cells)
                if waiting:
                    continue
                if self._admit(design, activation, cell, bindings[cell["slot_id"]], assets, deadline):
                    free -= 1
            trials = [row for row in self.store.list("trial", design["campaign_id"]) if row.get("study_execution_id") == execution_id]
            expired = time.time() >= activation["deadline_at"]
            state = self.assess(execution_id)
            if expired or state["complete"]:
                execution["status"] = "closing"
                if not any(row["status"] in ACTIVE for row in trials):
                    try:
                        self.workspace.resources.release(activation["grant_id"], rationale="Fixed study work completed or its overall deadline expired")
                    except ValueError:
                        pass  # The diagnostic reconciler must close its reservations first.
                    else:
                        execution.update(status="complete" if state["complete"] else "incomplete", finished_at=now())
                self.store.put("study_execution", execution, "study.execution_progress")

    def _admit(self, design, activation, cell, binding, assets, deadline):
        source_slot = binding["source_slot"]
        procedure = design["template"]["methods"][source_slot]["procedure"]
        policy = self.store.get(cell["study_id"], "study")["validation_policy"]
        check_seconds = len(policy.get("required_recipes", [])) * policy.get("validation_wall_seconds", 120)
        from optimization_framework.contracts.diagnostics import DiagnosticSchedule
        diagnostic_seconds = sum(len(raw["at_counts"]) * DiagnosticSchedule(**raw).allocation() for raw in procedure["diagnostics"])
        grant_state = next(row for row in self.workspace.resources.assessment(design["campaign_id"])["grants"] if row["grant_id"] == activation["grant_id"])
        wall = min(procedure["wall_seconds"], deadline-time.time(), grant_state["available_seconds"]-check_seconds-diagnostic_seconds)
        if wall <= 0:
            return False
        decisions = []
        for asset_id in assets:
            decision = self.workspace.assets.decide(ReuseDecision(id="reuse_"+content_hash([cell["id"], asset_id]),
                campaign_id=design["campaign_id"], study_id=cell["study_id"], asset_id=asset_id, decision="reuse", intended_use="optimizer_input",
                rationale="The frozen template explicitly binds this input asset or the matching instance and seed's milestone prefix",
                consequences={"scope": cell["scope"], "reference_role": cell.get("reference_role")},
                authority="frozen_study_template", created_at=design["created_at"]))
            decisions.append(decision["id"])
        request = self._request(procedure, design["campaign_id"], cell["task_id"], cell["seed"], wall_seconds=wall,
            initial_assets=assets, reuse_decision_ids=decisions, priority=cell["priority"])
        execution = {"study_id": cell["study_id"], "study_execution_id": design["execution_id"], "execution_grant_id": activation["grant_id"],
            "absolute_deadline": deadline, "execution_source_id": design["source_id"], "protocol_cell_id": cell["id"],
            "logical_method": binding["logical_method"]}
        if cell["scope"] != "development":
            protocol = self.store.get(design["protocol_id"], "confirmation_protocol")
            execution.update(protected_cohort_id=design["protocol_id"], confirmation_protocol_id=design["protocol_id"],
                confirmation_protocol_hash=protocol["content_hash"], confirmation_method_id=binding["method_id"],
                confirmation_kind=design["template"]["confirmation_kind"], confirmatory=True)
        self.workspace.resources.check(activation["grant_id"], design["campaign_id"], wall+check_seconds+diagnostic_seconds, deadline_at=deadline)
        trial = self.workspace.create_trial(request, execution=execution)
        self.store.put_immutable("cell_launch", {"id": "launch_"+cell["id"], "campaign_id": design["campaign_id"],
            "cell_id": cell["id"], "design_id": design["id"], "trial_id": trial["id"], "method_binding_id": binding["id"],
            "method_id": binding["method_id"], "input_asset_ids": assets, "experiment_spec_hash": trial["experiment_spec_hash"],
            "created_at": now()}, "study.cell_admitted")
        if check_seconds:
            self.store.put("execution_check_grant", {"id": "checks_"+trial["id"], "campaign_id": design["campaign_id"],
                "execution_grant_id": activation["grant_id"], "trial_id": trial["id"], "reserved_seconds": check_seconds,
                "status": "reserved", "created_at": now()}, "study.checks_reserved")
        return True

    def _schedule_checks(self, design, activation):
        for grant in self.store.list("execution_check_grant", design["campaign_id"]):
            if grant["execution_grant_id"] != activation["grant_id"] or grant["status"] != "reserved":
                continue
            trial = self.store.get(grant["trial_id"], "trial")
            if time.time() >= trial["absolute_deadline"]:
                grant.update(status="deadline_reached", finished_at=now())
                self.store.put("execution_check_grant", grant, "study.checks_closed")
                continue
            if trial["status"] in ACTIVE or trial.get("asset_capture_attempt", -1) != trial.get("attempt", 0):
                continue
            if trial["status"] != "completed" or not (trial.get("result") or {}).get("scientific_complete"):
                if trial["status"] not in {"paused", "interrupted"}:
                    grant.update(status="not_reached", finished_at=now())
                    self.store.put("execution_check_grant", grant, "study.checks_closed")
                continue
            policy = self.store.get(trial["study_id"], "study")["validation_policy"]
            grant["status"] = "dispatching"
            self.store.put("execution_check_grant", grant)
            children = []
            for recipe_id in policy.get("required_recipes", []):
                child = self.workspace.run_recipe(trial["id"], RecipeInput(recipe_id=recipe_id, subject_limit=1,
                    parameters=policy.get("required_recipe_parameters", {}).get(recipe_id, {}),
                    wall_seconds=policy.get("validation_wall_seconds", 120)), authority="frozen_study_template")
                child["priority"] = design["template"]["validation_priority"]
                self.store.put("trial", child)
                children.append(child["id"])
            grant.update(status="dispatched", trial_ids=children, dispatched_at=now())
            self.store.put("execution_check_grant", grant, "study.checks_dispatched")

    def _nomination(self, design):
        try:
            return self.store.get("nomination_"+design["study_ids"]["development"], "nomination")
        except KeyError:
            return None

    def _select(self, design, activation, cells):
        if self._nomination(design):
            return
        development = [cell for cell in cells if cell["scope"] == "development"]
        ready = True
        rows = []
        for cell in development:
            launch = self._launch(cell["id"])
            if not launch:
                ready = False
                continue
            trial = self.store.get(launch["trial_id"], "trial")
            row = experiment_evidence(self.store, trial, cutoff_at=activation["development_deadline"])
            rows.append(row)
            if trial["status"] in ACTIVE | {"paused", "interrupted"}:
                ready = False
            if not row["evidence_complete"]:
                children = [job for job in self.store.list("trial", design["campaign_id"]) if job.get("parent_trial_id") == trial["id"]]
                if any(job["status"] in ACTIVE | {"paused", "interrupted"} or job.get("asset_capture_attempt", -1) != job.get("attempt", 0) for job in children):
                    ready = False
                if any(grant["trial_id"] == trial["id"] and grant["status"] == "reserved" for grant in self.store.list("execution_check_grant", design["campaign_id"])):
                    ready = False
                if any(grant["status"] == "reserved" for grant in row["diagnostics"]["grants"]):
                    ready = False
                if trial["status"] == "completed" and trial.get("asset_capture_attempt", -1) != trial.get("attempt", 0):
                    ready = False
        if not ready and time.time() < activation["development_deadline"]:
            return
        evidence = {"schema_version": 1, "study_id": design["study_ids"]["development"], "design_hash": design["content_hash"],
            "cutoff_at": activation["development_deadline"], "experiments": rows}
        result = evaluate(design["selection"], evidence, store=self.store)
        selected = result.get("selected_method_ids", [])
        if len(selected) > 1:
            raise ValueError("This template's selected slot needs one nominated method")
        methods = {row["method_id"]: row["logical_method"] for row in rows if row["method_id"] in selected and row["evidence_complete"]}
        if set(methods) != set(selected):
            raise ValueError("The selection rule returned a method without eligible development evidence")
        prototypes = {key: next(row["trial_id"] for row in rows if row["method_id"] == key and row["evidence_complete"]) for key in selected}
        record = {"id": "nomination_"+design["study_ids"]["development"], "campaign_id": design["campaign_id"],
            "study_id": design["study_ids"]["development"], "design_id": design["id"], "rule": design["selection"],
            "evidence_hash": content_hash(evidence), "evidence": evidence, "result": result, "selected_method_ids": selected,
            "methods": methods, "prototypes": prototypes, "authority": "frozen_study_template", "created_at": now()}
        nomination = self.store.put_immutable("nomination", record, "study.nominated")
        if result.get("needs_attention"):
            self.workspace.memory.issue(design["campaign_id"], "study_selection_inconclusive", result["reason"], affected=design["execution_id"])
        for slot_id, slot in design["template"]["methods"].items():
            if not slot["select_from"] or not selected:
                continue
            source = next((key for key in slot["select_from"] if content_hash(design["logical_methods"][key]) == selected[0]), None)
            if not source:
                raise ValueError("The nominated method is outside the selected slot's frozen candidates")
            self._bind(design, slot_id, methods[selected[0]], source_slot=source, nomination_id=nomination["id"])

    def assess(self, execution_id):
        execution = self.store.get(execution_id, "study_execution")
        design = self.store.get(execution["design_id"], "confirmation_design")
        cells, bindings = self._cells(design), self._bindings(design)
        activation = self.store.get(execution["activation_id"], "study_activation") if execution.get("activation_id") else None
        nomination = self._nomination(design)
        rows = []
        for cell in cells:
            canonical = self._canonical(cell, cells, bindings)
            launch = self._launch(canonical["id"])
            deadline = (activation["development_deadline"] if cell["scope"] == "development" else activation["deadline_at"]) if activation else None
            trial = self.store.get(launch["trial_id"], "trial") if launch else None
            evidence = experiment_evidence(self.store, trial, cutoff_at=deadline) if trial else None
            if evidence and deadline and time.time() >= deadline:
                current = experiment_evidence(self.store, trial)
                evidence["evidence_complete"] &= current["evidence_complete"]
                evidence["validation"] = current["validation"]
            waiting = []
            if not activation:
                waiting.append("activation")
            if cell["slot_id"] not in bindings or cell["admission"] == "nomination" and not nomination:
                waiting.append("nomination")
            if not trial and cell["slot_id"] in bindings:
                _, dependencies = self._input(design, cell, bindings[cell["slot_id"]], cells)
                waiting.extend(dependencies)
            if not trial and not waiting:
                waiting.append("resource_admission")
            status = trial["status"] if trial else "deadline_reached" if deadline and time.time() >= deadline else "waiting"
            rows.append({**cell, "canonical_cell_id": canonical["id"], "method_id": bindings.get(cell["slot_id"], {}).get("method_id"),
                "trial_id": trial["id"] if trial else None, "status": status, "waiting_for": waiting,
                "scientific_complete": bool(trial and trial["status"] == "completed" and (trial.get("result") or {}).get("scientific_complete")),
                "evidence_complete": bool(evidence and evidence["evidence_complete"]), "evidence": evidence})
        return {"execution": execution, "design_id": design["id"], "template_id": design["template_id"],
            "input_bindings": design.get("input_bindings", {}),
            "name": design["template"]["name"], "template": design["template"], "study_ids": design["study_ids"],
            "resources": self.workspace.resources.assessment(design["campaign_id"]) if self.workspace else None,
            "qualification_only": design["template"]["qualification_only"], "activation": activation, "cells": rows,
            "nomination_id": nomination["id"] if nomination else None,
            "complete": bool(rows) and bool(nomination and nomination["selected_method_ids"]) and all(row["evidence_complete"] for row in rows if row["scope"] != "development"),
            "bindings": list(bindings.values())}
