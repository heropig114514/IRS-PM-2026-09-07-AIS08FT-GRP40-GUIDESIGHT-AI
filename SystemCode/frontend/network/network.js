import {EchoClient} from '../echo-client.js';

const $ = id => document.getElementById(id);
const profiles = [320, 480, 640].flatMap(edge => [0.6, 0.8, 0.9].map(quality => ({edge, quality})));
let source = null, sourceUrl = null, generation = 0, active = null, report = null, loading = false;
const median = values => percentile(values, .5);
function percentile(values, p) {
  const a = [...values].sort((x, y) => x - y), index = (a.length - 1) * p;
  return a[Math.floor(index)] + (index % 1) * (a[Math.ceil(index)] - a[Math.floor(index)]);
}
function summarize() {
  report.groups = profiles.map(profile => {
    const rows = report.samples.filter(row => row.edge === profile.edge && row.quality === profile.quality);
    const values = key => rows.map(row => row[key]);
    return rows.length ? {...profile, n: rows.length, bytes: median(values('bytes')), encode_ms: median(values('encode_ms')),
      rtt_ms: median(values('network_rtt_ms')), rtt_p95_ms: percentile(values('network_rtt_ms'), .95),
      total_ms: median(values('total_ms'))} : {...profile, n: 0};
  });
  $('results').replaceChildren(...report.groups.map(group => {
    const tr = document.createElement('tr');
    for (const value of [`${group.edge} / ${group.quality}`,
      ...['rtt_ms', 'rtt_p95_ms', 'bytes', 'encode_ms', 'total_ms'].map(key => group.n ?
        (group[key] / (key === 'bytes' ? 1000 : 1)).toFixed(1) : '—'), group.n]) {
      const td = document.createElement('td'); td.textContent = value; tr.append(td);
    }
    return tr;
  }));
  const baseline = report.groups.find(group => group.edge === 640 && group.quality === .8);
  $('baseline').textContent = baseline.n ? `参考配置 640 / 0.8：网络中位 ${baseline.rtt_ms.toFixed(1)} ms，` +
    `P95 ${baseline.rtt_p95_ms.toFixed(1)} ms，已测 ${baseline.n} 次。` : '参考配置：640 / 0.8，等待测试。';
}
async function choose(file) {
  if (!file || active || loading) return;
  if (file.size > 20 * 1024 * 1024) { $('status').textContent = '请选择小于 20 MB 的图片。'; return; }
  loading = true; $('start').disabled = true;
  const selected = new Image(), url = URL.createObjectURL(file);
  try {
    selected.src = url; await selected.decode();
    if (!selected.naturalWidth || selected.naturalWidth * selected.naturalHeight > 60000000) throw new Error('图片分辨率过大');
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    sourceUrl = url; source = selected; $('preview').src = url; $('preview').hidden = false;
    $('source').textContent = `${source.naturalWidth} × ${source.naturalHeight} · ${(file.size / 1000).toFixed(1)} KB`;
    $('status').textContent = '图片已就绪，可以开始测试。';
  } catch { URL.revokeObjectURL(url); $('status').textContent = '图片无法解码，请改用 JPEG 或 PNG 照片。'; }
  finally { loading = false; $('start').disabled = !source; }
}
async function run() {
  if (!source || active || loading) return;
  const mine = ++generation, input = source, client = new EchoClient(`ws://${location.host}/ws/echo/`, () => {});
  active = client;
  for (const id of ['start', 'file', 'capture', 'network']) $(id).disabled = true;
  $('stop').disabled = false; $('download').disabled = true; $('progress').value = 0;
  report = {schema_version: 1, at_utc: new Date().toISOString(), route: location.origin,
    network_label: $('network').value, source: {width: input.naturalWidth, height: input.naturalHeight},
    user_agent: navigator.userAgent, measurement: 'JPEG upload and identical JPEG echo; no inference; not one-way latency',
    rounds: 10, expected_samples: 90, samples: [], pings: [], failures: [], complete: false};
  const started = performance.now(), canvas = document.createElement('canvas'), context = canvas.getContext('2d');
  summarize();
  try {
    $('status').textContent = '正在连接这台电脑…'; await client.connect();
    report.connection_ms = performance.now() - started;
    report.websocket_extensions = client.socket.extensions;
    $('connection').textContent = `连接建立：${report.connection_ms.toFixed(1)} ms`;
    for (let round = 0; round < 10; round++) {
      report.pings.push({round, ...(await client.ping())});
      for (let offset = 0; offset < profiles.length; offset++) {
        if (mine !== generation) throw new Error('已停止');
        if (performance.now() - started > 180000) throw new Error('测试超过 3 分钟上限');
        const {edge, quality} = profiles[(round + offset) % profiles.length];
        const scale = Math.min(1, edge / Math.max(input.naturalWidth, input.naturalHeight));
        const width = Math.round(input.naturalWidth * scale), height = Math.round(input.naturalHeight * scale);
        if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
        const began = performance.now(); context.drawImage(input, 0, 0, width, height);
        const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', quality));
        if (!blob || blob.type !== 'image/jpeg') throw new Error('JPEG 编码失败');
        const bytes = await blob.arrayBuffer(), encodeMs = performance.now() - began;
        if (mine !== generation) throw new Error('已停止');
        const echo = await client.sendFrame(bytes), received = performance.now();
        const image = new Image(), url = URL.createObjectURL(new Blob([echo.bytes], {type: 'image/jpeg'}));
        try { image.src = url; await image.decode(); } finally { URL.revokeObjectURL(url); }
        if (mine !== generation) throw new Error('已停止');
        report.samples.push({round, edge, quality, width, height, bytes: bytes.byteLength, encode_ms: encodeMs,
          network_rtt_ms: echo.rttMs, decode_ms: performance.now() - received,
          total_ms: performance.now() - began, byte_exact: true});
        $('progress').value = report.samples.length; summarize();
        $('status').textContent = `已完成 ${report.samples.length} / 90 次，所有返回图片均通过逐字节校验。`;
      }
    }
    report.complete = true; $('status').textContent = '测试完成。可以导出报告，再换一张图片或一个网络重测。';
  } catch (error) {
    report.failures.push({reason: mine !== generation ? 'stopped' : error.message, completed_samples: report.samples.length});
    $('status').textContent = `测试已结束：${mine !== generation ? '手动停止或页面离开' : error.message}。已完成的记录可以导出。`;
  } finally {
    report.elapsed_ms = performance.now() - started; client.stop(); active = null;
    for (const id of ['start', 'file', 'capture', 'network', 'download']) $(id).disabled = false;
    $('stop').disabled = true;
  }
}
function stop() { if (active) { generation++; active.stop(); } }
$('file').addEventListener('change', event => choose(event.target.files[0]));
$('capture').addEventListener('change', event => choose(event.target.files[0]));
$('start').addEventListener('click', run); $('stop').addEventListener('click', stop);
$('download').addEventListener('click', () => {
  if (!report || active) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = `prs-image-network-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
});
document.addEventListener('visibilitychange', () => { if (document.visibilityState !== 'visible') stop(); });
window.addEventListener('pagehide', stop);
