/** Run against the Jane demo with the two local passwords supplied via environment.
 * Requires Playwright (developer tool only) and an installed Chrome.
 * PLAYWRIGHT_MODULE/CHROME_PATH can point to externally installed tools.
 */
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright');
import assert from 'node:assert/strict';
const origin=process.env.HEALTH_ORIGIN ?? 'http://127.0.0.1:7777';
if (!process.env.CARE_JAMES_PASSWORD || !process.env.CARE_OLIVIA_PASSWORD) throw new Error('Set both demo passwords through the environment; do not put them in this file.');
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try {
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 const page=await context.newPage(); const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(origin+'/overview');
 await page.locator('.demo-workspace-switch select').selectOption('clinician');
 await page.waitForURL('**/clinician/sign-in');
 assert.equal(await page.locator('.review-dashboard').count(),0);
 await page.getByLabel('Username',{exact:true}).fill('james.smith');
 await page.getByLabel('Password',{exact:true}).fill('incorrect-password');
 await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await page.getByRole('alert').filter({hasText:'Incorrect username or password.'}).waitFor();
 const accounts=[['james.smith',process.env.CARE_JAMES_PASSWORD,'Dr Smith','Dr James Smith','21 Oct 2026'],['olivia.bennett',process.env.CARE_OLIVIA_PASSWORD,'Dr Bennett','Dr Olivia Bennett','26 Mar 2025']];
 let revoked;
 for(const [username,password,salutation,name,last] of accounts) {
  await page.getByLabel('Username',{exact:true}).fill(username);
  await page.getByLabel('Password',{exact:true}).fill(password);
  await page.getByRole('button',{name:'Sign in',exact:true}).click();
  await page.waitForURL(origin+'/clinician');
  await page.locator('.clinician-greeting').filter({hasText:salutation}).waitFor();
  assert.equal(await page.locator('.clinician-account').getByText(name,{exact:true}).count(),1);
  assert.ok((await page.locator('[aria-label="Patient and consultations"]').innerText()).includes(last));
  assert.match(await page.locator('.clinician-greeting').innerText(),new RegExp(`Good (morning|afternoon|evening), ${salutation}`));
  const cookie=(await context.cookies()).find(c=>c.name==='care_clinician_session');
  assert.ok(cookie.httpOnly);assert.equal(cookie.sameSite,'Strict');
  assert.ok(!(await page.evaluate(()=>document.cookie)).includes(cookie.value));
  await page.reload();await page.locator('.clinician-greeting').filter({hasText:salutation}).waitFor();
  assert.ok((await page.locator('[aria-label="Patient and consultations"]').innerText()).includes(last));
  assert.equal(await page.locator('input[type="date"]').inputValue(),username==='james.smith'?'2026-10-21':'2025-03-26');
  await page.screenshot({path:`/private/tmp/dashboard-${username}.png`,fullPage:false});
  const source=page.getByRole('link',{name:'View original source',exact:true}).first();
  await source.click();await page.waitForURL('**/clinician/artifact/**');
  await page.locator('main').getByText('Reading…',{exact:true}).waitFor({state:'detached'});
  await page.getByRole('link',{name:/Clinical notes/}).click();
  await page.waitForURL('**/clinician/progress-notes');
  await page.getByText('Password protected. Please enter your password.',{exact:true}).waitFor();
  assert.equal(await page.locator('textarea').count(),0);
  await page.getByRole('link',{name:/Review dashboard/}).click();
  await page.locator('.clinician-greeting').waitFor();
  await page.setViewportSize({width:390,height:844});
  await page.locator('#menu').waitFor({state:'detached'});
  await page.screenshot({path:`/private/tmp/dashboard-mobile-${username}.png`,fullPage:false});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.setViewportSize({width:1440,height:1000});
  revoked=cookie.value;
  await page.getByRole('button',{name:'Sign out',exact:true}).click();
  await page.waitForURL('**/clinician/sign-in');
  assert.equal(await page.locator('.clinician-account').count(),0);
  assert.equal(await page.locator('.review-dashboard').count(),0);
  const response=await context.request.get(origin+'/api/clinician/dashboard');assert.equal(response.status(),401);
  await page.goto(origin+'/clinician');await page.waitForURL('**/clinician/sign-in');
 }
 const clean=await browser.newContext();
 for(const route of ['/clinician','/clinician/record','/clinician/progress-notes','/doctor-notes']) {
  const r=await clean.request.get(origin+route,{maxRedirects:0});assert.equal(r.status(),303,route);
 }
 for(const route of ['/api/clinician/dashboard','/api/clinician/wiki','/api/clinician/timeline','/api/clinician/profile','/api/doctor-notes']) {
  const r=await clean.request.get(origin+route);assert.equal(r.status(),401,route);
 }
 await clean.addCookies([{name:'care_clinician_session',value:revoked,url:origin}]);
 assert.equal((await clean.request.get(origin+'/api/clinician/dashboard')).status(),401);
 await page.locator('.demo-workspace-switch select').selectOption('patient-carer');
 await page.waitForURL('**/overview');await page.locator('health-overview h2').waitFor();
 assert.deepEqual(errors,[]);
 console.log('PASS Chrome: incorrect/correct sign-in, both personalised identities and review dates, refresh, protected originals, locked notes, mobile, logout/replay protection, direct routes/APIs and patient flow');
}finally{await browser.close();}
