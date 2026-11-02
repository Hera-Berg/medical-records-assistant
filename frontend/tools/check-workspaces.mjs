/** Exercise the built workspace shell against a running demo server.
 * HEALTH_ORIGIN=http://127.0.0.1:7788 node tools/check-workspaces.mjs
 * Uses jsdom; it verifies navigation and rendered content, not browser layout.
 */
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const origin = process.env.HEALTH_ORIGIN ?? 'http://127.0.0.1:7777';
const directory = new URL('../../agent/server/static_files/', import.meta.url);
const assets = new URL('assets/', directory);
const bundle = readFileSync(new URL(readdirSync(assets).find(n => n.endsWith('.js')), assets), 'utf8');
const wait = async (condition) => {
  for (let n = 0; n < 100; n++) {
    if (condition()) return;
    await new Promise(resolve => setTimeout(resolve, 30));
  }
  assert.fail('Timed out waiting for the workspace UI');
};
async function open(path, preference, storageBlocked = false, wide = true) {
  assert.equal((await fetch(origin + path)).status, 200, `Server route ${path}`);
  const dom = new JSDOM('<div id="root"></div>', {
    url: origin + path, pretendToBeVisual: true, runScripts: 'outside-only',
  });
  const w = dom.window;
  w.matchMedia = () => ({ matches: wide, addEventListener() {}, removeEventListener() {} });
  w.scrollTo = () => {};
  w.HTMLElement.prototype.scrollIntoView = () => {};
  w.HTMLDialogElement.prototype.close = () => {};
  w.fetch = (url, init) => fetch(new URL(url, origin), init);
  if (preference) w.localStorage.setItem('health-agent.demo-workspace', preference);
  if (storageBlocked) Object.defineProperty(w, 'localStorage', { get() { throw new Error('Storage blocked'); } });
  for (const script of ['overview.js', 'patient-profile.js', 'documents.js', 'calendar.js']) {
    w.eval(readFileSync(new URL(script, directory), 'utf8'));
  }
  w.eval(bundle);
  await wait(() => w.document.querySelector('.demo-workspace-switch select'));
  return w;
}
function switchTo(w, workspace) {
  const select = w.document.querySelector('.demo-workspace-switch select');
  select.value = workspace;
  select.dispatchEvent(new w.Event('change', { bubbles: true }));
}
function click(w, path) {
  const link = w.document.querySelector(`#menu a[href="${path}"]`);
  assert.ok(link, `Navigation link ${path}`);
  link.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
}
const w = await open('/overview');
try {
  await wait(() => w.document.querySelector('health-overview h2'));
  assert.equal(w.document.querySelector('select').value, 'patient-carer');
  switchTo(w, 'clinician');
  await wait(() => w.location.pathname === '/clinician' && w.document.querySelector('[aria-label="Review dashboard placeholder"]'));
  await wait(() => w.document.querySelector('h1').textContent === 'Review dashboard');
  click(w, '/timeline');
  await wait(() => w.location.pathname === '/timeline');
  assert.equal(w.document.querySelector('select').value, 'clinician');
  click(w, '/documents');
  await wait(() => w.location.pathname === '/documents');
  assert.equal(w.document.querySelector('select').value, 'clinician');
  click(w, '/clinician');
  await wait(() => w.document.querySelector('[aria-label="Review dashboard placeholder"]'));
  switchTo(w, 'patient-carer');
  await wait(() => w.document.querySelector('health-overview'));
  assert.equal(w.document.querySelector('#menu a[href="/progress-notes"]'), null);
  click(w, '/calendar');
  await wait(() => w.location.pathname === '/calendar');
  assert.equal(w.document.querySelector('select').value, 'patient-carer');
  switchTo(w, 'clinician');
  await wait(() => w.document.querySelector('[aria-label="Review dashboard placeholder"]'));
  w.history.back();
  await wait(() => w.location.pathname === '/calendar');
  assert.equal(w.document.querySelector('select').value, 'clinician');
} finally { w.close(); }
for (const [path, stored, expected, blocked] of [
  ['/clinician', 'patient-carer', 'clinician'],
  ['/overview', 'clinician', 'patient-carer'],
  ['/', 'clinician', 'patient-carer'],
  ['/timeline', 'clinician', 'clinician'],
  ['/artifact/example', 'clinician', 'clinician'],
  ['/clinician', null, 'clinician', true],
]) {
  const page = await open(path, stored, blocked);
  try {
    assert.equal(page.document.querySelector('select').value, expected, path);
    if (blocked) {
      switchTo(page, 'patient-carer');
      await wait(() => page.document.querySelector('health-overview'));
      assert.equal(page.document.querySelector('select').value, 'patient-carer');
    }
  } finally { page.close(); }
}
console.log('PASS: workspace homes, switch, shared routes, refresh preference, Back and blocked storage');

