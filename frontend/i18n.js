/** Responsibilities: 两个诊断页面共享语言选择、系统语言解析及安全 DOM 翻译。
 * Implementation: 词典匹配原始文本及参数；WeakMap 保留原文以支持即时双向切换；
 * MutationObserver 处理现有控制器生成的状态/Leaflet 标签，不修改业务契约。
 * Related Modules: translations.js, index.html, maps.html, core.camera.ASSETS。
 * Declaration Index: resolveLanguage; translate; localizePage; localizePage.render;
 * localizePage.visit; localizePage.refresh; localizePage.choose; localizePage.systemChanged;
 * localizePage.storageChanged; localizePage.changed。
 * Variable Index: STORAGE_KEY — 同源语言偏好键；patterns — 固定词典和参数匹配器。
 */
import { translations } from './translations.js';
export const STORAGE_KEY = 'prs.language';
const patterns = Object.entries(translations).map(([source, target]) => ({
  source, target,
  regex: new RegExp('^' + source.split(/\{\d+\}/).map(part =>
    part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('(.+?)') + '$'),
}));

/** Functionality: 解析语言偏好。Inputs: preference 与浏览器 languages 数组。
 * Outputs: zh-CN 或 en。Logic: 显式选择优先；系统主语言为 zh 时用中文，否则英文。
 * Constraints: 未支持的系统语言使用英文；不改变处理或定位参数。 */
export function resolveLanguage(preference, languages = []) {
  if (preference === 'zh-CN' || preference === 'en') return preference;
  return /^zh(?:-|$)/i.test(languages[0] || '') ? 'zh-CN' : 'en';
}

/** Functionality: 翻译单段界面文字。Inputs: text 原文、language 目标语言。
 * Outputs: 翻译文字或原文。Logic: 保留首尾空白；只匹配词典项，参数递归翻译标签。
 * Constraints: 未知外部服务诊断和 ORS 原指令保留原文；从不插入 HTML。 */
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
  // API 错误附带代码与请求编号；仅翻译已知诊断前缀，完整保留标识。
  const suffix = normalized.indexOf('（');
  if (suffix > 0 && Object.hasOwn(translations, normalized.slice(0, suffix)))
    return translations[normalized.slice(0, suffix)] + normalized.slice(suffix);
  return text;
}

/** Functionality: 为当前页面安装语言选择器与翻译生命周期。
 * Inputs: doc/win 可注入 DOM 与窗口；Outputs: refresh 函数及监听器注册。
 * Logic: 读取同源偏好、监听系统和跨标签页变更，记录节点原文/已呈现值以避免观察器循环。
 * Constraints: 只保存语言枚举；存储受限时明确记录诊断并保持当前页选择；无权限或网络请求。 */
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
  /** Functionality: 维护单节点或属性的原文。Inputs: node/key/value/set。
   * Outputs: 安全文本更新。Logic: 外部更新刷新原文，语言更新复用保存的原文。
   * Constraints: 不改写表单值，不持有已删除节点。 */
  function render(node, key, value, set) {
    const records = originals.get(node) || {};
    let record = records[key];
    if (!record || value !== record.rendered) record = { source: value };
    record.rendered = translate(record.source, language);
    records[key] = record;
    originals.set(node, records);
    if (value !== record.rendered) set(record.rendered);
  }
  /** Functionality: 遍历可翻译 DOM。Inputs: node。Outputs: 文本及 alt/aria-label/title 更新。
   * Logic: 跳过脚本、样式、用户输入及 ORS 原指令；保留元素和事件。
   * Constraints: 不使用 innerHTML；忽略注释和非元素/文本节点。 */
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
  /** Functionality: 即时刷新页面。Inputs: 当前 preference 和 navigator。
   * Outputs: 文档 lang、选择器、界面文字。Logic: 每次重新解析系统首选语言。
   * Constraints: 不重新初始化地图或摄像头，不重发请求。 */
  function refresh() {
    language = resolveLanguage(preference, win.navigator.languages || [win.navigator.language]);
    doc.documentElement.lang = language;
    select.value = preference;
    visit(doc.documentElement);
  }
  /** Functionality: 应用用户明确选择。Inputs: select.value。
   * Outputs: 偏好保存、界面刷新、诊断日志。Logic: 只接受三种枚举。
   * Constraints: 存储失败不会宣称已持久化；不记录用户数据。 */
  function choose() {
    if (!['en', 'zh-CN', 'system'].includes(select.value)) return;
    preference = select.value;
    try { win.localStorage.setItem(STORAGE_KEY, preference); }
    catch (error) { console.warn('[PRS language] Preference not saved:', error.name); }
    console.info('[PRS language] Preference changed:', preference);
    refresh();
  }
  /** Functionality: 响应系统语言更新。Inputs: languagechange。
   * Outputs: 必要时刷新。Logic: 仅 system 模式生效。Constraints: 保留显式选择。 */
  function systemChanged() { if (preference === 'system') refresh(); }
  /** Functionality: 同步同源标签页偏好。Inputs: StorageEvent。
   * Outputs: 有效枚举或删除后的系统模式。Logic: 只处理本设置键。
   * Constraints: 忽略非法值，不写回存储。 */
  function storageChanged(event) {
    if (event.key !== STORAGE_KEY && event.key !== null) return;
    if (event.newValue !== null && !['en', 'zh-CN', 'system'].includes(event.newValue)) return;
    preference = event.newValue || 'system';
    refresh();
  }
  /** Functionality: 翻译异步界面更新。Inputs: MutationRecord 数组。
   * Outputs: 新增或变化节点的文字。Logic: 复用原文记录避免自身更新循环。
   * Constraints: 不改变服务端响应、坐标值或处理状态。 */
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
