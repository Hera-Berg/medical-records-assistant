/** Comparison/reset, date provenance, summary and timezone browser verification. Developer-only Playwright/Chrome; credentials are supplied through the environment. */
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright');
import assert from 'node:assert/strict';
const origin=process.env.HEALTH_ORIGIN ?? 'http://127.0.0.1:7777';
if (!process.env.CARE_DEMO_PASSWORD) throw new Error('Supply CARE_DEMO_PASSWORD through the environment.');
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try {
 const context=await browser.newContext({viewport:{width:1440,height:1000},timezoneId:'America/Los_Angeles'});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 async function counts(){
  for(const section of await page.locator('[data-category]').all()) {
   assert.equal(Number(await section.locator('h2 .badge').innerText()),await section.locator('[data-summary]').count());
  }
 }
 async function dashboardDate(value){
  await page.locator('.comparison-form input[type=date]').fill(value);
  const response=page.waitForResponse(r=>r.url().includes('/api/clinician/dashboard?comparison_date='+value));
  await page.getByRole('button',{name:'Apply date',exact:true}).click();
  const data=await(await response).json();
  await page.waitForFunction(date=>document.querySelector('.comparison-period')?.textContent.includes(date),new Date(value+'T00:00:00Z').toLocaleDateString('en-AU',{timeZone:'Australia/Sydney',day:'numeric',month:'short',year:'numeric'}));
  await counts();return data;
 }
 for(const [user,ownDate] of [['james.smith','2026-10-21'],['olivia.bennett','2025-03-26']]){
  await page.goto(origin+'/clinician');await page.waitForURL('**/clinician/sign-in');
  await page.getByLabel('Username',{exact:true}).fill(user);await page.getByLabel('Password',{exact:true}).fill(process.env.CARE_DEMO_PASSWORD);
  await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForURL(origin+'/clinician');
  await page.getByRole('heading',{name:'Changes since your last review',exact:true}).waitFor();
  assert.equal(await page.locator('input[type=date]').inputValue(),ownDate);
  await page.locator('.patient-identity-strip').getByText('Jane Doe',{exact:true}).waitFor();
  assert.ok((await page.locator('.comparison-period').innerText()).includes('Australia/Sydney'));
  const original=await(await context.request.get(origin+'/api/clinician/dashboard')).json();
  assert.equal(original.comparison_period.mode,'last_consultation');assert.equal(original.comparison_period.start_date,ownDate);
  assert.ok(original.comparison_period.start_timestamp.includes('09:30:00'));
  await counts();
  const custom=await dashboardDate('2026-10-30');
  assert.equal(custom.comparison_period.mode,'custom');assert.equal(custom.comparison_period.start_timestamp,'2026-10-30T00:00:00+11:00');
  const request=custom.groups.follow_up.find(r=>r.id==='jane-history-68');assert.ok(request.outside_period);assert.equal(request.date,'2026-10-22');
  await page.getByText('Unresolved request predating the selected period',{exact:false}).waitFor();
  for(const row of custom.groups.newly_received){assert.ok(row.event_date<'2026-10-30'||!row.event_date);assert.ok(row.uploaded_at||row.received_date);}
  const reset=page.waitForResponse(r=>r.url()===origin+'/api/clinician/dashboard');
  await page.getByRole('button',{name:'Use last consultation',exact:true}).click();await reset;
  await page.waitForFunction(date=>document.querySelector('input[type=date]')?.value===date,ownDate);
  await counts();
  if(user==='james.smith'){
   assert.ok(original.groups.reported.some(r=>r.date_basis?.includes('Observation date not supplied')));
   assert.ok(original.groups.newly_received.length>0);
   await page.screenshot({path:'/private/tmp/period-desktop.png',fullPage:false});
   for(const rows of Object.values(original.groups))for(const row of rows){
    const response=await context.request.get(origin+row.source_url);assert.equal(response.status(),200,row.source_url);
    if(row.source_url.includes('/artifact/')) {const short=row.source_url.split('/').pop();assert.equal((await context.request.get(origin+'/api/clinician/artifact/'+short+'/meta')).status(),200);}
   }
  }
  await dashboardDate('2026-10-01');
  await page.getByRole('link',{name:'Appointment summary',exact:false}).click();await page.waitForURL('**/clinician/summary');
  await page.getByText('selected custom period',{exact:false}).first().waitFor();
  assert.equal(await page.getByLabel('Summary comparison from').inputValue(),'2026-10-01');
  const preview=page.waitForResponse(r=>r.url().endsWith('/api/clinician/summary/preview'));
  await page.getByRole('button',{name:'Use last consultation',exact:true}).click();
  const summary=await(await preview).json();assert.equal(summary.comparison_period.start_date,ownDate);assert.equal(summary.comparison_period.mode,'last_consultation');
  await page.waitForFunction(date=>document.querySelector('input[type=date]')?.value===date,ownDate);
  for(const section of summary.sections)for(const row of section.lines)for(const source of row.sources)assert.ok(source.citation.resolved);
  await page.getByRole('link',{name:/Review dashboard/}).click();
  await page.getByRole('heading',{name:'Changes since your last review',exact:true}).waitFor();
  assert.equal(await page.locator('input[type=date]').inputValue(),ownDate);
  await page.setViewportSize({width:390,height:844});await page.locator('#menu').waitFor({state:'detached'});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  if(user==='james.smith')await page.screenshot({path:'/private/tmp/period-mobile.png',fullPage:false});
  await page.getByRole('button',{name:'Sign out',exact:true}).click();await page.waitForURL('**/clinician/sign-in');
  assert.equal((await context.request.get(origin+'/api/clinician/summary')).status(),401);
  await page.setViewportSize({width:1440,height:1000});
 }
 await page.locator('.demo-workspace-switch select').selectOption('patient-carer');await page.waitForURL('**/overview');
 await page.goto(origin+'/summary');await page.getByText('Patient/carer summary:',{exact:false}).waitFor();
 assert.equal(await page.locator('.comparison-form').count(),0);
 assert.deepEqual(errors,[]);
 console.log('PASS Chrome: both own defaults, custom periods, reset, counts, genuine source links, historical uploads, unknown observation dates, unresolved older request, shared clinician summary period, account separation, patient summary basis and Sydney display in a non-Sydney browser');
}finally{await browser.close();}
