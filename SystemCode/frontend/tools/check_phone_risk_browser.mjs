/* Responsibilities: Diagnose remote detection-to-speech using an explicit public-image camera fixture.
 * Implementation: Real deployed camera, transport, model, scheduler and PCM output; only sensor pixels are synthetic.
 * Related Modules: camera.js, assist-mvp.js, assist-guidance.js, speech-output.js.
 * Declaration Index: None. Variable Index: report contains body-free frame age and alert/output evidence;
 * profile explicitly selects the deployed page or one comparison; cameraSourcePath optionally overrides only this tab.
 * PRS_PLAYWRIGHT_PATH selects an installed development runtime, not a production dependency.
 * Constraints: No local inference, changed TTL, fabricated model results, retries or physical audio claim. */
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
const [url,output,profile="deployed",cameraSourcePath]=process.argv.slice(2);
const address=new URL(url);
if(address.protocol!=="https:" || address.pathname!=="/mvp/maps/" || address.username || address.password || address.search || address.hash || !output)
 throw new Error("Use HTTPS_MAPS_URL NEW_REPORT [deployed|standard|low-latency-1] [CAMERA_SOURCE]");
if(!['deployed','standard','low-latency-1'].includes(profile))throw new Error('Choose an explicit profile');
const {chromium}=createRequire(import.meta.url)(process.env.PRS_PLAYWRIGHT_PATH);
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:390,height:844}});
const report={profile,requested_frames:8,source:'public detection-sample.jpg canvas camera; real remote GPU',physical_phone:false,errors:[]};
page.on('pageerror',e=>report.errors.push(e.message.slice(0,180)));
try {
 if(cameraSourcePath) {
  const cameraSource=await readFile(cameraSourcePath);
  report.source_override_sha256=createHash('sha256').update(cameraSource).digest('hex');
  await page.route('**/mvp/assets/camera.js',route=>route.fulfill({status:200,contentType:'application/javascript',body:cameraSource}));
 }
 const statusResponse=await page.request.get(new URL('../integration/status/',url).href,{timeout:15000});
 if(!statusResponse.ok() || !(await statusResponse.json()).accept_available)throw new Error('Remote inference is not available; no retry');
 await page.goto(url,{waitUntil:'domcontentloaded',timeout:45000});
 await page.locator('#product-camera-start').waitFor({state:'visible'});
 await page.waitForFunction(()=>!document.getElementById('product-camera-start').disabled,{},{timeout:20000});
 await page.evaluate(async()=>{const app=await import('/mvp/assets/integration/assist-mvp.js');window.__riskApp=app;});
 await page.locator('#assist-speech-start').click();
 await page.waitForFunction(()=>window.__riskApp.speechActivation.describe().ready,{},{timeout:80000});
 await page.waitForFunction(()=>window.__riskApp.speechOutput.phase==='completed',{},{timeout:20000});
 if(profile!=="deployed")await page.evaluate(profile=>{window.__riskApp.cameraApp.cameraProfile=profile;},profile);
 report.selected_profile=await page.evaluate(()=>window.__riskApp.cameraApp.cameraProfile);
 const sourceBytes=await readFile(new URL('./fixtures/detection-sample.jpg',import.meta.url));
 report.fixture_sha256=createHash('sha256').update(sourceBytes).digest('hex');
 const bytes=[...sourceBytes];
 await page.evaluate(async bytes=>{
  const {cameraApp,guidanceManager,speechOutput}=window.__riskApp;
  const image=new Image();const source=URL.createObjectURL(new Blob([new Uint8Array(bytes)],{type:'image/jpeg'}));
  image.src=source;await image.decode();URL.revokeObjectURL(source);
  const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
  // Functionality: Supply repeated public fixture pixels. Inputs: Fixed image. Outputs: Canvas stream.
  // Logic: Real camera code still encodes/sends every admitted frame. Constraints: No physical sensor or model stub.
  const draw=()=>canvas.getContext('2d').drawImage(image,0,0);draw();
  window.__drawTimer=setInterval(draw,33);
  Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async constraints=>{
   if(constraints.audio)throw new Error('Fixture supplies video only');return canvas.captureStream(30);
  }});
  const frames=[],events=[];window.__riskEvidence={frames,events};
  // Functionality: Observe validated results. Inputs: Real source events. Outputs: Scalar timing/counts.
  // Logic: Keep all completed frames and failures. Constraints: No image, object label or spoken text archive.
  cameraApp.subscribe(e=>{
   if(e.type==='frame') {const m=e.result.metadata;frames.push({index:m.index,capture_age_ms:performance.now()-e.capturedAt,
    server_ms:m.server_ms,inference_ms:m.inference_ms,count:m.count,
    transport_ms:e.timing.transportMs,capture_ms:e.timing.captureMs,decode_ms:e.timing.decodeMs,render_queue_ms:e.timing.renderQueueMs,
    alerts:m.boxes.filter(b=>b.alert_required===true&&b.alert_priority>0).length,
    grades:m.boxes.map(b=>b.risk_level),paused:guidanceManager.describe().paused.speech});}
   if(e.type==='error')events.push({phase:'camera_error',reason:e.reason,message:e.message});
  });
  guidanceManager.subscribe(e=>{if(e.channel==='speech')events.push(Object.fromEntries(
   ['phase','reason','kind','at','capture_to_start_ms','confirmed_capture_to_start_ms'].filter(k=>Object.hasOwn(e,k)).map(k=>[k,e[k]])));});
 },bytes);
 await page.locator('#product-camera-start').click();
 await page.waitForFunction(()=>window.__riskApp.cameraApp.pipeline || window.__riskEvidence.events.some(e=>e.phase==='camera_error'),{},{timeout:15000});
 await page.evaluate(()=>{if(window.__riskApp.cameraApp.pipeline)window.__riskApp.cameraApp.pipeline.frameLimit=8;});
 await page.waitForFunction(()=>window.__riskEvidence.frames.length>=8 || window.__riskEvidence.events.some(e=>e.phase==='camera_error'),{},{timeout:60000});
 report.evidence=await page.evaluate(()=>({...window.__riskEvidence,profile:window.__riskApp.cameraApp.sessionCaptureProfile,contract:window.__riskApp.cameraApp.pipeline?.client.contract,manager:window.__riskApp.guidanceManager.describe(),
  speech:window.__riskApp.speechOutput.describe(),status:document.getElementById('assist-result-status').textContent}));
 await page.locator('#assist-stop-all').click();
 report.complete=report.evidence.frames.length===8 && !report.evidence.events.some(e=>e.phase==='camera_error');if(!report.complete)process.exitCode=1;
} catch(e) {report.failure={name:e.name,message:e.message.slice(0,1200)};process.exitCode=1;
 report.evidence=await page.evaluate(()=>window.__riskEvidence??{}).catch(()=>({}));}
finally {await browser.close();await writeFile(output,JSON.stringify(report,null,2)+'\n',{flag:'wx'});}
console.log(JSON.stringify(report));
