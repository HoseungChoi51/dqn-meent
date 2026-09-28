"""Explicit researcher inputs for tests that need an existing idea.

Campaign creation intentionally produces no scientific proposals.
"""
from optimization_framework.campaigns.hypotheses import create
from optimization_framework.contracts.requests import HypothesisInput
from optimization_framework.storage.sqlite import identifier


def researcher_idea(workspace, campaign_id):
    return create(workspace, campaign_id, HypothesisInput(campaign_id=campaign_id,
        title="Researcher fixture proposal", algorithm="random", mechanism="Independent feasible samples",
        rationale="Explicit input for this service test"), identity=identifier("fixture_idea"))
