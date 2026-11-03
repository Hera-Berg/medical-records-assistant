import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE??'playwright');
const origin=process.env.HEALTH_ORIGIN??'http://127.0.0.1:7788';
const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 if(process.env.MUTATE_FIXTURE==='1'){
  assert.equal(origin,'http://127.0.0.1:7788');
  const login=await(await context.request.post(origin+'/api/auth/clinician/login',{data:{username:'james.smith',password:process.env.CARE_DEMO_PASSWORD}})).json();
  for(const key of ['critical-sodium','baseline']){
   for(const body of [{kind:'review',sources:[key]},{kind:'release',source:key,recipients:['patient'],message:'Fictional report shared for review.',confirmed:true}]){
    const r=await context.request.post(origin+'/api/clinician/issues/'+key+'/workflow',{data:{...body,request_id:crypto.randomUUID()},headers:{'X-Clinician-CSRF':login.csrf}});assert.equal(r.status(),200);
   }
  }
  await context.request.post(origin+'/api/auth/clinician/logout',{data:{},headers:{'X-Clinician-CSRF':login.csrf}});
 }
 const data=await(await context.request.get(origin+'/api/patient/timeline/feed')).json();
 assert.ok(data.events.length>=5);assert.ok(!JSON.stringify(data).includes('/clinician/'));
 await page.goto(origin+'/overview');await page.locator('#menu').getByRole('link',{name:'Timeline',exact:true}).click();await page.locator('.clinical-timeline-row').first().waitFor();assert.equal(await page.getByLabel('Date range',{exact:true}).inputValue(),'30');assert.equal(await page.locator('.clinical-timeline table').count(),0);assert.ok(await page.locator('.clinical-timeline-row').count()<=10);await page.screenshot({path:'/private/tmp/patient-timeline-desktop.png'});
 await page.getByLabel('Date range',{exact:true}).selectOption('all');
 while(await page.getByRole('button',{name:'Load earlier events',exact:true}).count())await page.getByRole('button',{name:'Load earlier events',exact:true}).click();
 assert.equal(await page.locator('.clinical-timeline-row').count(),data.events.length);
 for(const category of data.categories){await page.getByRole('button',{name:category,exact:true}).click();assert.equal(await page.locator('.clinical-timeline-row').count(),Math.min(10,data.events.filter(e=>e.category===category).length));}
 await page.getByRole('button',{name:'All',exact:true}).click();
 for(const row of data.events){assert.equal((await context.request.get(origin+row.source_url)).status(),200);assert.equal((await context.request.get(origin+row.original_url)).status(),200);}
 assert.equal((await context.request.get(origin+'/api/clinician/timeline/feed')).status(),401);
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/private/tmp/patient-timeline-mobile.png'});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
 assert.deepEqual(errors,[]);console.log(`PASS ${origin}: patient feed, permitted source links, shared timeline styling, filters, date range, earlier history, desktop/mobile and clinician endpoint protection (${data.events.length} accessible sources).`);
}finally{await browser.close();}
