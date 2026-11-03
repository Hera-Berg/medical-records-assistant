/** Session-only progress-note access. Use MUTATE_FIXTURE=1 only on disposable port 7788. */
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE??'playwright');
const origin=process.env.HEALTH_ORIGIN??'http://127.0.0.1:7777',mutate=process.env.MUTATE_FIXTURE==='1';
if(mutate&&!origin.endsWith(':7788'))throw Error('Mutations require the disposable fixture.');
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH??'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1440,height:900}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 async function login(user){await page.goto(origin+'/clinician/progress-notes');await page.getByLabel('Username',{exact:true}).fill(user);await page.getByLabel('Password',{exact:true}).fill(process.env.CARE_DEMO_PASSWORD);await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForURL('**/clinician');await page.locator('#menu').getByRole('link',{name:'Progress notes',exact:false}).click();await page.getByText('Add a progress note',{exact:true}).waitFor();}
 assert.equal((await context.request.get(origin+'/api/clinician/progress-notes')).status(),401);
 await login('james.smith');assert.equal(await page.locator('input[type=password]').count(),0);assert.equal(await page.getByRole('button',{name:'Lock',exact:true}).count(),0);
 const before=await(await context.request.get(origin+'/api/clinician/progress-notes')).json();assert.equal(before.legacy_locked_count,0);
 if(mutate){await page.getByText('Add a progress note',{exact:true}).click();await page.getByLabel('Note',{exact:true}).fill('Isolated progress note for session access verification.');await page.getByRole('button',{name:'Save progress note',exact:true}).click();await page.getByRole('status').filter({hasText:'Progress note saved.'}).waitFor();}
 else{assert.equal(before.notes.length,21);await page.screenshot({path:'/private/tmp/progress-notes-session-desktop.png'});}
 await page.reload();await page.getByText('Add a progress note',{exact:true}).waitFor();assert.equal(await page.locator('input[type=password]').count(),0);
 const notes=await(await context.request.get(origin+'/api/clinician/progress-notes')).json();assert.equal(notes.notes.length,before.notes.length+(mutate?1:0));
 if(mutate)assert.equal(notes.notes[0].author,'Dr James Smith');
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);if(!mutate)await page.screenshot({path:'/private/tmp/progress-notes-session-mobile.png'});await page.setViewportSize({width:1440,height:900});
 await page.getByRole('button',{name:'Sign out',exact:true}).click();await page.waitForURL('**/clinician/sign-in');assert.equal((await context.request.get(origin+'/api/clinician/progress-notes')).status(),401);
 await login('olivia.bennett');const other=await(await context.request.get(origin+'/api/clinician/progress-notes')).json();assert.equal(other.notes.length,notes.notes.length);assert.equal(await page.locator('input[type=password]').count(),0);
 assert.deepEqual(errors,[]);console.log('PASS: no secondary password/lock, existing notes available immediately, refresh persistence, both clinician accounts, sign-out/API protection and mobile layout'+(mutate?', isolated save uses authenticated author.':'.'));
}finally{await browser.close();}
