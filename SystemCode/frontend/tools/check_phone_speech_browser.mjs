/* Responsibilities: Validate phone speech recovery against actual public TTS.
 * Implementation: Use an isolated Chromium context and trusted calibration clicks on an already deployed page.
 * Related Modules: speech-output, speech-activation, assist-mvp and public same-origin TTS.
 * Declaration Index: snapshot; play; playRiskFixture; checkRiskEligibility. Variable Index: target/output/browser/page/report own this bounded check;
 * PRS_PLAYWRIGHT_PATH selects the browser runtime; optional PRS_BROWSER_PROXY is explicit transport.
 * Constraints: No local server, GPU inference, permissions, TLS bypass or physical-audibility claim. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
const [target,output]=process.argv.slice(2);
if(!target || !output || !process.env.PRS_PLAYWRIGHT_PATH) throw new Error("Use HTTPS_MAPS_URL NEW_REPORT; set PRS_PLAYWRIGHT_PATH");
const address=new URL(target);
if(address.protocol!=="https:" || !["/mvp/maps/","/mvp/maps/test"].includes(address.pathname) || address.search || address.hash || address.username || address.password)
  throw new Error("Use the trusted public /mvp/maps/ entry without credentials or parameters");
const {chromium}=createRequire(import.meta.url)(process.env.PRS_PLAYWRIGHT_PATH);
const browser=await chromium.launch({channel:'chrome',headless:true,...(process.env.PRS_BROWSER_PROXY?{proxy:{server:process.env.PRS_BROWSER_PROXY}}:{})});
const context=await browser.newContext({viewport:{width:390,height:844}}), page=await context.newPage();
const report={physical_audio_verified:false,checks:[],errors:[]};
page.on('pageerror',error=>report.errors.push({name:error.name,message:error.message.slice(0,180)}));
/* Functionality: Read content-free state from the page's canonical instances.
 * Inputs: Live page. Outputs: Driver/gate and visible controls. Logic: Real imported instances.
 * Constraints: Does not trigger playback or change application state. */
async function snapshot() {
  return page.evaluate(async()=>{
    const {speechOutput}=await import('/mvp/assets/speech-output.js');
    const {speechActivation,guidanceManager}=await import('/mvp/assets/integration/assist-mvp.js');
    return {driver:speechOutput.describe(),activation:speechActivation.describe(),paused:guidanceManager.describe().paused,
      status:document.getElementById('assist-speech-status').textContent};
  });
}
/* Functionality: Exercise one explicit calibration and require actual PCM lifecycle completion.
 * Inputs: Test label. Outputs: Result with elapsed time. Logic: Wait for a newer request ID and completed state.
 * Constraints: No retry, GPU inference, automatic permission grants or physical sound verification. */
async function play(label) {
  const before=(await snapshot()).driver.last_request?.id??0, start=Date.now();
  await page.locator('#assist-speech-start').click();
  await page.waitForFunction(previous=>{
    const speechOutput=window.__speechUnderTest;
    return speechOutput.lastRequest?.id>previous && ['completed','error','stopped'].includes(speechOutput.phase);
  },before,{timeout:80000});
  const result=await snapshot();
  assert.equal(result.driver.state,'completed',JSON.stringify(result));
  assert.equal(result.activation.ready,true); assert.equal(result.paused.speech,false);
  report.checks.push({label,elapsed_ms:Date.now()-start,...result});
}
/* Functionality: Verify prepared risk audio through the live page's actual cache and PCM player.
 * Inputs: One language and public manifest composition. Outputs: Completed risk-cache playback with no TTS socket.
 * Logic: Submit a labeled synthetic risk phrase directly, without fabricating model or camera evidence.
 * Constraints: Tests transport/audio only; no inference, changed risk thresholds or actual hazard claim. */
async function playRiskFixture(language) {
  let ttsSockets=0;
  const observe=socket=>{if(new URL(socket.url()).pathname.endsWith("/ws/tts/"))ttsSockets++;};
  page.on("websocket",observe);
  const before=(await snapshot()).driver.last_request?.id??0;
  try {
    const accepted=await page.evaluate(async language=>{
      const response=await fetch("/mvp/assets/risk-audio/manifest.json");
      if(!response.ok)throw new Error("Risk manifest unavailable");
      const manifest=await response.json(), phrase=manifest.compositions.find(item=>item.language===language);
      return window.__speechUnderTest.speak(phrase.text,{language,voice_id:"Cherry",rate:2,volume:1,purpose:"risk"}).accepted;
    },language);
    assert.equal(accepted,true);
    await page.waitForFunction(previous=>window.__speechUnderTest.lastRequest?.id>previous &&
      ["completed","error","stopped"].includes(window.__speechUnderTest.phase),before,{timeout:45000});
    const result=await snapshot();
    assert.equal(result.driver.state,"completed",JSON.stringify(result));
    assert.equal(result.driver.last_request.audio_transport,"risk_cache"); assert.equal(ttsSockets,0);
    report.checks.push({label:"Prepared risk phrase "+language,synthetic_risk:true,tts_sockets:ttsSockets,...result});
  } finally {page.off("websocket",observe);}
}
/* Functionality: Exercise visible eligibility using labeled synthetic detection metadata.
 * Inputs: Fresh, expired and non-alert fixtures. Outputs: Visible reasons and actual cached PCM completion.
 * Logic: Use the deployed manager rather than direct speech submission; retain original capture deadlines.
 * Constraints: No GPU, physical camera, threshold changes or claim that fixtures are model results. */
