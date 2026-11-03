/** Read-only acceptance check for the active port-7777 demo (session changes only). */
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE??'playwright');
const origin=process.env.HEALTH_ORIGIN??'http://127.0.0.1:7777';
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH??'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1440,height:900},timezoneId:'America/Los_Angeles'}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 for(const [username,date] of [['james.smith','2026-10-21'],['olivia.bennett','2025-03-26']]){
  await page.goto(origin+'/clinician');await page.getByLabel('Username',{exact:true}).fill(username);await page.getByLabel('Password',{exact:true}).fill(process.env.CARE_DEMO_PASSWORD);await page.getByRole('button',{name:'Sign in',exact:true}).click();
  await page.getByRole('heading',{name:'Changes since your last appointment',exact:true}).waitFor();
  const data=await(await context.request.get(origin+'/api/clinician/dashboard')).json();assert.equal(data.comparison_date,date);assert.equal(await page.locator('.issue-row').count(),Math.min(5,data.tab_counts.needs_review));
  const left=await page.locator('.review-list-panel').boundingBox(),right=await page.locator('.review-detail-panel').boundingBox();assert.ok(right.x>=left.x+left.width);
  assert.equal(await page.getByText('Reading documents on this computer needs a one-time download.',{exact:false}).count(),0);
  await page.locator('.patient-identity-strip').getByText('Jane Doe',{exact:true}).waitFor();
  if(username==='james.smith'){
   await page.screenshot({path:'/private/tmp/review-final-desktop.png'});
   const issue=data.issues.find(i=>i.id==='critical-sodium'),artifact=issue.contributions[0].artifact;
   await page.goto(origin+'/clinician/artifact/'+artifact);await page.locator('.native-letter').waitFor();
   assert.ok((await page.locator('.docs-lab-results').innerText()).includes('135–145'));
   const raw=await page.getByRole('link',{name:'Download original',exact:true}).getAttribute('href');assert.equal((await context.request.get(origin+raw)).status(),200);assert.equal((await context.request.get(origin+'/api/artifact/'+artifact)).status(),issue.workflow.releases.some(r=>r.source==='critical-sodium')?200:404);
   await page.getByRole('link',{name:'Back to review',exact:false}).click();await page.locator('.issue-row').first().waitFor();
   await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/private/tmp/review-final-mobile.png'});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.goto(origin+'/clinician/issue/critical-sodium');await page.getByRole('dialog').waitFor();await page.screenshot({path:'/private/tmp/review-final-detail.png'});
   await page.getByRole('link',{name:'Back to review',exact:false}).click();await page.setViewportSize({width:1440,height:900});
  }
  await page.getByRole('button',{name:'Sign out',exact:true}).click();await page.waitForURL('**/clinician/sign-in');assert.equal((await context.request.get(origin+'/api/clinician/dashboard')).status(),401);
 }
 await page.locator('.demo-workspace-switch select').selectOption('patient-carer');await page.waitForURL('**/overview');
 await page.goto(origin+'/care-team');await page.locator('.care-team-grid').first().waitFor();
 assert.equal(await page.locator('.care-team-grid img').count(),3);
 for(const img of await page.locator('.care-team-grid img').all()){await img.waitFor();assert.ok(await img.evaluate(el=>el.complete&&el.naturalWidth>0));}
 const feed=await(await context.request.get(origin+'/api/shared-care')).json();assert.ok(feed.simulation.includes('Shared patient/carer demo view'));
 assert.deepEqual(errors,[]);console.log('PASS active 7777: latest dashboard, both personal dates, five rows, existing states preserved, protected originals, readable report, two-column desktop/mobile detail, sign-out protection and retained care-team photographs.');
}finally{await browser.close();}
