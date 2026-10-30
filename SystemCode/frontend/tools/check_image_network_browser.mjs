// Measure real browser JPEG encoding and byte-verified public echo independently of inference.
// Fixed public fixture only; one in-flight image, bounded rounds, no retry or personal sensors.
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {readFile, writeFile} from 'node:fs/promises';

const [target, output, roundsText = '10'] = process.argv.slice(2);
const address = new URL(target), rounds = Number(roundsText);
if (address.protocol !== 'https:' || address.pathname !== '/mvp/maps/test' ||
    address.username || address.password || address.search || address.hash || !output ||
    !Number.isSafeInteger(rounds) || rounds < 3 || rounds > 20)
  throw new Error('Use HTTPS_MVP_MAPS_TEST_URL NEW_REPORT.json [3..20 rounds]');
const {chromium} = createRequire(import.meta.url)(process.env.PRS_PLAYWRIGHT_PATH);
const fixture = await readFile(new URL('./fixtures/detection-sample.jpg', import.meta.url));
const echoSource = await readFile(new URL('../echo-client.js', import.meta.url), 'utf8');
const report = {at_utc: new Date().toISOString(), target: address.origin, physical_phone: false,
  client: 'Desktop Chrome, direct public route without proxy', rounds,
  fixture_sha256: createHash('sha256').update(fixture).digest('hex'),
  echo_client_sha256: createHash('sha256').update(echoSource).digest('hex'),
  measurement: 'JPEG upload and identical JPEG return; excludes model inference; not one-way latency',
  order: 'Rotate profile order each round; retain first rounds and all failures', errors: []};
const browser = await chromium.launch({channel: 'chrome', headless: true});
const page = await browser.newPage();
page.on('pageerror', error => report.errors.push(error.message.slice(0, 180)));
try {
  const response = await page.request.get(target, {timeout: 15000});
  if (!response.ok()) throw new Error(`Public entry status ${response.status()}`);
  // Isolate transfer measurements from map tiles, voice resources and other page work.
  // Only this test tab receives the shell; the server and other tabs are unchanged.
  await page.route(target, route => route.fulfill({contentType: 'text/html',
    body: '<!doctype html><title>Image network measurement</title><p>Public fixture test</p>'}));
  await page.route('**/mvp/assets/echo-client.js', route => route.fulfill({
    contentType: 'text/javascript', body: echoSource}));
  await page.goto(target, {waitUntil: 'domcontentloaded', timeout: 15000});
  report.result = await page.evaluate(async ({bytes, rounds}) => {
    const {EchoClient} = await import('/mvp/assets/echo-client.js');
    const image = new Image(), source = URL.createObjectURL(new Blob([new Uint8Array(bytes)], {type: 'image/jpeg'}));
    try { image.src = source; await image.decode(); } finally { URL.revokeObjectURL(source); }
    const profiles = [320, 480, 640].flatMap(edge => [0.6, 0.8, 0.9].map(quality => ({edge, quality})));
    const canvas = document.createElement('canvas'), context = canvas.getContext('2d');
    const result = {source_size: {width: image.naturalWidth, height: image.naturalHeight},
      samples: [], pings: [], failures: [], profiles};
    const client = new EchoClient(`wss://${location.host}/mvp/ws/echo/`, () => {});
    const began = performance.now();
    try {
      await client.connect(); result.connection_ms = performance.now() - began;
      result.websocket_extensions = client.socket.extensions;
      for (let round = 0; round < rounds; round++) {
        if (performance.now() - began > 180000) throw new Error('Three-minute total test budget exceeded');
        result.pings.push({round, ...(await client.ping())});
        for (let offset = 0; offset < profiles.length; offset++) {
          const profileIndex = (round + offset) % profiles.length, {edge, quality} = profiles[profileIndex];
          const scale = Math.min(1, edge / Math.max(image.naturalWidth, image.naturalHeight));
          canvas.width = Math.round(image.naturalWidth * scale); canvas.height = Math.round(image.naturalHeight * scale);
          const started = performance.now(); context.drawImage(image, 0, 0, canvas.width, canvas.height);
          const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', quality));
          if (!blob || blob.type !== 'image/jpeg') throw new Error('JPEG encoding unavailable');
          const encoded = await blob.arrayBuffer(), encodeMs = performance.now() - started;
          const echo = await client.sendFrame(encoded);
          const received = performance.now();
          const returned = URL.createObjectURL(new Blob([echo.bytes], {type: 'image/jpeg'}));
          const preview = new Image();
          try { preview.src = returned; await preview.decode(); } finally { URL.revokeObjectURL(returned); }
          result.samples.push({round, profile_index: profileIndex, edge, quality,
            width: canvas.width, height: canvas.height, bytes: encoded.byteLength, encode_ms: encodeMs,
            network_rtt_ms: echo.rttMs, decode_ms: performance.now() - received,
            encode_network_decode_ms: performance.now() - started, byte_exact: true});
        }
      }
    } catch (error) { result.failures.push({name: error.name, message: error.message.slice(0, 180)}); }
    finally { client.stop(); }
    result.elapsed_ms = performance.now() - began;
    return result;
  }, {bytes: [...fixture], rounds});
  const quantile = (values, p) => {
    const sorted = [...values].sort((a, b) => a - b), index = (sorted.length - 1) * p;
    return sorted[Math.floor(index)] + (index % 1) * (sorted[Math.ceil(index)] - sorted[Math.floor(index)]);
  };
  report.groups = report.result.profiles.map(({edge, quality}) => {
    const samples = report.result.samples.filter(sample => sample.edge === edge && sample.quality === quality);
    if (!samples.length) return {edge, quality, n: 0};
    const values = key => samples.map(sample => sample[key]);
    return {edge, quality, n: samples.length, bytes_median: quantile(values('bytes'), .5),
      encode_median_ms: quantile(values('encode_ms'), .5), rtt_median_ms: quantile(values('network_rtt_ms'), .5),
      rtt_p95_ms: quantile(values('network_rtt_ms'), .95), rtt_max_ms: Math.max(...values('network_rtt_ms')),
      total_median_ms: quantile(values('encode_network_decode_ms'), .5),
      total_p95_ms: quantile(values('encode_network_decode_ms'), .95)};
  });
  report.complete = report.result.samples.length === rounds * 9 && !report.result.failures.length && !report.errors.length;
} catch (error) { report.failure = {name: error.name, message: error.message.slice(0, 500)}; report.complete = false; }
finally { await browser.close(); await writeFile(output, JSON.stringify(report, null, 2) + '\n', {flag: 'wx'}); }
if (!report.complete) process.exitCode = 1;
console.log(JSON.stringify({complete: report.complete, groups: report.groups, failure: report.failure, errors: report.errors}));
