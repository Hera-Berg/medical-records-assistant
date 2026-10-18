// Browser-only preference: no record data or server settings are changed.
const KEY = 'health-agent-theme';
function savedTheme() {
  try { return localStorage.getItem(KEY) === 'dark' ? 'dark' : 'light'; }
  catch { return 'light'; }
}
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
}
applyTheme(savedTheme());

class HealthAppearance extends HTMLElement {
  connectedCallback() {
    if (this.childElementCount) return;
    this.innerHTML = `<fieldset class="appearance-settings">
      <legend>Appearance</legend>
      <p>Choose how your record looks. This choice is saved in this browser.</p>
      <div class="appearance-options">
        <label><input type="radio" name="appearance" value="light"> Light</label>
        <label><input type="radio" name="appearance" value="dark"> Dark</label>
      </div>
      <p class="appearance-status" role="status"></p>
    </fieldset>`;
    this.sync();
    this.onChange = (event) => {
      const input = event.target;
      if (!(input instanceof HTMLInputElement)) return;
      applyTheme(input.value);
      let message = `${input.value === 'dark' ? 'Dark' : 'Light'} mode selected.`;
      try { localStorage.setItem(KEY, input.value); }
      catch { message += ' This browser cannot save your choice after you close the page.'; }
      this.querySelector('[role="status"]').textContent = message;
    };
    this.onStorage = (event) => {
      if (event.key === KEY || event.key === null) {
        applyTheme(savedTheme());
        this.sync();
      }
    };
    this.addEventListener('change', this.onChange);
    window.addEventListener('storage', this.onStorage);
  }
  sync() {
    for (const input of this.querySelectorAll('input')) {
      input.checked = input.value === document.documentElement.dataset.theme;
    }
  }
  disconnectedCallback() {
    this.removeEventListener('change', this.onChange);
    window.removeEventListener('storage', this.onStorage);
    this.replaceChildren();
  }
}
customElements.define('health-appearance', HealthAppearance);
// Keep other open tabs in step even when Settings is not mounted.
window.addEventListener('storage', (event) => {
  if (event.key === KEY || event.key === null) applyTheme(savedTheme());
});
