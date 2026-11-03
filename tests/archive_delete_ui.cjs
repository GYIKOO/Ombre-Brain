const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const html=readFileSync('frontend/dashboard.html','utf8');
const code=html.slice(html.indexOf('let archiveDeleteBusy ='),html.indexOf('async function hardDeleteSelectedTests()'));
const button={innerHTML:'delete',disabled:false};
let requests=0,resolveRequest,refreshes=0;
const selected=new Set(['a','b']);
const ctx=vm.createContext({
 document:{querySelectorAll:()=>[button]},
 selectedBucketIds:selected,allBuckets:[{id:'a',type:'archived'},{id:'b',type:'archived'}],
 duplicateResults:{pairs:[{a:{id:'x'},b:{id:'y'}}],total:1,reviewed:[{archived:'a',representative:'x'},{archived:'b',representative:'y'}]},
 confirm:()=>true,alert:()=>{},closeDetail:()=>{},renderDuplicates:()=>{},
 loadBuckets:async()=>{refreshes++;},loadDuplicates:async()=>{throw Error('Unexpected full scan');},
 readJsonSafe:async r=>r.body,
 authFetch:async()=>{requests++;return new Promise(resolve=>resolveRequest=resolve);},
});
vm.runInContext(code,ctx);
(async()=>{
 const first=vm.runInContext('deleteArchivedBuckets()',ctx);
 assert.equal(button.disabled,true);
 await vm.runInContext('deleteArchivedBuckets()',ctx);
 assert.equal(requests,1);
 resolveRequest({ok:true,body:{deleted:['a'],errors:[{id:'b',error:'protected'}]}});
 await first;
 assert.deepEqual([...selected],['b']);
 assert.equal(ctx.duplicateResults.pairs.length,1);
 assert.equal(ctx.duplicateResults.reviewed.length,1);
 assert.equal(button.disabled,false);assert.equal(button.innerHTML,'delete');
 ctx.authFetch=async()=>({ok:false,body:{error:'failure'}});
 await vm.runInContext('deleteArchivedBuckets()',ctx);
 assert.deepEqual([...selected],['b']);assert.equal(button.disabled,false);
 ctx.authFetch=async()=>null;
 await vm.runInContext('deleteArchivedBuckets()',ctx);
 assert.equal(button.disabled,false);
 assert.equal(refreshes,1);
 for(const m of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g))new vm.Script(m[1]);
 console.log('PASS deletion: no full scan, double-click guard, partial failure selection, error/auth recovery');
})().catch(e=>{console.error(e);process.exitCode=1;});
