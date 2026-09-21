import http from 'node:http';
const host = process.argv[2];
if (!host || !(host === '127.0.0.1' || /^10\.0\.0\.\d+$/.test(host))) throw new Error('Expected loopback or local Wi-Fi IPv4 address');
const upstreamPort = Number(process.argv[3] || 18001);
if (!Number.isInteger(upstreamPort) || upstreamPort < 1 || upstreamPort > 65535) throw new Error('Invalid upstream port');
export function adaptMessage(message) {
  if (message?.method === 'tools/call') {
    const shape = Object.fromEntries(Object.entries(message.params?.arguments || {}).map(([key,value]) => [key, value === null ? 'null' : Array.isArray(value) ? {type:'array',length:value.length,itemKeys:value[0] && typeof value[0]==='object' ? Object.keys(value[0]) : undefined} : typeof value === 'object' ? {type:'object',keys:Object.keys(value)} : {type:typeof value,...(typeof value==='string'?{length:value.length}:{})}]));
    console.log(JSON.stringify({event:'tool-call-shape',time:new Date().toISOString(),tool:message.params?.name,arguments:shape}));
  }
  if (message?.method === 'tools/call' && message.params?.name === 'hold') {
    const args = message.params.arguments || {};
    if (['text', 'memory', 'topics', 'emotion', 'timestamp'].some(key => Object.hasOwn(args,key))) {
      const content = args.content ?? args.text ?? args.memory;
      if (typeof content !== 'string' || !content.trim()) return message;
      const allowed = ['title','tags','importance','pinned','feel','source_bucket','valence','arousal','why_remembered','meaning','media','test_data','domain','source_content','source_ranges','quotes'];
      const normalized = {...Object.fromEntries(Object.entries(args).filter(([key]) => allowed.includes(key))), content};
      if (!Object.hasOwn(normalized,'tags') && Array.isArray(args.topics) && args.topics.every(t=>typeof t==='string')) normalized.tags = args.topics.join(',');
      // Keep source metadata as evidence, without duplicating or rewriting the summary body.
      if (!normalized.source_content) normalized.source_content = 'Phone summary source metadata:\n' + JSON.stringify({content,topics:args.topics,emotion:args.emotion,timestamp:args.timestamp,_actor:args._actor});
      console.log(JSON.stringify({event:'summary-adaptation',contentLength:content.length,resultKeys:Object.keys(normalized)}));
      return {...message,params:{...message.params,arguments:normalized}};
    }
  }
  if (message?.method === 'tools/call' && message.params?.name === 'grow') {
    const args = message.params.arguments || {};
    // Only adapt the observed phone format. Native grow calls remain unchanged.
    if (Array.isArray(args.messages) && args.messages.length && !Object.hasOwn(args, 'content') && !Object.hasOwn(args, 'items')) {
      const valid = args.messages.every(item => item && typeof item.role === 'string' && typeof item.content === 'string');
      if (!valid) return message;
      const content = JSON.stringify({source:args.source, timestamp:args.timestamp, roundsCount:args.roundsCount, _actor:args._actor, messages:args.messages}, null, 2);
      const normalized = {content};
      if (typeof args.test_data === 'boolean') normalized.test_data = args.test_data;
      console.log(JSON.stringify({event:'raw-dialogue-adaptation',messages:args.messages.length,contentLength:content.length}));
      return {...message,params:{...message.params,arguments:normalized}};
    }
  }
  if (message?.method !== 'tools/call' || message.params?.name !== 'breath_search') return message;
  const args = message.params.arguments || {};
  const aliases = ['question', 'limit', 'top_k', 'n_results'];
  if (!aliases.some(key => Object.hasOwn(args, key))) return message;
  const allowed = ['query', 'domain', 'max_results', 'date_from', 'date_to', 'quotes', 'mode', 'with_ids'];
  const normalized = Object.fromEntries(Object.entries(args).filter(([key]) => allowed.includes(key)));
  normalized.query = args.query || args.question || '';
  const count = args.max_results ?? args.limit ?? args.top_k ?? args.n_results;
  if (count !== undefined) normalized.max_results = Number(count);
  normalized.mode = 'automatic';
  console.log(JSON.stringify({event:'memory-parameter-adaptation',keys:Object.keys(args),resultKeys:Object.keys(normalized)}));
  return {...message, params:{...message.params, arguments:normalized}};
}
http.createServer(async (req, res) => {
  if (req.url !== '/mcp' || !['GET', 'POST', 'DELETE', 'OPTIONS'].includes(req.method)) {
    res.writeHead(404); res.end(); return;
  }
  const headers = { host: `127.0.0.1:${upstreamPort}` };
  for (const key of ['authorization', 'content-type', 'accept', 'origin', 'mcp-session-id', 'mcp-protocol-version', 'last-event-id', 'access-control-request-method', 'access-control-request-headers']) {
    if (req.headers[key]) headers[key] = req.headers[key];
  }
  let body;
  if (req.method === 'POST') {
    try {
      const chunks = []; let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 8 * 1024 * 1024) { res.writeHead(413); res.end(); return; }
        chunks.push(chunk);
      }
      body = Buffer.concat(chunks);
      try {
        const message = JSON.parse(body.toString('utf8'));
        body = Buffer.from(JSON.stringify(Array.isArray(message) ? message.map(adaptMessage) : adaptMessage(message)));
      } catch { /* Let the MCP server report malformed JSON. */ }
    } catch { res.writeHead(400); res.end(); return; }
  }
  const upstream = http.request({host:'127.0.0.1',port:upstreamPort,path:'/mcp',method:req.method,headers}, reply => {
    res.writeHead(reply.statusCode, reply.headers);
    reply.pipe(res);
  });
  upstream.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end(); });
  res.on('close', () => upstream.destroy());
  if (body) upstream.end(body);
  else req.pipe(upstream);
}).listen(4188, host, () => console.log(`Ombre MCP phone endpoint: http://${host}:4188/mcp`));
