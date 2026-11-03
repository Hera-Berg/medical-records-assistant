/** Developer verification; credentials and optional external Playwright path come from env. */
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE??'playwright');
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH??'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
const origin=process.env.HEALTH_ORIGIN??'http://127.0.0.1:7777';
try{
 const context=await browser.newContext({viewport:{width:1440,height:1000},timezoneId:'America/Los_Angeles'});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(origin+'/clinician');
 await page.getByLabel('Username',{exact:true}).fill('james.smith');
 await page.getByLabel('Password',{exact:true}).fill(process.env.CARE_DEMO_PASSWORD);
 await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await page.getByRole('heading',{name:'Changes since your last review',exact:true}).waitFor();
 const data=await(await context.request.get(origin+'/api/clinician/dashboard')).json();
 assert.ok(data.issues.length>5);
 assert.equal(await page.locator('.issue-row').count(),5);
 assert.ok(!data.issues.some(i=>i.headline.toLowerCase()==='why i'));
 assert.equal(data.issues.find(i=>i.id==='walking-support').source_count,2);
 assert.equal(await page.locator('.original-contribution').count(),0);
 assert.equal(await page.locator('.review-notice details').getAttribute('open'),null);
 const headlines=await page.locator('.issue-row strong').allTextContents();console.log('Initial headlines',headlines);
 await page.screenshot({path:'/private/tmp/compact-review-desktop.png'});
 await page.getByRole('button',{name:`View all changes (${data.issues.length})`,exact:true}).click();
 assert.equal(await page.locator('.issue-row').count(),data.issues.length);
 for(const issue of data.issues){
   await page.locator(`[data-summary="${issue.id}"] button`).click();
   await page.getByRole('heading',{name:issue.headline,exact:true}).waitFor();
   assert.equal(await page.locator('.evidence-contribution').count(),issue.source_count);
   for(const source of issue.contributions){assert.equal((await context.request.get(origin+source.source_url)).status(),200);}
   await page.getByRole('link',{name:'Back to review',exact:false}).click();
   await page.getByRole('heading',{name:'Changes since your last review',exact:true}).waitFor();
   assert.equal(await page.locator('.issue-row').count(),data.issues.length);
 }
 await page.locator('.comparison-form input').fill('2026-10-01');
 const response=page.waitForResponse(r=>r.url().includes('dashboard?comparison_date=2026-10-01'));
 await page.getByRole('button',{name:'Apply date',exact:true}).click();const custom=await(await response).json();
 await page.waitForFunction(n=>document.querySelectorAll('.issue-row').length===n,custom.issues.length);
 await page.evaluate(()=>scrollTo(0,500));await page.waitForTimeout(100);const scroll=await page.evaluate(()=>scrollY);
 await page.locator('.issue-row').first().evaluate(el=>el.click());await page.getByRole('link',{name:'Back to review',exact:false}).click();
 await page.waitForFunction(n=>Math.abs(scrollY-n)<5,scroll);
 assert.equal(await page.locator('.comparison-form input').inputValue(),'2026-10-01');
 await page.getByRole('button',{name:'Use last consultation',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('input[type=date]').value==='2026-10-21');
 await page.setViewportSize({width:390,height:844});await page.evaluate(()=>scrollTo(0,0));
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.screenshot({path:'/private/tmp/compact-review-mobile.png'});
 await page.locator('.issue-row').first().click();await page.locator('.evidence-contribution').first().waitFor();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.getByRole('button',{name:'Sign out',exact:true}).click();
 assert.equal((await context.request.get(origin+'/api/clinician/dashboard')).status(),401);
 assert.deepEqual(errors,[]);
 console.log('PASS: five-row limit, issue/source counts, all detail/source destinations, walking grouping, custom/reset, return expansion/date/scroll, desktop/mobile, signed-out protection');
}finally{await browser.close();}
