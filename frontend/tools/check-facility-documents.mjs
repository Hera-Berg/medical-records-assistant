/** Paper-layout integration and exact release boundaries. Mutations only on 7788. */
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE??'playwright');
const origin=process.env.HEALTH_ORIGIN??'http://127.0.0.1:7788',mutate=process.env.MUTATE_FIXTURE==='1';
if(mutate)assert.equal(origin,'http://127.0.0.1:7788');
const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1500,height:1100}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const login=await(await context.request.post(origin+'/api/auth/clinician/login',{data:{username:'james.smith',password:process.env.CARE_DEMO_PASSWORD}})).json();assert.ok(login.csrf);
 const dashboard=await(await context.request.get(origin+'/api/clinician/dashboard')).json();assert.equal(dashboard.comparison_date,'2026-10-21');
 const docs=await(await context.request.get(origin+'/api/clinician/document-list')).json();
 const names=['Dr Matthew Thompson - Cardiologist letter','Olivia Reed - Audiologist letter','Sarah Green - Physiotherapist letter','Daniel Brooks - Podiatrist letter'];
 for(const name of names)assert.ok(docs.documents.some(d=>d.title===name));
 assert.ok(docs.documents.some(d=>d.title==='Full blood examination'));
 await page.goto(origin+'/clinician/documents');await page.locator('.medical-document-list').waitFor();
 const timeline=await(await context.request.get(origin+'/api/clinician/timeline/feed')).json();
 for(const name of names)assert.ok(timeline.events.some(d=>d.title===name));
 let protectedSource;
 for(const name of [...names,'Full blood examination','Electrolyte panel']){
  const document=docs.documents.find(d=>d.title===name);assert.ok(document.url);const short=document.url.split('/').at(-1);
  const meta=await(await context.request.get(origin+'/api/clinician/artifact/'+short+'/meta')).json();assert.equal(meta.native.title,name);assert.ok(meta.native.original_document_layout);
  const raw=await context.request.get(origin+'/api/clinician/artifact/'+short);assert.equal(raw.status(),200);assert.ok(raw.headers()['content-disposition'].includes(name+'.pdf'));assert.ok((await raw.body()).subarray(0,4).toString()==='%PDF');
  await page.goto(origin+document.url);await page.locator('.native-issued-page').first().waitFor();assert.equal(await page.locator('.native-issued-page').first().getAttribute('src'),'/api/clinician/artifact/'+short+'/pages/1');assert.equal(await page.locator('.docs-letter h2').count(),0);
  if(name===names[0]){await page.waitForFunction(()=>{const im=document.querySelector('.native-issued-page');return im?.complete&&im.naturalWidth>0;});await page.screenshot({path:'/private/tmp/facility-document-browser-desktop.png'});protectedSource=short;}
 }
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/private/tmp/facility-document-browser-mobile.png'});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
 if(mutate){
  const key='critical-sodium';
  for(const body of [{kind:'review',sources:[key]},{kind:'release',source:key,recipients:['patient'],message:'Your selected laboratory report.',confirmed:true}]){
   const r=await context.request.post(origin+'/api/clinician/issues/'+key+'/workflow',{data:{...body,request_id:crypto.randomUUID()},headers:{'X-Clinician-CSRF':login.csrf}});assert.equal(r.status(),200);
  }
 }
 const patientDocs=await(await context.request.get(origin+'/api/patient/documents')).json();
 for(const d of patientDocs.documents.filter(d=>d.accessible&&d.url))assert.equal((await context.request.get(origin+'/api/artifact/'+d.url.split('/').at(-1))).status(),200);
 assert.equal((await context.request.get(origin+'/api/artifact/'+protectedSource)).status(),404);
 await context.request.post(origin+'/api/auth/clinician/logout',{data:{},headers:{'X-Clinician-CSRF':login.csrf}});
 assert.equal((await context.request.get(origin+'/api/clinician/artifact/'+protectedSource)).status(),401);
 const bennett=await context.request.post(origin+'/api/auth/clinician/login',{data:{username:'olivia.bennett',password:process.env.CARE_DEMO_PASSWORD}});assert.equal(bennett.status(),200);
 const other=await(await context.request.get(origin+'/api/clinician/dashboard')).json();assert.equal(other.comparison_date,'2025-03-26');
 assert.deepEqual(errors,[]);console.log(`PASS ${origin}: canonical titles across records, generated filenames, original PDF frames, desktop/mobile, source permissions, exact document release and personal comparison dates.`);
}finally{await browser.close();}
