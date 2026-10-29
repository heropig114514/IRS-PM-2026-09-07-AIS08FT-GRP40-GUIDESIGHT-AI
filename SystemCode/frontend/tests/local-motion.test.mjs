/** Responsibilities: Verify visual tracking against known local motion and invalidation cases.
 * Implementation: The pinned real JSFeat implementation processes deterministic translated pixels.
 * Related Modules: integration/motion-core.js, integration/jsfeat.min.js.
 * Declaration Index: image; makeTracker; seed; closeBox.
 * Variable Index: cv is the real library in an isolated context; metadata is never mutated.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import '../integration/motion-core.js';
const context = {module:{exports:{}}};
vm.runInNewContext(readFileSync(new URL('../integration/jsfeat.min.js',import.meta.url),'utf8'),context);
const cv=context.module.exports;

/** Functionality: Create independently moving textured foreground. Inputs: displacement/occlusion.
 * Outputs: RGBA. Logic: identical object coordinates produce identical texture.
 * Constraints: synthetic visual geometry, not a model or physical phone benchmark. */
export function image(dx=0,dy=0,occluded=false) {
  const rgba=new Uint8ClampedArray(320*240*4);
  for(let y=0;y<240;y++)for(let x=0;x<320;x++) {
    const px=x-dx,py=y-dy;
    const inside=!occluded&&px>=40&&px<120&&py>=50&&py<145;
    const hash=Math.imul(px,374761393)^Math.imul(py,668265263);
    const value=inside?40+((Math.imul(hash^(hash>>>13),1274126177)>>>16)%190):25;
    const i=(y*320+x)*4;rgba[i]=rgba[i+1]=rgba[i+2]=value;rgba[i+3]=255;
  }
  return rgba;
}
/** Functionality: Start a real tracker. Inputs: None. Outputs: initialized tracker.
 * Logic: initial local frame sets dimensions. Constraints: monotonic fixture clock. */
function makeTracker(){const tracker=new globalThis.PrsMotionTracker(cv);tracker.frame({rgba:image(),width:320,height:240,at:100});return tracker;}
/** Functionality: Seed exact original geometry. Inputs: tracker and optional boxes.
 * Outputs: tracked result. Logic: original timestamp remains 100. Constraints: no warning updates. */
function seed(tracker,boxes=[{xyxy:[40,50,120,145],label:'person',distance_m:1.7,alert_required:true}]) {
  return tracker.seed({rgba:image(),width:320,height:240,at:100,index:1,boxes,sourceWidth:320,sourceHeight:240});
}
/** Functionality: Compare tracked coordinates with known truth. Inputs: result and displacement.
 * Outputs: assertions. Logic: every edge must match within two tracking pixels.
 * Constraints: translation only; not detector accuracy. */
function closeBox(result,dx,dy){assert.equal(result.boxes.length,1);[40+dx,50+dy,120+dx,145+dy].forEach((v,i)=>assert.ok(Math.abs(result.boxes[0].xyxy[i]-v)<2,`${result.boxes[0].xyxy} vs ${v}`));}

test('delayed detection catches up through image history, without altering risk metadata',()=>{
  const tracker=makeTracker();
  for(let i=1;i<=8;i++) tracker.frame({rgba:image(i*3,i*2),width:320,height:240,at:100+i*83});
  const boxes=[{xyxy:[40,50,120,145],label:'person',distance_m:1.7,alert_required:true}];
  const original=JSON.stringify(boxes),result=seed(tracker,boxes);
  closeBox(result,24,16);assert.equal(result.capturedAt,100);assert.equal(result.displayAt,764);
  assert.equal(result.boxes[0].distance_m,1.7);assert.equal(JSON.stringify(boxes),original);
  const next=tracker.frame({rgba:image(27,18),width:320,height:240,at:847});closeBox(next,27,18);
});
test('occlusion, sampling gaps and textureless seeds hide uncertain boxes',()=>{
  const tracker=makeTracker();seed(tracker);
  assert.equal(tracker.frame({rgba:image(0,0,true),width:320,height:240,at:183}).boxes.length,0);
  const gap=makeTracker();seed(gap);
  assert.equal(gap.frame({rgba:image(20),width:320,height:240,at:400}).boxes.length,0);
  const blank=makeTracker();
  assert.equal(blank.seed({rgba:image(0,0,true),width:320,height:240,at:100,index:1,boxes:[{xyxy:[40,50,120,145]}],sourceWidth:320,sourceHeight:240}).boxes.length,0);
});
test('age, orientation, older results and empty detections cannot resurrect old boxes',()=>{
  const tracker=makeTracker();seed(tracker);
  for(let i=1;i<=25;i++)tracker.frame({rgba:image(),width:320,height:240,at:100+i*83});
  assert.equal(tracker.result().boxes.length,0);assert.equal(seed(tracker).boxes.length,0);
  const clear=makeTracker();seed(clear);
  const empty={rgba:image(),width:320,height:240,at:100,index:2,boxes:[],sourceWidth:320,sourceHeight:240};
  assert.equal(clear.seed(empty).boxes.length,0);assert.equal(seed(clear).boxes.length,0);
  assert.equal(clear.frame({rgba:new Uint8ClampedArray(240*320*4),width:240,height:320,at:183}).boxes.length,0);
});
test('history and simultaneous tracks are bounded',()=>{
  const tracker=makeTracker();
  for(let i=1;i<150;i++)tracker.frame({rgba:image(),width:320,height:240,at:100+i*20});
  assert.ok(tracker.history.length<=28);
  const result=tracker.seed({rgba:image(),width:320,height:240,at:3080,index:1,boxes:Array.from({length:20},()=>({xyxy:[40,50,120,145]})),sourceWidth:320,sourceHeight:240});
  assert.ok(result.boxes.length<=6);assert.ok(tracker.tracks.every(t=>t.points.length<=20));
  assert.equal(result.diagnostics.excluded.tracking_budget,14);
});
test('priority selection retains original source indices and reports excluded targets',()=>{
  const tracker=makeTracker();
  const boxes=Array.from({length:8},(_,i)=>({xyxy:[40,50,120,145],alert_priority:i}));
  const before=JSON.stringify(boxes),result=seed(tracker,boxes);
  assert.deepEqual(result.boxes.map(box=>box.source_box_index),[7,6,5,4,3,2]);
  assert.equal(result.diagnostics.detected_count,8);assert.equal(result.diagnostics.seeded_count,6);
  assert.equal(result.diagnostics.excluded.tracking_budget,2);assert.equal(JSON.stringify(boxes),before);
});
test('small and textureless targets have distinct exclusion reasons; lost tracks count once',()=>{
  const tracker=makeTracker();const result=seed(tracker,[{xyxy:[40,50,45,55]},{xyxy:[180,50,250,150]}]);
  assert.deepEqual(result.diagnostics.excluded,{small_target:1,insufficient_features:1});
  const moving=makeTracker();seed(moving);
  const lost=moving.frame({rgba:image(0,0,true),width:320,height:240,at:183});
  assert.equal(lost.diagnostics.lost.flow_unreliable,1);
  const next=moving.frame({rgba:image(0,0,true),width:320,height:240,at:266});
  assert.equal(next.diagnostics.lost.flow_unreliable,1);
});