const destinations = {
  clinician: [
    ['/clinician', 'Review dashboard', '[aria-label="Review dashboard placeholder"]'],
    ['/record', 'Clinical record', 'main h2'],
    ['/documents', 'Documents', 'health-documents'],
    ['/timeline', 'Timeline', 'main'],
    ['/progress-notes', 'Clinical notes', 'main form'],
  ],
  'patient-carer': [
    ['/', 'Home', 'health-overview'],
    ['/add', 'Record an update', 'main'],
    ['/calendar', 'Calendar', 'health-calendar'],
    ['/documents', 'Shared documents', 'health-documents'],
    ['/care-team', 'My care team', '.care-team-grid'],
  ],
};
for (const [workspace, links] of Object.entries(destinations)) {
  const page = await open(workspace === 'clinician' ? '/clinician' : '/overview');
  try {
    assert.equal(page.document.querySelector('[aria-label="Close the menu"]'), null, 'Desktop sidebar stays visible');
    const primary = page.document.querySelector('[aria-label="Workspace navigation"]');
    assert.deepEqual([...primary.querySelectorAll('a')].map(a => a.textContent.trim().replace(' (showing now)', '')), links.map(l => l[1]));
    for (const [path, label, selector] of [...links, ['/profile', workspace === 'clinician' ? 'Patient profile' : 'My profile', 'patient-profile'], ['/settings', 'Settings', 'main']]) {
      click(page, path);
      await wait(() => page.location.pathname === path && page.document.querySelector(selector));
      assert.equal(page.document.querySelector('.demo-workspace-switch select').value, workspace);
      const active = page.document.querySelectorAll('#menu [aria-current="page"]');
      assert.equal(active.length, 1, `${workspace} ${path} active link count`);
      assert.ok(active[0].textContent.includes(label), `${workspace} ${path} active label`);
      assert.doesNotMatch(page.document.querySelector('main').textContent, /There is no page at/);
      if (path === '/progress-notes') {
        await wait(() => /Password protected|passphrase|password/i.test(page.document.querySelector('main').textContent));
        assert.equal(page.document.querySelector('textarea'), null, 'Clinical notes remain locked');
      }
    }
    const toolPaths = [...page.document.querySelectorAll('[aria-label="Record tools"] a')].map(a => a.getAttribute('href'));
    for (const path of toolPaths) {
      click(page, path);
      await wait(() => page.location.pathname === path && page.document.querySelector('#menu [aria-current="page"]')?.getAttribute('href') === path);
      assert.equal(page.document.querySelector('.demo-workspace-switch select').value, workspace);
      assert.doesNotMatch(page.document.querySelector('main').textContent, /There is no page at/);
    }
    assert.equal(page.document.querySelector('#menu a[href="/doctor-notes"]'), null);
  } finally { page.close(); }
}
const mobile = await open('/overview', null, false, false);
try {
  assert.equal(mobile.document.querySelector('#menu'), null);
  const openMenu = () => mobile.document.querySelector('[aria-label="Open the menu"]').click();
  openMenu();
  await wait(() => mobile.document.querySelector('#menu'));
  click(mobile, '/care-team');
  await wait(() => mobile.location.pathname === '/care-team' && !mobile.document.querySelector('#menu'));
  openMenu();
  await wait(() => mobile.document.querySelector('#menu'));
  mobile.dispatchEvent(new mobile.KeyboardEvent('keydown', {key:'Escape'}));
  await wait(() => !mobile.document.querySelector('#menu'));
} finally { mobile.close(); }
console.log('PASS: all workspace navigation destinations, active links, locked notes, secondary links and collapsible mobile menu');
