/* Responsibilities: Verify asynchronous tracking ownership and diagnostic attribution.
 * Implementation: A message-controlled worker and local pixels stub; algorithm tests use real CV.
 * Related Modules: integration/local-motion.js, local-motion.test.mjs.
 * Declaration Index: setup; event; reply.
 * Variable Index: isolated document stub; records contain scalar diagnostics only.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {LocalMotion} from '../integration/local-motion.js';

/** Functionality: Bind a controlled worker. Inputs: None. Outputs: controller and observations.
 * Logic: avoid browser globals beyond canvas creation. Constraints: not a worker performance test. */
function setup() {
  const previous=globalThis.document;
  globalThis.document={createElement:()=>({width:320,height:240})};
  try {
    const diagnostics=[],results=[],messages=[];
    const motion=new LocalMotion({video:{},sourceImage:{naturalWidth:640,naturalHeight:480},
      onDiagnostic:value=>diagnostics.push(value),onResult:value=>results.push(value)});
    motion.worker={postMessage:value=>messages.push(value),terminate(){}};
    motion.pixels=()=>new Uint8ClampedArray(320*240*4);
    return {motion,diagnostics,results,messages};
  } finally {globalThis.document=previous;}
}
/** Functionality: Create a source event. Inputs: index/epoch. Outputs: metadata and timestamp.
 * Logic: fresh local time. Constraints: no real model prediction. */
function event(index,epoch=1){return {capturedAt:performance.now()-100,result:{metadata:{index,tracking_epoch:epoch,
  width:640,height:480,boxes:[{xyxy:[10,10,100,100]}]}}};}
/** Functionality: Create a source-bound worker reply. Inputs: source event. Outputs: message.
 * Logic: copied source association. Constraints: optical flow itself is separately verified. */
function reply(source){return {generation:0,epoch:source.result.metadata.tracking_epoch,ack:'seed',type:'motion',workMs:1,
  result:{index:source.result.metadata.index,capturedAt:source.capturedAt,displayAt:performance.now(),width:320,height:240,
    boxes:[{xyxy:[5,5,50,50],source_box_index:0}],diagnostics:{detected_count:1,seeded_count:1,active_count:1,excluded:{},lost:{}}}};}

test('a superseded seed cannot repaint or change diagnostics for the new detection',()=>{
  const f=setup(),one=event(1),two=event(2);f.motion.ingest(one);f.motion.ingest(two);
  assert.deepEqual(f.diagnostics[0].excluded,{superseded_detection:1});
  assert.equal(f.diagnostics[0].index,1);
  f.motion.receive(reply(one));assert.equal(f.results.length,0);assert.equal(f.diagnostics.length,1);
  f.motion.receive(reply(two));assert.equal(f.results.length,1);assert.equal(f.results[0].index,2);
  assert.equal(f.diagnostics.at(-1).index,2);assert.equal(f.motion.pendingSeed,false);
  f.motion.stop();
});
test('stopping records pending work and rejects a late callback from its old generation',()=>{
  const f=setup(),source=event(1);f.motion.ingest(source);f.motion.stop();
  assert.deepEqual(f.diagnostics.at(-1).excluded,{tracking_stopped:1});
  const count=f.results.length;f.motion.receive(reply(source));assert.equal(f.results.length,count);
});
