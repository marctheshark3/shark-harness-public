const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(`${__dirname}/../integrations/t3-current-session.js`, 'utf8');
function fixture() {
  let notifications = 0, timer, historyCall;
  const listeners = {};
  const document = {visibilityState:'visible', hasFocus:()=>true};
  const window = {location:{pathname:'/environment/thread-1', hash:''},
    history:{pushState(...args){historyCall = {self:this, args}; return 17;}, replaceState(){}},
    addEventListener(name, fn){listeners[name]=fn;}, setInterval(fn, delay){assert.equal(delay,2000);timer=fn;}};
  const context = {window, document, Pc(){notifications++;}};
  vm.createContext(context);vm.runInContext(source,context);
  return {window,document,listeners,tick:()=>timer(),count:()=>notifications,historyCall:()=>historyCall,
    scopes:env=>JSON.parse(JSON.stringify(context.sharkCurrentThreadScopes(env)))};
}
test('reports only the exact focused environment and existing-thread route',()=>{
  const f=fixture();
  assert.deepEqual(f.scopes('environment'),[{type:'thread',threadId:'thread-1'}]);
  assert.deepEqual(f.scopes('other'),[]);
  for(const path of ['/', '/environment', '/draft/thread-1', '/environment/thread/extra', '/environment/%2F', '/environment/%']){
    f.window.location.pathname=path; assert.deepEqual(f.scopes('environment'),[]);
  }
  f.window.location.pathname='/environment/thread-1';
  f.document.visibilityState='hidden';assert.deepEqual(f.scopes('environment'),[]);
  f.document.visibilityState='visible';f.document.hasFocus=()=>false;assert.deepEqual(f.scopes('environment'),[]);
});
test('navigation preserves browser behavior and reports changes through existing channel',()=>{
  const f=fixture(); const args=[{a:1},'', '/environment/thread-2'];
  assert.equal(f.window.history.pushState(...args),17);
  assert.deepEqual(f.historyCall().args,args);assert.equal(f.historyCall().self,f.window.history);
  assert.equal(f.count(),1);f.listeners.popstate();f.listeners.hashchange();assert.equal(f.count(),3);
  f.tick();assert.equal(f.count(),4);f.document.hasFocus=()=>false;f.tick();assert.equal(f.count(),4);
});
test('desktop hash routes identify the selected environment and thread',()=>{
  const f=fixture();
  f.window.location.pathname='/';
  f.window.location.hash='#/remote-environment/remote-thread?panel=files';
  assert.deepEqual(f.scopes('remote-environment'),[{type:'thread',threadId:'remote-thread'}]);
  assert.deepEqual(f.scopes('environment'),[]);
});
test('app-owned thread IDs decode once in browser and desktop routes',()=>{
  const f=fixture();
  for(const id of ['mcp:synthetic-session','thread:delegated-task:command%3Amcp%3Asynthetic']) {
    f.window.location.hash='';
    f.window.location.pathname='/environment/'+encodeURIComponent(id);
    assert.deepEqual(f.scopes('environment'),[{type:'thread',threadId:id}]);
    f.window.location.pathname='/';
    f.window.location.hash='#/environment/'+encodeURIComponent(id)+'?panel=files';
    assert.deepEqual(f.scopes('environment'),[{type:'thread',threadId:id}]);
  }
  for(const id of ['%2F','%5C','%00','%','%20']) {
    f.window.location.hash='#/environment/'+id;
    assert.deepEqual(f.scopes('environment'),[]);
  }
});
const contextSource=fs.readFileSync(`${__dirname}/../integrations/t3-voice-context.js`,'utf8');
function nativeFixture() {
  let context, calls=[], promoted=0;
  const window={sharkVoice:{register(c){context=c;}}};
  const scope={window,structuredClone,Date};vm.createContext(scope);vm.runInContext(contextSource,scope);
  const native={route:'/draft/draft',environmentId:'remote-environment',environment:'Remote environment',newThread:true,
    thread:{id:'remote-thread',title:'Untitled',worktreePath:null},
    project:{id:'remote-project',title:'Another project',workspaceRoot:'/remote/project'},
    runtimeMode:'approval-required',envMode:'worktree',baseBranch:'main',worktreeBranch:'voice-work',startFromOrigin:true,
    read:()=>({providerAvailable:true,multipleModelSelections:null,selectedModelSelection:{instanceId:'provider',model:'chosen'},interactionMode:'plan'}),
    dispatch:async req=>{calls.push(req);return {_tag:'Success',value:{sequence:12}};},onCreated:()=>{promoted++;}};
  scope.sharkRegisterConversation(native);
  return {native,capture:()=>context.capture(),calls,promoted:()=>promoted};
}
test('remote draft preserves selected settings, bootstraps once, and stays pinned across navigation',async()=>{
  const f=nativeFixture(), target=f.capture();
  f.native.environmentId='another';f.native.runtimeMode='full-access';
  assert.equal(target.destination.environmentId,'remote-environment');
  const job={id:'voice-command',messageId:'voice-message',text:'Build the thing'};
  assert.equal((await target.send(job)).state,'sent');
  const request=f.calls[0];assert.equal(request.environmentId,'remote-environment');
  assert.equal(request.input.runtimeMode,'approval-required');assert.equal(request.input.interactionMode,'plan');
  assert.equal(request.input.modelSelection.model,'chosen');
  assert.equal(request.input.bootstrap.createThread.projectId,'remote-project');
  assert.equal(request.input.bootstrap.prepareWorktree.baseBranch,'main');
  assert.equal(request.input.commandId,job.id);assert.equal(request.input.message.messageId,job.messageId);
  assert.equal(f.promoted(),1);
  await target.send({...job,id:'next-command'});assert.equal(f.calls[1].input.bootstrap,undefined);
});
test('failed acknowledgement never promotes draft or claims success',async()=>{
  const f=nativeFixture();f.native.dispatch=async()=>{throw Error('Disconnected')};
  assert.equal((await f.capture().send({id:'c',messageId:'m',text:'Words'})).state,'uncertain');
  assert.equal(f.promoted(),0);
});
test('current T3 launch acknowledgements bind to the selected remote thread',async()=>{
  const f=nativeFixture();
  f.native.protocolVersion=2;
  f.native.dispatch=async request=>{f.calls.push(request);return {_tag:'Success',value:{threadId:'remote-thread'}};};
  const result=await f.capture().send({id:'c',messageId:'m',text:'Words'});
  assert.deepEqual(JSON.parse(JSON.stringify(result)),{state:'sent',threadId:'remote-thread'});
  assert.equal(f.promoted(),1);
  assert.equal(f.calls[0].environmentId,'remote-environment');
});
test('a launch acknowledgement for another thread cannot report a sent prompt',async()=>{
  const f=nativeFixture();
  f.native.protocolVersion=2;
  f.native.dispatch=async()=>({_tag:'Success',value:{threadId:'wrong-thread'}});
  assert.equal((await f.capture().send({id:'c',messageId:'m',text:'Words'})).state,'uncertain');
  assert.equal(f.promoted(),0);
});