async function checkRiskEligibility() {
  const prior=(await snapshot()).driver.last_request.id;
  await page.evaluate(async()=>{
    const {guidanceManager}=await import('/mvp/assets/integration/assist-mvp.js');
    const box={track_id:17,label:'person',direction:'front',risk_level:'high',alert_priority:3,alert_required:true,
      ttc_sec:null,distance_m:2,motion_state:'stationary',relative_speed_mps:0,risk_reasons:[]};
    window.__riskFixture={box,guidanceManager};guidanceManager.beginRisk('synthetic-browser-risk');
    guidanceManager.ingestRisk({sessionId:'synthetic-browser-risk',capturedAt:performance.now()-1500,
      metadata:{index:0,tracking_epoch:1,boxes:[box]}});
  });
  assert.match(await page.locator('#assist-risk-status').textContent(),/one-second/);
  assert.equal((await snapshot()).driver.last_request.id,prior);
  await page.evaluate(()=>{
    const {box,guidanceManager}=window.__riskFixture;
    guidanceManager.ingestRisk({sessionId:'synthetic-browser-risk',capturedAt:performance.now(),
      metadata:{index:1,tracking_epoch:1,boxes:[{...box,risk_level:'medium',alert_required:false}]}});
  });
  assert.match(await page.locator('#assist-risk-status').textContent(),/does not meet/);
  assert.equal((await snapshot()).driver.last_request.id,prior);
  await page.evaluate(()=>{
    const {box,guidanceManager}=window.__riskFixture;
    let index=2;
    guidanceManager.ingestRisk({sessionId:'synthetic-browser-risk',capturedAt:performance.now(),
      metadata:{index:index++,tracking_epoch:1,boxes:[box]}});
    // An uncached phrase may take longer than one frame's lifetime to download. Feed
    // explicitly synthetic fresh confirmations while it prepares, like an ongoing source.
    window.__riskConfirmTimer=setInterval(()=>guidanceManager.ingestRisk({sessionId:'synthetic-browser-risk',
      capturedAt:performance.now(),metadata:{index:index++,tracking_epoch:1,boxes:[box]}}),250);
  });
  try {
    await page.waitForFunction(previous=>window.__speechUnderTest.lastRequest?.id>previous &&
      ['completed','error','stopped'].includes(window.__speechUnderTest.phase),prior,{timeout:20000});
  } finally { await page.evaluate(()=>clearInterval(window.__riskConfirmTimer)); }
  const result=await snapshot();assert.equal(result.driver.state,'completed',JSON.stringify(result));
  assert.equal(result.driver.last_request.audio_transport,'risk_cache');
  report.checks.push({label:'Manager rejects expired/non-alert input; continuously confirmed required risk completes',
    synthetic_risk:true,synthetic_confirmation_interval_ms:250,...result});
  await page.evaluate(()=>window.__riskFixture.guidanceManager.stopRisk('synthetic_check_completed'));
}

try {
  await page.goto(target,{waitUntil:'domcontentloaded',timeout:45000});
  await page.locator('#assist-speech-start').waitFor({state:'visible',timeout:20000});
  await page.evaluate(async()=>{window.__speechUnderTest=(await import('/mvp/assets/speech-output.js')).speechOutput;
    window.__activationUnderTest=(await import('/mvp/assets/integration/assist-mvp.js')).speechActivation;});
  assert.equal(await page.locator('#assist-speech-status').isVisible(),true);
  await page.locator('#language').selectOption('zh-CN');
  await play('Chinese visible control');
  await page.evaluate(async()=>{const {speechOutput}=await import('/mvp/assets/speech-output.js'); await speechOutput.audioContext.suspend();});
  await page.waitForFunction(()=>!window.__activationUnderTest.describe().ready);
  report.interrupted=await snapshot(); assert.equal(report.interrupted.paused.speech,true);
  await play('Explicit recovery after AudioContext suspend');
  await page.evaluate(async()=>{const {speechOutput}=await import('/mvp/assets/speech-output.js'); await speechOutput.audioContext.close();});
  await page.locator('#language').selectOption('en');
  await play('English recovery after AudioContext close');
  await playRiskFixture('zh-CN');
  await playRiskFixture('en-US');
  await checkRiskEligibility();
  report.audio_resources=await page.evaluate(()=>performance.getEntriesByType('resource')
    .filter(e=>e.name.includes('/assets/risk-audio/'))
    .map(e=>({manifest:e.name.endsWith('/manifest.json'),bytes:e.decodedBodySize,transfer_bytes:e.transferSize,duration_ms:e.duration})));
  await page.screenshot({path:output+'.png',fullPage:false});
} catch(error) {report.failure={name:error.name,message:error.message.slice(0,1600)};process.exitCode=1;}
finally {await browser.close();await writeFile(output,JSON.stringify(report,null,2)+'\n',{flag:'wx'});}
console.log(JSON.stringify(report,null,2));
