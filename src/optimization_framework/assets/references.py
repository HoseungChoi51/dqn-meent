"""Explicit, idempotent publication of captured references from installed adapters."""
from optimization_framework.contracts.assets import Asset
from optimization_framework.contracts.base import canonical_json, content_hash
from optimization_framework.contracts.experiments import ArtifactReference
from optimization_framework.contracts.references import ReferenceImportInput, ReferenceSet
from optimization_framework.evaluation.registry import problems
from optimization_framework.storage.sqlite import now


def registered(provider):
    adapter = problems.get(provider)
    manifests = [ReferenceSet(**item) for item in adapter.reference_sets()] if hasattr(adapter, "reference_sets") else []
    if any(item.problem_id != provider for item in manifests) or len({item.id for item in manifests}) != len(manifests):
        raise ValueError("Reference sets must have distinct names under their owning problem adapter")
    return manifests


def matching_sets(provider, requirements):
    if not requirements:
        return []
    # Browsing templates reveals source identities, not historical candidate
    # values. Publication and experimental use remain separate explicit actions.
    return [{"provider": provider, "manifest_preview": manifest.model_dump(mode="json", exclude={"solutions": {"__all__": {"candidate"}}}), "manifest_digest": manifest.digest()}
        for manifest in registered(provider)
        if all(any(item.slot == slot and item.candidate_digest == requirement.candidate_digest
                   for item in manifest.solutions) for slot, requirement in requirements.items())]


def validate_input(catalog, name, requirement, asset, *, verify=True, instance=None):
    if asset["kind"] != requirement.asset_kind or catalog.availability(asset)["status"] == "unavailable":
        raise ValueError(f"Input {name} needs an available {requirement.asset_kind} asset")
    if requirement.candidate_digest and content_hash(asset["payload"].get("candidate")) != requirement.candidate_digest:
        raise ValueError(f"Input {name} does not match the declared reference candidate")
    catalog.check_input_exposure(asset)
    if instance and asset["kind"] == "solution":
        instance.candidate_schema.canonicalize(asset["payload"].get("candidate"))
    if verify:
        for reference in asset["artifacts"]:
            catalog.artifacts.verify(ArtifactReference(**reference))


def input_candidates(catalog, requirements, instance=None):
    result = {name: [] for name in requirements}
    for asset in catalog.store.list("asset") if requirements else []:
        for name, requirement in requirements.items():
            try:
                validate_input(catalog, name, requirement, asset, verify=False, instance=instance)
            except (ValueError, KeyError, TypeError):
                continue
            result[name].append({key: asset[key] for key in ("id", "title", "campaign_id", "cost_provenance", "exposure_status")})
    return result


def import_set(workspace, campaign_id, request, *, authority):
    request = request if isinstance(request, ReferenceImportInput) else ReferenceImportInput(**request)
    manifest = next((item for item in registered(request.provider) if item.id == request.reference_set_id), None)
    if manifest is None or manifest.digest() != request.manifest_digest:
        raise ValueError("The reviewed reference manifest is unavailable or changed; refresh before importing")
    problem = problems.resolve(manifest.problem_id, manifest.configuration)
    if problem.scientific_identity != manifest.scientific_identity:
        raise ValueError("The captured reference problem differs from the installed adapter's scientific identity")
    for item in manifest.solutions:
        problem.candidate_schema.canonicalize(item.candidate)
    record_id = "reference_set_" + manifest.digest()
    receipt_id = "reference_import_" + content_hash([campaign_id, record_id])
    with workspace.store.transaction():
        workspace.store.get(campaign_id, "campaign")
        try:
            return workspace.store.get(receipt_id, "reference_import")
        except KeyError:
            pass
        blob = workspace.assets.artifacts.put_bytes(canonical_json(manifest.model_dump(mode="json")), media_type="application/json")
        workspace.store.put_immutable("reference_set", {"id": record_id, "manifest": manifest.model_dump(mode="json"),
            "manifest_digest": manifest.digest(), "artifact": blob.model_dump(mode="json")}, "reference_set.captured")
        bindings = {}
        for item in manifest.solutions:
            asset = workspace.assets.publish(Asset(id="reference_"+content_hash([record_id, item.slot]),
                campaign_id=record_id, kind="solution", title=item.title, artifacts=[blob],
                payload={"candidate": item.candidate, "reference_set_id": record_id, "reference_role": item.role,
                    "historical_seed": item.seed, "source_identities": [source.model_dump(mode="json") for source in item.sources]},
                applicability={"problem_id": manifest.problem_id, "scientific_identity": manifest.scientific_identity,
                    "configuration": manifest.configuration}, exposed_instance_ids=[manifest.scientific_identity],
                cost_provenance="unknown", exposure_status="unknown", authority="captured_reference_manifest",
                created_at=manifest.captured_at))
            bindings[item.slot] = asset["id"]
        return workspace.store.put_immutable("reference_import", {"id": receipt_id, "campaign_id": campaign_id,
            "reference_set_id": record_id, "manifest_digest": manifest.digest(), "asset_bindings": bindings,
            "authority": authority, "created_at": now(), "historical_cost": "unknown", "historical_exposure": "partial",
            "interpretation": "Captured candidate values and source identities; original producer history and costs are not imported. Experimental use requires a separate frozen binding or reuse decision."}, "reference_set.imported")
