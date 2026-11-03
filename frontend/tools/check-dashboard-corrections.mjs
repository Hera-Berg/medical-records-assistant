/** Source, attribution, grouping, count and responsive regression checks. Requires developer-only Playwright/Chrome and CARE_DEMO_PASSWORD. */
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright');
import assert from 'node:assert/strict';
const origin=process.env.HEALTH_ORIGIN ?? 'http://127.0.0.1:7777';
if (!process.env.CARE_DEMO_PASSWORD) throw new Error('Supply the demo password through the environment.');
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try {
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(origin+'/clinician');await page.waitForURL('**/clinician/sign-in');
 for(const [user,name,date] of [['james.smith','Dr James Smith','2026-10-21'],['olivia.bennett','Dr Olivia Bennett','2025-03-26']]) {
  await page.getByLabel('Username',{exact:true}).fill(user);await page.getByLabel('Password',{exact:true}).fill(process.env.CARE_DEMO_PASSWORD);
  await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForURL(origin+'/clinician');
  await page.getByRole('heading',{name:'Changes since your last review',exact:true}).waitFor();
  await page.locator('.patient-identity-strip').getByText('Jane Doe',{exact:true}).waitFor();
  assert.equal(await page.locator('input[type=date]').inputValue(),date);
  assert.ok((await page.locator('.clinician-account').innerText()).includes(name));
  const response=await context.request.get(origin+'/api/clinician/dashboard');assert.equal(response.status(),200);const data=await response.json();
  for(const [key,rows] of Object.entries(data.groups)) {
    const section=page.locator(`[data-category=${key}]`);
    assert.equal(await section.locator('h2 .badge').innerText(),String(rows.length));
    assert.equal(await section.locator('[data-summary]').count(),rows.length);
    for(const row of rows) {
      assert.equal((await context.request.get(origin+row.source_url)).status(),200);
      if(row.source_url.includes('/artifact/')) {
        const short=row.source_url.split('/').pop();const meta=await context.request.get(origin+'/api/clinician/artifact/'+short+'/meta');assert.equal(meta.status(),200);assert.ok((await meta.json()).present);
      } else {const note=await context.request.get(origin+'/api/clinician/observations/'+row.id);assert.equal(note.status(),200);assert.equal((await note.json()).text,row.summary);}
    }
  }
  assert.deepEqual(data.groups.follow_up.map(r=>r.id),['jane-history-68']);
  assert.ok(data.groups.reported.some(r=>r.author==='Jane Doe'&&r.date==='2026-10-23'));
  assert.ok(data.groups.reported.some(r=>r.author==='Emily Doe'&&r.date==='2026-10-24'));
  assert.ok(data.groups.reported.some(r=>r.author==='Morgan Bell'&&r.date==='2026-10-25'));
  assert.equal(await page.locator('.dashboard-appointments > ul > li').count(),data.upcoming_appointments.length);
  assert.ok(data.upcoming_appointments.length<=3);
  await page.locator('.past-attendance summary').click();assert.ok((await page.locator('.past-attendance').innerText()).includes('Attendance unknown'));
  assert.equal(await page.locator('.summary-provenance').count(),1);
  if(user==='james.smith'){
    assert.equal(data.groups.documents.length,0);assert.equal(data.groups.medications.length,0);
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:'/private/tmp/corrected-dashboard-desktop.png',fullPage:true});
    await page.locator('input[type=date]').fill('2026-10-17');await page.getByRole('button',{name:'Apply date',exact:true}).click();
    await page.locator('[data-summary="jane-history-64"]').waitFor();
    await page.locator('input[type=date]').fill(date);await page.getByRole('button',{name:'Apply date',exact:true}).click();
    await page.locator('[data-summary="jane-history-64"]').waitFor({state:'detached'});
  }
  for(const route of ['record','documents','timeline','progress-notes']) {
    await page.goto(origin+'/clinician/'+route);
    await page.locator('.patient-identity-strip').getByText('Jane Doe',{exact:true}).waitFor();
    assert.ok((await page.locator('.patient-identity-strip').innerText()).includes('1 Nov 1935'));
    if(route==='record') assert.ok(!(await page.locator('main').innerText()).includes('your own documents'));
    if(route==='progress-notes') await page.getByText('Password protected. Please enter your password.',{exact:true}).waitFor();
  }
  await page.goto(origin+'/clinician');await page.getByRole('heading',{name:'Changes since your last review',exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});await page.locator('#menu').waitFor({state:'detached'});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:`/private/tmp/corrected-dashboard-${user}-mobile.png`,fullPage:false});
  await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('button',{name:'Sign out',exact:true}).click();await page.waitForURL('**/clinician/sign-in');
  assert.equal(await page.locator('.patient-identity-strip').count(),0);
  assert.equal((await context.request.get(origin+'/api/clinician/dashboard')).status(),401);
 }
 assert.deepEqual(errors,[]);
 console.log('PASS: both dates/accounts, every genuine source, displayed counts, all three attributed new demo observations, original typed notes, explicit unresolved request, 3 upcoming bookings, unknown past attendance, identity strip, mobile, sign-out and note lock');
} finally { await browser.close(); }
