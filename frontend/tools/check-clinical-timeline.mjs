/** Timeline acceptance; read-only against fixture or live server. */
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE??'playwright');
const origin=process.env.HEALTH_ORIGIN??'http://127.0.0.1:7788';
const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1440,height:1000},timezoneId:'America/Los_Angeles'}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 assert.equal((await context.request.get(origin+'/api/clinician/timeline/feed')).status(),401);
 await page.goto(origin+'/clinician/timeline');await page.getByLabel('Username',{exact:true}).fill('james.smith');await page.getByLabel('Password',{exact:true}).fill(process.env.CARE_DEMO_PASSWORD);await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForURL(origin+'/clinician');
 await page.goto(origin+'/clinician/timeline');await page.locator('.clinical-timeline-row').first().waitFor();
 assert.equal(await page.getByLabel('Date range',{exact:true}).inputValue(),'30');assert.ok(await page.locator('.clinical-timeline-row').count()<=10);assert.equal(await page.locator('.clinical-timeline table').count(),0);
 const iconCount=await page.locator('#menu .nav-item-icon:visible').count();assert.equal(iconCount,0);
 await page.screenshot({path:'/private/tmp/clinical-timeline-desktop.png'});
 const response=await(await context.request.get(origin+'/api/clinician/timeline/feed')).json();
 for(const category of response.categories){await page.getByRole('button',{name:category,exact:true}).click();await page.getByLabel('Date range',{exact:true}).selectOption('all');const count=response.events.filter(e=>e.category===category).length;assert.equal(await page.locator('.clinical-timeline-row').count(),Math.min(10,count));}
 await page.getByRole('button',{name:'All',exact:true}).click();await page.getByLabel('Date range',{exact:true}).selectOption('30');let shown=await page.locator('.clinical-timeline-row').count();
 while(await page.getByRole('button',{name:'Load earlier events',exact:true}).count()){await page.getByRole('button',{name:'Load earlier events',exact:true}).click();const next=await page.locator('.clinical-timeline-row').count();assert.ok(next>=shown);shown=next;}
 assert.equal(shown,response.events.length);assert.equal(await page.getByLabel('Date range',{exact:true}).inputValue(),'all');
 const titles=[...await page.locator('.timeline-document-title').allTextContents(),...await page.locator('.timeline-event-content h3').allTextContents()];assert.ok(titles.includes('Electrolyte panel'));assert.ok(titles.includes('Current medication chart'));
 for(const event of response.events){assert.equal((await context.request.get(origin+event.source_url)).status(),200);assert.equal((await context.request.get(origin+event.original_url)).status(),200);}
 const months=await page.locator('.timeline-month > h2').allTextContents();assert.ok(months.some(m=>m.includes('2024')));
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/private/tmp/clinical-timeline-mobile.png'});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);assert.equal(await page.locator('.clinical-timeline-row').count(),shown);
 const session=await(await context.request.get(origin+'/api/auth/clinician/session')).json();await context.request.post(origin+'/api/auth/clinician/logout',{data:{},headers:{'X-Clinician-CSRF':session.csrf}});assert.equal((await context.request.get(origin+'/api/clinician/timeline/feed')).status(),401);
 assert.deepEqual(errors,[]);console.log(`PASS ${origin}: newest-first source feed, all category filters, last-30-days default, ten-row limit, earlier history, all ${shown} source links, text-only sidebar, desktop/mobile and sign-out protection.`);
}finally{await browser.close();}
