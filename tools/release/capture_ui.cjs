// Genuine UI captures without an HTTP server. Every page request is fulfilled
// from bounded source files or fictional data, or aborted; none is forwarded.
const {chromium} = require('playwright');
const fs = require('fs'), path = require('path'), crypto = require('crypto');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, 'docs/screenshots');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const items = [
  {uid:'PIS-20260101-120000Z-DEMO', title:'Verify a reproducible artifact', status:'in_progress', slug:'verify-artifact', tags:['synthetic','verification']},
  {uid:'PIS-20260101-120100Z-DEMO', title:'Resume an interrupted workflow', status:'ready_for_implement', slug:'resume-workflow', tags:['synthetic','recovery']},
  {uid:'PIS-20260101-120200Z-DEMO', title:'Record the evidence boundary', status:'verified', slug:'record-evidence', tags:['synthetic','documentation']},
].map(row => ({...row, project_id:'causyl', updated_utc:'2026-01-01T12:00:00Z', pis_excerpt:'Fictional interface fixture. No operational work was performed.'}));
function api(url) {
  if (url.pathname === '/api/ledger/items') return {items};
  if (url.pathname === '/api/mobile/cockpit') return {pis:{items:items.map(row=>({uid:row.uid,kind:'pis',meta:row})),topic_catalog:[],groups:[],scheduled_bindings:{}}};
  if (url.pathname.startsWith('/api/ledger/item/')) {
    const item = items.find(row => url.pathname.endsWith(row.uid)) || items[0];
    return {uid:item.uid,meta:item,pis_markdown:'# '+item.title+'\n\nFictional interface preview.\n\n## Acceptance\n\n- Inspect the artifact identity.\n- Record the validation result.\n- Preserve unresolved proof gaps.\n',actions:[]};
  }
  return {ok:true,status:'idle',items:[],runs:[],tasks:[],schedules:[],agents:[],workflows:[],session:[],events:[],counts:{},config:{},projects:[{project_id:'causyl',title:'Causyl'}]};
}
(async()=>{
  fs.mkdirSync(output,{recursive:true});
  const browser = await chromium.launch({headless:true,args:['--disable-background-networking']});
  const context = await browser.newContext({serviceWorkers:'block',viewport:{width:1440,height:1000}});
  const inputs = new Map(), requests = [], errors = [], screenshots = [];
  if (context.routeWebSocket) await context.routeWebSocket('**/*', socket => socket.close());
  await context.route('**/*', async route => {
    const req=route.request(), url=new URL(req.url());
    requests.push({method:req.method(),path:url.pathname});
    if(url.origin!=='http://causyl.invalid' || req.method()!=='GET') return route.abort('blockedbyclient');
    if(url.pathname.startsWith('/api/')) return route.fulfill({json:api(url)});
    let rel;
    if(url.pathname==='/') rel='index.html';
    else if(url.pathname==='/agents') rel='agents.html';
    else if(url.pathname==='/mobile/backlog') rel='mobile_backlog.html';
    else if(url.pathname.startsWith('/static/')) rel=url.pathname.slice('/static/'.length);
    else return route.fulfill({status:404,body:'Not in the interface fixture.'});
    const base=path.join(root,'infra/webui/static'), file=path.resolve(base,rel);
    if(!file.startsWith(base+path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) return route.abort('blockedbyclient');
    const bytes=fs.readFileSync(file), name=path.relative(root,file).split(path.sep).join('/');
    inputs.set(name,sha(bytes));
    return route.fulfill({body:bytes,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html'});
  });
  async function capture(name, url, viewport, ready) {
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    await page.setViewportSize(viewport);
    await page.goto('http://causyl.invalid'+url);
    await page.waitForSelector(ready, {state:"attached"});
    await page.waitForTimeout(500);
    if(name==='change-ledger.png') {
      await page.locator('[data-lg-uid="'+items[0].uid+'"]').click();
      await page.waitForTimeout(200);
    }
    if(name==='agent-surface.png') {
      await page.locator('#pisSelect').selectOption(items[0].uid);
      await page.waitForTimeout(200);
    }
    const bytes=await page.screenshot({path:path.join(output,name),animations:'disabled'});
    screenshots.push({path:'docs/screenshots/'+name,sha256:sha(bytes),...viewport});
    await page.close();
  }
  try {
    await capture('change-ledger.png','/',{width:1440,height:1000},'[data-lg-uid]');
    await capture('agent-surface.png','/agents',{width:1440,height:1000},'#pisSelect option[value="'+items[0].uid+'"]');
    await capture('operator-mobile.png','/mobile/backlog',{width:390,height:1000},'#pisSelect option[value="'+items[0].uid+'"]');
    if(errors.length) throw Error(errors.join('; '));
    const receipt={mode:'isolated_real_ui_synthetic_responses',operator_service_started:false,network_requests_sent:0,provider_or_agent_calls:0,
      captured_utc:new Date().toISOString(),browser_version:browser.version(),playwright_version:require('playwright/package.json').version,
      capture_script:'tools/release/capture_ui.cjs',capture_script_sha256:sha(fs.readFileSync(__filename)),
      screenshots,source_assets:[...inputs].map(([path,sha256])=>({path,sha256})),
      request_count:requests.length,blocked_mutation_attempts:requests.filter(r=>r.method!=='GET').length,script_errors:errors,
      interpretation:'Rendered source interfaces with fictional data; no operator workflow or agent execution is demonstrated.'};
    fs.writeFileSync(path.join(output,'CAPTURE_PROVENANCE.json'),JSON.stringify(receipt,null,2)+'\n');
    console.log(JSON.stringify({screenshots: screenshots.length,script_errors:errors,network_requests_sent:0,blocked_mutation_attempts:receipt.blocked_mutation_attempts}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
