/** Responsibilities: Share language selection, system-language resolution and safe DOM translation.
 * Implementation: Match original text and parameters through a dictionary; WeakMap retains source text
 * for immediate bidirectional switching. MutationObserver handles controller/Leaflet labels without
 * changing business contracts.
 * Related Modules: translations.js, index.html, maps.html, core.camera.ASSETS.
 * Declaration Index: resolveLanguage; translate; localizePage; localizePage.render;
 * localizePage.visit; localizePage.refresh; localizePage.choose; localizePage.systemChanged;
 * localizePage.storageChanged; localizePage.changed.
 * Variable Index: STORAGE_KEY — same-origin language preference; patterns — dictionary/parameter matchers.
 */
import { translations } from './translations.js';
export const STORAGE_KEY = 'prs.language';
const patterns = Object.entries(translations).map(([source, target]) => ({
  source, target,
  regex: new RegExp('^' + source.split(/\{\d+\}/).map(part =>
    part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('(.+?)') + '$'),
}));

/** Functionality: Resolve the language preference. Inputs: preference and browser languages array.
 * Outputs: zh-CN or en. Logic: Explicit selection wins; a primary zh system language selects Chinese.
 * Constraints: Unsupported system languages use English; processing/location parameters are unchanged. */
export function resolveLanguage(preference, languages = []) {
  if (preference === 'zh-CN' || preference === 'en') return preference;
  return /^zh(?:-|$)/i.test(languages[0] || '') ? 'zh-CN' : 'en';
}

/** Functionality: Translate one UI text segment. Inputs: source text and target language.
 * Outputs: Translated or original text. Logic: Preserve surrounding whitespace; match dictionary entries
 * only, recursively translating parameter labels.
 * Constraints: Preserve unknown external diagnostics and original ORS instructions; never insert HTML. */
export function translate(text, language) {
  if (language !== 'en') return text;
  const normalized = text.trim().replace(/\s+/g, ' ');
  for (const entry of patterns) {
    const match = entry.regex.exec(normalized);
    if (!match) continue;
    const translated = entry.target.replace(/\{(\d+)\}/g,
      (_, index) => translate(match[Number(index) + 1], language));
    return text.match(/^\s*/)[0] + translated + text.match(/\s*$/)[0];
  }
  // Translate only known API diagnostic prefixes, preserving error codes and request identifiers.
  const suffix = normalized.indexOf('（');
  if (suffix > 0 && Object.hasOwn(translations, normalized.slice(0, suffix)))
    return translations[normalized.slice(0, suffix)] + normalized.slice(suffix);
  return text;
}

/** Functionality: Install the page language selector and translation lifecycle.
 * Inputs: Injectable doc/win DOM and window; Outputs: refresh function and registered listeners.
 * Logic: Read same-origin preferences, observe system/tab changes and track source/rendered text
 * to prevent observer loops.
 * Constraints: Store only the language enum; log storage failures and retain the page's selection;
 * request no permissions or network access. */
export function localizePage(doc = document, win = window) {
  const select = doc.getElementById('language');
  const originals = new WeakMap();
  let preference = 'system';
  try {
    const stored = win.localStorage.getItem(STORAGE_KEY);
    if (['en', 'zh-CN', 'system'].includes(stored)) preference = stored;
  } catch (error) {
    console.warn('[PRS language] Cannot read preference:', error.name);
  }
  let language;
  /** Functionality: Maintain source text for one node/attribute. Inputs: node/key/value/set.
   * Outputs: Safe text updates. Logic: External edits refresh source text; language edits reuse it.
   * Constraints: Do not rewrite form values or retain removed nodes. */
  function render(node, key, value, set) {
    const records = originals.get(node) || {};
    let record = records[key];
    if (!record || value !== record.rendered) record = { source: value };
    record.rendered = translate(record.source, language);
    records[key] = record;
    originals.set(node, records);
    if (value !== record.rendered) set(record.rendered);
  }
  /** Functionality: Traverse translatable DOM. Inputs: node. Outputs: Text and alt/aria-label/title updates.
   * Logic: Skip scripts, styles, user input and original ORS instructions; retain elements/events.
   * Constraints: No innerHTML; ignore comments and nodes other than elements/text. */
  function visit(node) {
    if (node.nodeType === 3) {
      if (node.parentElement?.closest('script,style,textarea,[data-language-original]')) return;
      render(node, 'text', node.nodeValue, value => { node.nodeValue = value; });
      return;
    }
    if (node.nodeType !== 1 || node.matches('script,style,textarea,[data-language-original]')) return;
    for (const attribute of ['alt', 'aria-label', 'title']) {
      if (node.hasAttribute(attribute)) render(node, attribute, node.getAttribute(attribute),
        value => node.setAttribute(attribute, value));
    }
    for (const child of node.childNodes) visit(child);
  }
  /** Functionality: Refresh the current page. Inputs: Current preference and navigator.
   * Outputs: Document lang, selector and UI text. Logic: Resolve system language on each refresh.
   * Constraints: Do not reinitialize maps/cameras or resend requests. */
  function refresh() {
    language = resolveLanguage(preference, win.navigator.languages || [win.navigator.language]);
    doc.documentElement.lang = language;
    select.value = preference;
    visit(doc.documentElement);
  }
  /** Functionality: Apply explicit user selection. Inputs: select.value.
   * Outputs: Saved preference, UI refresh and diagnostic logs. Logic: Accept only three enum values.
   * Constraints: Do not claim persistence after storage failure or log user data. */
  function choose() {
    if (!['en', 'zh-CN', 'system'].includes(select.value)) return;
    preference = select.value;
    try { win.localStorage.setItem(STORAGE_KEY, preference); }
    catch (error) { console.warn('[PRS language] Preference not saved:', error.name); }
    console.info('[PRS language] Preference changed:', preference);
    refresh();
  }
  /** Functionality: Handle system-language updates. Inputs: languagechange.
   * Outputs: Refresh when needed. Logic: Apply only in system mode. Constraints: Retain explicit selection. */
  function systemChanged() { if (preference === 'system') refresh(); }
  /** Functionality: Synchronize same-origin tab preferences. Inputs: StorageEvent.
   * Outputs: Valid enum or system mode after deletion. Logic: Handle only this setting key.
   * Constraints: Ignore invalid values; do not write back to storage. */
  function storageChanged(event) {
    if (event.key !== STORAGE_KEY && event.key !== null) return;
    if (event.newValue !== null && !['en', 'zh-CN', 'system'].includes(event.newValue)) return;
    preference = event.newValue || 'system';
    refresh();
  }
  /** Functionality: Translate asynchronous UI changes. Inputs: MutationRecord array.
   * Outputs: Text for added/changed nodes. Logic: Reuse source records to prevent self-triggered loops.
   * Constraints: Do not change server responses, coordinate values or processing state. */
  function changed(records) {
    for (const record of records) {
      if (record.type === 'characterData') visit(record.target);
      else if (record.type === 'attributes') visit(record.target);
      else for (const node of record.addedNodes) visit(node);
    }
  }
  select.addEventListener('change', choose);
  win.addEventListener('languagechange', systemChanged);
  win.addEventListener('storage', storageChanged);
  refresh();
  new win.MutationObserver(changed).observe(doc.documentElement, {
    subtree: true, childList: true, characterData: true, attributes: true,
    attributeFilter: ['alt', 'aria-label', 'title'],
  });
  return refresh;
}

if (typeof document !== 'undefined' && document.getElementById('language')) localizePage();
