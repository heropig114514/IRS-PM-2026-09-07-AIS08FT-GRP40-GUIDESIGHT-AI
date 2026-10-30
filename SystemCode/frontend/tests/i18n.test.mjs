/** Responsibilities: Verify language resolution, dictionary placeholders, source-text protection, and bidirectional DOM switching.
 * Implementation: Use explicit DOM/storage/event fixtures; do not request camera, GPS, or services.
 * Related Modules: i18n.js, translations.js.
 * Declaration Index: testResolution; testCatalog; testDom; testDom.element;
 * testDom.element.matches; testDom.element.hasAttribute; testDom.element.getAttribute;
 * testDom.element.setAttribute; testDom.element.addEventListener;
 * testDom.Observer; testDom.Observer.constructor; testDom.Observer.observe.
 * Variable Index: None; test-local variables hold controlled DOM, storage, and window events.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveLanguage, translate, localizePage, STORAGE_KEY } from '../i18n.js';
import { translations } from '../translations.js';

/** Functionality: Verify browser-preferred language and explicit overrides. Inputs: Fixed language enums/lists.
 * Outputs: Resolution assertions. Logic: Cover Chinese locales, English, other languages, and empty lists.
 * Constraints: Does not claim to verify real operating-system settings UI. */
test('language resolution uses browser preference and honors explicit choices', function testResolution() {
  for (const locale of ['zh-CN', 'zh-TW', 'zh-HK', 'ZH']) assert.equal(resolveLanguage('system', [locale]), 'zh-CN');
  for (const languages of [[], ['en-US'], ['fr-FR', 'zh-CN']]) assert.equal(resolveLanguage('system', languages), 'en');
  assert.equal(resolveLanguage('en', ['zh-CN']), 'en');
  assert.equal(resolveLanguage('zh-CN', ['en-US']), 'zh-CN');
});

/** Functionality: Verify all dictionary entries and dynamic fields. Inputs: Actual dictionary and fixed values.
 * Outputs: Assertions for complete English coverage, preserved Chinese source text, and diagnostic identifiers.
 * Logic: Substitute each template field, then call the real translation function.
 * Constraints: Unknown third-party text remains unchanged; this does not review translation quality or verify external services. */
test('catalog translates all entries and preserves diagnostic identifiers', function testCatalog() {
  for (const [source, target] of Object.entries(translations)) {
    const sample = source.replace(/\{(\d+)\}/g, (_, index) => `value-${index}`);
    const expected = target.replace(/\{(\d+)\}/g, (_, index) => `value-${index}`);
    assert.equal(translate(sample, 'en'), expected, source);
    assert.equal(translate(sample, 'zh-CN'), sample);
  }
  assert.equal(translate('YOLO26n 检测 80 类；Hugging Face ZeroGPU 返回标注图像。', 'en'), 'YOLO26n detects 80 classes; Hugging Face ZeroGPU returns annotated images.');
  assert.equal(translate('连接尚未就绪（busy / abc-123）', 'en'), 'Connection not ready（busy / abc-123）');
  assert.equal(translate('  尚未连接\n', 'en'), '  Not connected\n');
  assert.equal(translate('Unknown provider diagnostic', 'en'), 'Unknown provider diagnostic');
});

/** Functionality: Verify persistence, immediate switching, dynamic status, and system/cross-tab events.
 * Inputs: Explicit minimal DOM, storage, and MutationObserver fixtures.
 * Outputs: Text, attribute, lang, and storage assertions. Logic: Dispatch registered callbacks directly.
 * Constraints: No real browser layout/event scheduling; fixtures do not replace real-device verification. */
test('DOM switching retains source strings and follows system and storage events', function testDom() {
  /** Functionality: Create an element fixture for this test. Inputs: children and attrs.
   * Outputs: Writable element. Logic: Implement DOM methods used by translation.
   * Constraints: Does not parse HTML or simulate browser rendering. */
  function element(children = [], attrs = {}) {
    return { nodeType: 1, childNodes: children, attrs, handlers: {},
      /** Functionality: Check the source-text protection marker. Inputs: selector. Outputs: Boolean.
       * Logic: The fixture recognizes only this protection marker. Constraints: Does not implement full CSS. */
      matches(selector) { return selector.includes('[data-language-original]') && Object.hasOwn(attrs, 'data-language-original'); },
      /** Functionality: Check for an attribute. Inputs: key. Outputs: Boolean. Logic: Check own keys only. Constraints: No inherited properties. */
      hasAttribute(key) { return Object.hasOwn(attrs, key); },
      /** Functionality: Read an attribute. Inputs: key. Outputs: Value. Logic: Read directly. Constraints: Test uses a fixed attribute set. */
      getAttribute(key) { return attrs[key]; },
      /** Functionality: Write an attribute. Inputs: key/value. Outputs: Updated attrs. Logic: Assign directly. Constraints: Does not dispatch observer events automatically. */
      setAttribute(key, value) { attrs[key] = value; },
      /** Functionality: Store an event handler. Inputs: name/handler. Outputs: Updated handlers. Logic: One listener per name. Constraints: Tests invoke handlers directly. */
      addEventListener(name, handler) { this.handlers[name] = handler; },
    };
  }
  const text = { nodeType: 3, nodeValue: '尚未连接' };
  const original = { nodeType: 3, nodeValue: '左转' };
  const select = element();
  const root = element([text, select, element([original], {'data-language-original': ''})], {'aria-label': '摄像头回传工作台'});
  const events = {}, saved = new Map();
  let observe;
  /** Functionality: Capture the actual observer callback. Logic: Dispatch changes explicitly by hand.
   * Constraints: Does not simulate the browser microtask queue. */
  class Observer {
    /** Functionality: Store the callback. Inputs: callback. Outputs: Updated observe reference. Logic: Retain the reference. Constraints: Do not call automatically. */
    constructor(callback) { observe = callback; }
    /** Functionality: Accept observer configuration. Inputs: None; registration is implicit. Outputs: None. Logic: Do not observe. Constraints: Tests dispatch manually. */
    observe() {}
  }
  const win = { navigator: { languages: ['en-US'] }, MutationObserver: Observer,
    localStorage: {getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value)},
    addEventListener: (name, handler) => { events[name] = handler; },
  };
  localizePage({documentElement: root, getElementById: () => select}, win);
  assert.equal(text.nodeValue, 'Not connected');
  assert.equal(root.attrs['aria-label'], 'Camera feedback workspace');
  assert.equal(original.nodeValue, '左转');
  select.value = 'zh-CN'; select.handlers.change();
  assert.equal(saved.get(STORAGE_KEY), 'zh-CN');
  assert.equal(text.nodeValue, '尚未连接');
  select.value = 'en'; select.handlers.change();
  text.nodeValue = '正在检测'; observe([{type: 'characterData', target: text}]);
  assert.equal(text.nodeValue, 'Detecting');
  select.value = 'zh-CN'; select.handlers.change();
  assert.equal(text.nodeValue, '正在检测');
  win.navigator.languages = ['en-US']; events.languagechange();
  assert.equal(root.lang, 'zh-CN');
  select.value = 'system'; select.handlers.change();
  assert.equal(root.lang, 'en');
  win.navigator.languages = ['zh-TW']; events.languagechange();
  assert.equal(root.lang, 'zh-CN');
  events.storage({key: STORAGE_KEY, newValue: 'en'});
  assert.equal(root.lang, 'en');
  events.storage({key: STORAGE_KEY, newValue: null});
  assert.equal(select.value, 'system');
});
