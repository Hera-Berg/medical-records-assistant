/** Read-only acceptance of the active patient portal; no bookings or corrections are written. */
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE??'playwright');
const origin=process.env.HEALTH_ORIGIN??'http://127.0.0.1:7777';
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH??'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1440,height:1000},timezoneId:'America/Los_Angeles'}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));await page.goto(origin+'/overview');await page.locator('#menu').getByRole('link',{name:'Notifications',exact:false}).waitFor();
 assert.equal(await page.locator('#menu').getByRole('link',{name:'My health record',exact:false}).count(),0);assert.equal(await page.locator('#menu').getByRole('link',{name:'Confirm extracted information',exact:false}).count(),0);
 await page.locator('#menu').getByRole('link',{name:'Notifications',exact:false}).click();await page.locator('.notifications-page h1').waitFor();const feed=await(await context.request.get(origin+'/api/shared-care')).json();await page.waitForFunction(n=>document.querySelectorAll('.notification-card').length===n,feed.messages.length);
 await page.locator('#menu').getByRole('link',{name:'Documents',exact:false}).click();await page.locator('.medical-document-list li').first().waitFor();assert.equal(await page.locator('.medical-document-list li').count(),10);const titles=await page.locator('.medical-document-list h2').allTextContents();assert.ok(titles.includes('Electrolyte panel'));assert.ok(titles.includes('Dr Matthew Thompson - Cardiologist letter'));assert.equal(await page.getByRole('button',{name:'My uploads',exact:false}).count(),0);assert.equal(await page.getByRole('button',{name:'Example documents',exact:false}).count(),0);await page.screenshot({path:'/private/tmp/live-patient-documents.png'});
 assert.equal((await context.request.get(origin+'/api/review')).status(),401);
 await page.locator('#menu').getByRole('link',{name:'Record an update',exact:false}).click();await page.getByRole('heading',{name:'Your submitted notes',exact:true}).waitFor();await page.getByRole('button',{name:'Correct this note',exact:true}).first().waitFor();
 await page.setViewportSize({width:390,height:844});await page.goto(origin+'/documents');await page.locator('.medical-document-list li').first().waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.goto(origin+'/notifications');await page.locator('.notifications-page h1').waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.deepEqual(errors,[]);console.log('PASS active 7777: Notifications route, ten medical documents, source names, removed patient clinical navigation, submitted-note corrections available, clinical confirmation protected and mobile layout. No active record writes.');
}finally{await browser.close();}
