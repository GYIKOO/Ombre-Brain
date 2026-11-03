const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const html=readFileSync('frontend/dashboard.html','utf8');
const code=html.slice(html.indexOf('let archiveDeleteBusy ='),html.indexOf('async function hardDeleteSelectedTests()'));
const button={innerHTML:'delete',disabled:false};
let requests=0,resolveRequest,refreshes=0;
const selected=new Set(['a','b']);
const ctx=vm.createContext({
 currentFilter:'archived',developerMode:false,prompt:()=> '清空全部归档',
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
 assert.equal(refreshes,3);
 // Neither hidden controls nor mixed selections may delete active memories.
 let batches=[];
 ctx.authFetch=async(url,opts)=>{const ids=JSON.parse(opts.body).ids;batches.push(ids);return {ok:true,body:{deleted:ids,errors:[]}};};
 ctx.currentFilter='all';
 await vm.runInContext('deleteArchivedBuckets()',ctx);
 assert.equal(batches.length,0);
 ctx.currentFilter='archived';
 await vm.runInContext('deleteArchivedBuckets(null,true)',ctx);
 assert.equal(batches.length,0);
 ctx.allBuckets.push({id:'active',type:'dynamic',score:0.1});selected.add('active');
 await vm.runInContext('deleteArchivedBuckets()',ctx);
 assert.equal(batches.length,0);
 // Clear-all ignores selection/page, snapshots only archived IDs, and batches requests.
 ctx.developerMode=true;
 ctx.allBuckets=Array.from({length:235},(_,i)=>({id:'archive-'+i,type:'archived'})).concat({id:'active',type:'dynamic'});
 ctx.prompt=()=> 'wrong';
 await vm.runInContext('deleteArchivedBuckets(null,true)',ctx);
 assert.equal(batches.length,0);
 ctx.prompt=()=> '清空全部归档';
 await vm.runInContext('deleteArchivedBuckets(null,true)',ctx);
 assert.deepEqual(batches.map(b=>b.length),[100,100,35]);
 assert.equal(new Set(batches.flat()).size,235);
 assert(!batches.flat().includes('active'));
 assert(selected.has('active'));
 // A later failed batch retains earlier successes, refreshes, and releases the guard.
 batches=[];
 ctx.authFetch=async(url,opts)=>{const ids=JSON.parse(opts.body).ids;batches.push(ids);if(batches.length===2)throw Error('offline');return {ok:true,body:{deleted:ids,errors:[]}};};
 selected.add('archive-0');selected.add('archive-150');
 await vm.runInContext('deleteArchivedBuckets(null,true)',ctx);
 assert.equal(batches.length,2);assert(!selected.has('archive-0'));assert(selected.has('archive-150'));
 assert.equal(button.disabled,false);
 vm.runInContext(html.slice(html.indexOf('function filterBuckets('),html.indexOf('// 翻页状态：')),ctx);
 assert.deepEqual(Array.from(ctx.filterBuckets([{id:'a',type:'archived'},{id:'b',type:'dynamic',score:0.1}]),b=>b.id),['a']);
 assert.match(html,/body\.archive-view\.developer-mode \.archive-only\.developer-only/);
 assert.match(html,/class="icon-btn archive-only developer-only"/);
 for(const m of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g))new vm.Script(m[1]);
 console.log('PASS deletion: no full scan, double-click guard, partial failure selection, error/auth recovery');
})().catch(e=>{console.error(e);process.exitCode=1;});
