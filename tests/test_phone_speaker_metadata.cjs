// Exercise the adapter without opening a server or contacting an API.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../ombre-phone-proxy.mjs'), 'utf8');
const fn = source.slice(source.indexOf('export function adaptMessage'), source.indexOf('http.createServer')).replace('export function', 'function');
const ctx = {console: {log() {}}}; vm.createContext(ctx); vm.runInContext(fn, ctx);
const messages = [
  {role:'user', name:'甲', speaker_id:'1', content:'我下班了', timestamp:123},
  {role:'assistant', name:'乙', speaker:{id:'2', name:'乙'}, content:'我给甲留饭'},
  {role:'assistant', name:'丙', content:'我在开会', timestamp:125}
];
const request = {method:'tools/call', params:{name:'grow', arguments:{source:'group', messages}}};
const before = JSON.stringify(request);
const result = ctx.adaptMessage(request);
assert.deepEqual(JSON.parse(result.params.arguments.content).messages, messages);
assert.equal(JSON.stringify(request), before);
const native = {method:'tools/call', params:{name:'grow', arguments:{content:'甲与乙聊天'}}};
assert.equal(ctx.adaptMessage(native), native);
console.log('Speaker metadata preserved; native calls and source unchanged.');
