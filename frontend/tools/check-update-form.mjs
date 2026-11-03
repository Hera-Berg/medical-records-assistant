/** Mutating acceptance runs only against the disposable 7788 fixture. */
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE??'playwright');
const origin='http://127.0.0.1:7788';
const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(origin+'/add');await page.getByRole('heading',{name:'Record an update',exact:true}).waitFor();
 await page.getByText('Jane Doe',{exact:true}).first().waitFor();
 await page.getByLabel('Who is adding this update?').fill('Emily Doe');await page.getByLabel('Relationship or role').fill('Daughter');
 await page.getByLabel('Your update').fill('Fictional browser check: needs help walking to breakfast.');
 await page.screenshot({path:'/private/tmp/update-form-desktop.png'});
 await page.getByRole('button',{name:'Save update',exact:true}).click();await page.getByRole('status').filter({hasText:'Update saved'}).waitFor();
 await page.locator('input[type=file]').setInputFiles({name:'fictional-browser-upload.txt',mimeType:'text/plain',buffer:Buffer.from('Fictional uploaded evidence for browser verification.')});
 await page.getByText('fictional-browser-upload.txt',{exact:true}).first().waitFor();
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/private/tmp/update-form-mobile.png'});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:'/private/tmp/update-form-mobile.png'});
 const login=await context.request.post(origin+'/api/auth/clinician/login',{data:{username:'james.smith',password:process.env.CARE_DEMO_PASSWORD}});assert.equal(login.status(),200);
 const data=await(await context.request.get(origin+'/api/clinician/dashboard')).json();assert.ok(data.issues.some(i=>i.contributions.some(c=>c.author==='Emily Doe'&&c.original_text?.includes('Fictional browser check'))));
 await page.setViewportSize({width:1440,height:1000});await page.goto(origin+'/clinician/documents');await page.locator('.medical-document-list li').first().waitFor();
 const row=page.locator('.medical-document-list li').filter({hasText:'Electrolyte panel'});await row.getByRole('button',{name:/Release to patient file|Released · manage/}).click();
 if(!await page.getByRole('checkbox',{name:'Reviewed',exact:true}).isChecked())await page.getByRole('checkbox',{name:'Reviewed',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.document-release input[type=checkbox]')?.checked);
 if(!await page.getByRole('button',{name:'Preview release',exact:true}).isVisible())await page.locator('.document-release').getByRole('button',{name:'Release to patient file',exact:true}).click();
 await page.getByRole('button',{name:'Preview release',exact:true}).click();await page.getByRole('button',{name:'Confirm release',exact:true}).click();await row.getByRole('button',{name:'Released · manage',exact:true}).waitFor();
 const docs=await(await context.request.get(origin+'/api/patient/documents')).json();const released=docs.documents.find(d=>d.title==='Electrolyte panel');assert.ok(released.accessible);assert.equal((await context.request.get(origin+released.url)).status(),200);
 await page.screenshot({path:'/private/tmp/document-release-desktop.png'});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/private/tmp/document-release-mobile.png'});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.deepEqual(errors,[]);console.log('PASS: desktop/mobile form, attributed update in clinician review, upload, deliberate review and previewed document release to patient files.');
}finally{await browser.close();}
