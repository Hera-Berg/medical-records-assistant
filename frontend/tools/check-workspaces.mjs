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
async function open(path, preference, storageBlocked = false) {
  assert.equal((await fetch(origin + path)).status, 200, `Server route ${path}`);
  const dom = new JSDOM('<div id="root"></div>', {
    url: origin + path, pretendToBeVisual: true, runScripts: 'outside-only',
  });
  const w = dom.window;
  w.matchMedia = () => ({ matches: true, addEventListener() {}, removeEventListener() {} });
  w.scrollTo = () => {};
  w.fetch = (url, init) => fetch(new URL(url, origin), init);
  if (preference) w.localStorage.setItem('health-agent.demo-workspace', preference);
  if (storageBlocked) Object.defineProperty(w, 'localStorage', { get() { throw new Error('Storage blocked'); } });
  w.eval(readFileSync(new URL('overview.js', directory), 'utf8'));
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
  await wait(() => w.location.pathname === '/clinician' && w.document.querySelector('.clinician-home'));
  assert.match(w.document.querySelector('main').textContent, /Review dashboard/);
  click(w, '/timeline');
  await wait(() => w.location.pathname === '/timeline');
  assert.equal(w.document.querySelector('select').value, 'clinician');
  click(w, '/documents');
  await wait(() => w.location.pathname === '/documents');
  assert.equal(w.document.querySelector('select').value, 'clinician');
  click(w, '/clinician');
  await wait(() => w.document.querySelector('.clinician-home'));
  switchTo(w, 'patient-carer');
  await wait(() => w.document.querySelector('health-overview'));
  click(w, '/progress-notes');
  await wait(() => w.location.pathname === '/progress-notes');
  assert.equal(w.document.querySelector('select').value, 'patient-carer');
  switchTo(w, 'clinician');
  await wait(() => w.document.querySelector('.clinician-home'));
  w.history.back();
  await wait(() => w.location.pathname === '/progress-notes');
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
