import time
import pytest

from optimization_framework.agents.diagnostics import reconcile, reserve
from optimization_framework.contracts.requests import CampaignInput, TaskInput
from optimization_framework.execution.service import Workspace


def test_fixed_masks_reserve_budget_and_measure_te_tm_reproducibly(tmp_path):
    pytest.importorskip('meent')
    from test_meent_2d_problem import configuration
    workspace=Workspace(tmp_path)
    campaign=workspace.create_campaign(CampaignInput(name='Fixed-mask diagnostic',compute_budget_seconds=100,
        validation_reserve_seconds=20,tasks=[TaskInput(name='MEENT',problem_id='meent_2d_dual_polarization_deflector',
            configuration=configuration(),fidelity={'rcwa_order_x':1,'rcwa_order_y':1})]))
    task=workspace.current_tasks(campaign['id'])[0]
    workspace.store.put_immutable('agent_artifact',{'id':'uniform','campaign_id':campaign['id'],'kind':'mask','content':[0]*128})
    payload={'task_id':task['id'],'mask_artifact_ids':['uniform'],'fidelities':[{'rcwa_order_x':1,'rcwa_order_y':1}],
        'wall_seconds':20,'repeats':2,'rationale':'Verify known homogeneous limit and repeated evaluation'}
    outcome=reserve(workspace,campaign['id'],payload,'fixture')
    assert workspace.allocated_seconds(campaign['id'])==20
    reconcile(workspace)
    for _ in range(100):
        time.sleep(.1);reconcile(workspace)
        job=workspace.store.get(outcome['job_id'],'fixed_mask_job')
        if job['status'] not in {'queued','starting','running'}:break
    assert job['status']=='completed',job
    report=workspace.store.get(job['report_id'],'fixed_mask_report')
    assert report['evaluation_requests']==2
    assert report['solver_executions']==4
    first,second=report['checks']
    assert first['observation']['objectives']['mean_plus1_transmission']==pytest.approx(0)
    assert not second['observation']['cache_hit']
    assert first['observation']['objectives']==second['observation']['objectives']
    assert job['execution_seconds'] <=20
    assert workspace.allocated_seconds(campaign['id'])==job['execution_seconds']
    assert workspace.store.list('fixed_mask_report')==[report]


def test_uncertain_fixed_mask_launch_keeps_reservation_and_never_replays(tmp_path,monkeypatch):
    workspace=Workspace(tmp_path)
    campaign=workspace.create_campaign(CampaignInput(name='Uncertain launch',compute_budget_seconds=100,validation_reserve_seconds=20,
        tasks=[TaskInput(name='Quadratic',problem_id='bounded_continuous',configuration={})]))
    workspace.store.put('fixed_mask_job',{'id':'uncertain','campaign_id':campaign['id'],'status':'starting',
        'started_epoch':time.time()-40,'wall_seconds':10,'execution_seconds':0,'scope':'interrupted diagnostic','identity':'frozen'})
    monkeypatch.setattr('optimization_framework.agents.diagnostics.subprocess.Popen',lambda *a,**k:pytest.fail('Unknown launch was replayed'))
    reconcile(workspace)
    job=workspace.store.get('uncertain','fixed_mask_job')
    assert job['status']=='interrupted' and job['execution_seconds']==10
    reconcile(workspace)
    assert len(workspace.store.list('fixed_mask_report'))==1
