const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = readFileSync('frontend/dashboard.html', 'utf8');
const source = html.slice(html.indexOf('let duplicateResults ='), html.indexOf('let ingestRetryTimer;'));
const calls = [];
let fail = false;
let rendered;
const context = vm.createContext({
  BASE:'', confirm:()=>true, alert:()=>{},
  loadBuckets:async()=>{},
  loadDuplicates:async()=>{throw Error('Review must not rescan');},
  renderDuplicates:d=>{rendered = JSON.parse(JSON.stringify(d));},
  fetch:async(url, options)=>{
    calls.push(JSON.parse(options.body));
    return {ok:!fail, json:async()=>({id:'decision', error:'failed'})};
  },
});
vm.runInContext(source, context);
const pair = (a,b)=>({a:{id:a,hash:a},b:{id:b,hash:b},score:.9});
const seed = [pair('a','b'),pair('a','c'),pair('b','c'),pair('d','e')];
async function run(code){return vm.runInContext(code,context);}
(async()=>{
  await run(`duplicateResults = ${JSON.stringify({pairs:seed,total:4,reviewed:[]})}`);
  await run("duplicateDecision('dismiss','a','b','a','b')");
  assert.equal(rendered.total,3);
  assert.deepEqual(rendered.pairs.map(p=>[p.a.id,p.b.id]),[['a','c'],['b','c'],['d','e']]);
  await run("duplicateDecision('archive','a','c','a','c')");
  assert.equal(rendered.total,1);
  assert.equal(rendered.pairs[0].a.id,'d');
  assert.equal(rendered.reviewed[0].archived,'c');
  fail=true;
  await run("duplicateDecision('dismiss','d','e','d','e')");
  assert.equal(rendered.total,1);
  fail=false;
  await run("duplicateDecision('dismiss','e','d','e','d')");
  assert.equal(rendered.total,0);
  assert.equal(calls.length,4);
  // Parse every inline script, including the extracted rendering function.
  for(const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) {
    new vm.Script(match[1]);
  }
  console.log('PASS: sequential dismiss/archive, unrelated pairs retained, failure retry, inline syntax');
})().catch(error=>{console.error(error);process.exitCode=1;});
