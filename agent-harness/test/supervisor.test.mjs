import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {Supervisor} from '../dist/supervisor.js';

const until = async predicate => { for (let i=0; i<200; i++) { if (predicate()) return; await new Promise(r=>setTimeout(r,5)); } throw Error('Timed out'); };
const request = (run_id, text='work', mode='follow_up') => ({run_id, text, mode, spec:{model:'fake', role:'pi'}});
function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(),'pi-test-')); t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const runtimes=[], options=[], gatewayCalls=[];
  const factory = async opts => {
    options.push(opts);
    const runtime={sessionFile:opts.sessionFile || path.join(opts.directory,'exact-session.jsonl'),sessionId:'exact-session',messages:[],steers:[],
      subscribe(fn){this.listener=fn;},
      prompt(text){this.input=text;return new Promise(resolve=>{this.finish=()=>{this.messages.push({role:'assistant',stopReason:'stop'});resolve();};});},
      async steer(text){this.steers.push(text);}, async abort(){this.finish?.();},getLastAssistantText(){return 'saved';}};
    runtimes.push(runtime); return runtime;
  };
  const gateway=async (operation, data)=>{gatewayCalls.push({operation,data});return operation==='manifest'?{instructions:'frozen',tools:[{name:'inspect',input_schema:{type:'object'}}]}:{ok:true};};
  return {directory,runtimes,options,gatewayCalls,factory,gateway,supervisor:new Supervisor(directory,factory,gateway,'private-auth')};
}
test('stable run receipts do not repeat work; steering stays on the active session', async t=>{
  const f=fixture(t), s=f.supervisor;
  await s.submit('pi',request('first')); await until(()=>f.runtimes[0]?.finish);
  await s.submit('pi',request('first'));
  await assert.rejects(s.submit('pi',request('first','changed')),/identity/);
  await s.submit('pi',request('steer','researcher change','steer'));
  await s.submit('pi',request('steer','researcher change','steer'));
  assert.equal(f.runtimes.length,1); assert.equal(f.runtimes[0].steers.length,1);
  assert.equal(s.view('pi').runs.steer.result.delivery,'steering_queued');
  f.runtimes[0].finish(); await until(()=>s.view('pi').runs.first.status==='completed');
});
test('restart uses the exact saved session and interrupts an uncertain run without replaying it',async t=>{
  const f=fixture(t); await f.supervisor.submit('pi',request('first')); await until(()=>f.runtimes[0]?.finish);
  const saved=f.supervisor.view('pi').session_file;
  fs.appendFileSync(path.join(f.directory,'pi/events.jsonl'),JSON.stringify({seq:50,type:'after_snapshot'})+'\n');
  const restarted=new Supervisor(f.directory,f.factory,f.gateway,'private-auth');
  assert.equal(restarted.view('pi').runs.first.status,'interrupted');
  await restarted.submit('pi',request('recovery'));await until(()=>f.runtimes.length===2);
  assert.equal(f.options[1].sessionFile,saved);
  assert.ok(restarted.view('pi',50).events[0].seq>50);
  f.runtimes[1].finish();await until(()=>restarted.view('pi').runs.recovery.status==='completed');
});
test('controls settle active and queued runs; tools retain the owning run identity',async t=>{
  const f=fixture(t),s=f.supervisor;
  await s.submit('pi',request('first'));await until(()=>f.runtimes[0]?.finish);
  await f.options[0].tools[0].execute('call-once',{query:'x'});
  assert.equal(f.gatewayCalls.at(-1).data.run_id,'first');
  await s.submit('pi',request('second'));
  await s.control('pi','pause');await until(()=>s.view('pi').runs.first.status==='paused');
  assert.equal(s.view('pi').runs.second.status,'paused');
  await s.control('pi','resume');
  await s.submit('pi',request('resume'));await until(()=>s.view('pi').runs.resume.status==='running');
  f.runtimes[0].finish();await until(()=>s.view('pi').runs.resume.status==='completed');
});
test('public events exclude hidden reasoning and catalog API prices',async t=>{
  const f=fixture(t);await f.supervisor.submit('pi',request('first'));await until(()=>f.runtimes[0]?.finish);
  f.runtimes[0].listener({type:'message_end',message:{role:'assistant',content:[{type:'thinking',thinking:'private'},{type:'text',text:'finding'}],usage:{input:7,output:3,cost:{total:42}}}});
  const event=f.supervisor.view('pi').events.find(e=>e.type==='assistant.message');
  assert.equal(event.text,'finding'); assert.equal(event.usage.input,7);assert.ok(!('cost' in event.usage));
  assert.ok(!JSON.stringify(event).includes('private'));f.runtimes[0].finish();
});
test('service shutdown interrupts work for recovery and preserves an explicit pause',async t=>{
  const f=fixture(t),s=f.supervisor;
  await s.submit('pi',request('active'));await until(()=>f.runtimes[0]?.finish);
  await s.submit('paused',request('pause-me'));await until(()=>f.runtimes[1]?.finish);
  await s.control('paused','pause');
  await s.close();await until(()=>s.view('pi').runs.active.status==='interrupted');
  const restarted=new Supervisor(f.directory,f.factory,f.gateway,'private-auth');
  assert.equal(restarted.agents.get('pi').control,null);
  assert.equal(restarted.agents.get('paused').control,'paused');
});

test('shutdown remains interrupted when SDK abort returns before the active prompt settles', async t=>{
  const f=fixture(t),s=f.supervisor;
  await s.submit('pi',request('active'));await until(()=>f.runtimes[0]?.finish);
  await s.submit('pi',request('queued'));
  f.runtimes[0].abort=async()=>{};
  await s.close();
  assert.equal(s.view('pi').runs.active.status,'interrupted');
  f.runtimes[0].finish();await until(()=>!s.agents.get('pi').busy);
  assert.equal(s.view('pi').runs.active.status,'interrupted');
  assert.equal(s.view('pi').runs.queued.status,'interrupted');
  const restarted=new Supervisor(f.directory,f.factory,f.gateway,'private-auth');
  assert.equal(restarted.agents.get('pi').control,null);
});
