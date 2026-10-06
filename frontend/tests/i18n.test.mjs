/** Responsibilities: 验证语言解析、词典参数、原文保护及 DOM 双向切换。
 * Implementation: 使用明确 DOM/存储/事件替身；不请求摄像头、GPS 或服务。
 * Related Modules: i18n.js, translations.js。
 * Declaration Index: testResolution; testCatalog; testDom; testDom.element;
 * testDom.element.matches; testDom.element.hasAttribute; testDom.element.getAttribute;
 * testDom.element.setAttribute; testDom.element.addEventListener;
 * testDom.Observer; testDom.Observer.constructor; testDom.Observer.observe。
 * Variable Index: None；测试局部变量保存受控 DOM、存储和窗口事件。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveLanguage, translate, localizePage, STORAGE_KEY } from '../i18n.js';
import { translations } from '../translations.js';

/** Functionality: 验证系统首选语言及显式覆盖。Inputs: 固定语言枚举/列表。
 * Outputs: 解析断言。Logic: 覆盖中文地区、英文、其他语言与空列表。
 * Constraints: 不声称已验证真实操作系统设置界面。 */
test('language resolution uses browser preference and honors explicit choices', function testResolution() {
  for (const locale of ['zh-CN', 'zh-TW', 'zh-HK', 'ZH']) assert.equal(resolveLanguage('system', [locale]), 'zh-CN');
  for (const languages of [[], ['en-US'], ['fr-FR', 'zh-CN']]) assert.equal(resolveLanguage('system', languages), 'en');
  assert.equal(resolveLanguage('en', ['zh-CN']), 'en');
  assert.equal(resolveLanguage('zh-CN', ['en-US']), 'zh-CN');
});

/** Functionality: 验证全部词典项和动态字段。Inputs: 实际词典与固定参数。
 * Outputs: 英文无遗漏、中文原文及标识保留断言。
 * Logic: 逐项替换模板字段，再执行实际翻译函数。
 * Constraints: 未知第三方文字保留原文，不代表翻译审校或外部服务验证。 */
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

/** Functionality: 验证保存、即时切换、动态状态及系统/跨标签页事件。
 * Inputs: 明确的最小 DOM、存储与 MutationObserver 替身。
 * Outputs: 文本、属性、lang 与存储断言。Logic: 直接派发实际注册的回调。
 * Constraints: 无真实浏览器布局/事件调度；替身不能替代真机验证。 */
test('DOM switching retains source strings and follows system and storage events', function testDom() {
  /** Functionality: 创建本测试使用的元素替身。Inputs: children 和 attrs。
   * Outputs: 可写元素。Logic: 实现翻译用到的 DOM 方法。
   * Constraints: 不解析 HTML，不模拟浏览器渲染。 */
  function element(children = [], attrs = {}) {
    return { nodeType: 1, childNodes: children, attrs, handlers: {},
      /** Functionality: 判断原文保护标记。Inputs: selector。Outputs: boolean。
       * Logic: 替身只使用保护标记。Constraints: 不实现完整 CSS。 */
      matches(selector) { return selector.includes('[data-language-original]') && Object.hasOwn(attrs, 'data-language-original'); },
      /** Functionality: 查询属性。Inputs: key。Outputs: boolean。Logic: 检查自有键。Constraints: 无继承属性。 */
      hasAttribute(key) { return Object.hasOwn(attrs, key); },
      /** Functionality: 读取属性。Inputs: key。Outputs: value。Logic: 直接读取。Constraints: 测试限定属性。 */
      getAttribute(key) { return attrs[key]; },
      /** Functionality: 写入属性。Inputs: key/value。Outputs: attrs 更新。Logic: 直接赋值。Constraints: 不自动派发观察事件。 */
      setAttribute(key, value) { attrs[key] = value; },
      /** Functionality: 保存事件。Inputs: name/handler。Outputs: handlers 更新。Logic: 单监听器。Constraints: 测试直接调用。 */
      addEventListener(name, handler) { this.handlers[name] = handler; },
    };
  }
  const text = { nodeType: 3, nodeValue: '尚未连接' };
  const original = { nodeType: 3, nodeValue: '左转' };
  const select = element();
  const root = element([text, select, element([original], {'data-language-original': ''})], {'aria-label': '摄像头回传工作台'});
  const events = {}, saved = new Map();
  let observe;
  /** Functionality: 捕获实际观察回调。Logic: 显式手动派发变更。
   * Constraints: 不模拟浏览器微任务队列。 */
  class Observer {
    /** Functionality: 保存回调。Inputs: callback。Outputs: observe 更新。Logic: 保存引用。Constraints: 不自动调用。 */
    constructor(callback) { observe = callback; }
    /** Functionality: 接受观察配置。Inputs: None 使用隐式注册。Outputs: 无。Logic: 不执行观察。Constraints: 测试手动派发。 */
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
