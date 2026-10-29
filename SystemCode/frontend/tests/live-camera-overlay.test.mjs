/* Responsibilities: Verify layered drawing, source identity and scalar accounting.
 * Implementation: Controlled canvas and clock, without camera or model substitutes.
 * Related Modules: integration/live-camera-overlay.js.
 * Declaration Index: fixture; detection; tracked.
 * Variable Index: per-test clock, strokes and metrics; no media or network.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {LiveCameraOverlay, overlayLifetime} from "../integration/live-camera-overlay.js";

test("default timer adapters keep the global receiver required by browsers", () => {
  const savedSet = globalThis.setTimeout, savedClear = globalThis.clearTimeout;
  const calls = [];
  globalThis.setTimeout = function(callback, delay) {
    assert.equal(this, globalThis); calls.push(delay); return 42;
  };
  globalThis.clearTimeout = function(timer) { assert.equal(this, globalThis); calls.push(timer); };
  try {
    const overlay = new LiveCameraOverlay({video: {}, canvas: {}, label: {}, maxAgeMs: 2000});
    const timer = overlay.setTimer(() => {}, 150); overlay.clearTimer(timer);
    assert.deepEqual(calls, [150, 42]);
  } finally { globalThis.setTimeout = savedSet; globalThis.clearTimeout = savedClear; }
});

test("brief boxes retain the original capture deadline", () => {
  assert.equal(overlayLifetime(0, 1500, 2000), 150);
  assert.equal(overlayLifetime(0, 1950, 2000), 50);
  assert.equal(overlayLifetime(0, 2000, 2000), 0);
  assert.equal(overlayLifetime(2001, 2000, 2000), 0);
  assert.equal(overlayLifetime(NaN, 2000, 2000), 0);
});

test("overlay clears on expiry and suppresses old or rotated results", () => {
  const rectangles = [], timers = [];
  const context = {clearRect() {}, setTransform() {}, setLineDash() {}, strokeRect(...args) { rectangles.push(args); },
    measureText() { return {width: 20}; }, fillRect() {}, fillText() {}};
  const canvas = {width: 0, height: 0, hidden: true, getContext: () => context};
  const video = {videoWidth: 640, videoHeight: 480, clientWidth: 320, clientHeight: 240};
  const label = {hidden: true, textContent: ""};
  let clock = 1500;
  const overlay = new LiveCameraOverlay({video, canvas, label, maxAgeMs: 2000, now: () => clock,
    setTimer(fn, delay) { timers.push({fn, delay}); return timers.length; }, clearTimer() {}});
  const event = {capturedAt: 0, result: {metadata: {width: 640, height: 480,
    boxes: [{xyxy: [40, 40, 240, 240], label: "person", distance_m: 2}]}}};
  assert.equal(overlay.show(event), true);
  assert.deepEqual(rectangles, [[20, 20, 100, 100]]);
  assert.match(label.textContent, /1.5 秒前/);
  assert.equal(timers[0].delay, 150);
  const motionEvent={...event,localTracking:true,result:{metadata:{...event.result.metadata,
    boxes:event.result.metadata.boxes.map((box,index)=>({...box,source_box_index:index}))}}};
  assert.equal(overlay.show(motionEvent), true);
  assert.match(label.textContent, /本地跟踪（估计位置）/);
  assert.match(label.textContent, /1.5 秒前/);
  clock=1650;timers.at(-1).fn(); assert.equal(canvas.hidden, true);
  clock = 2000; assert.equal(overlay.show(event), false);
  clock = 1500; video.videoWidth = 480; video.videoHeight = 640;
  assert.equal(overlay.show(event), false);
});

/** Functionality: Bind a deterministic canvas. Inputs: None. Outputs: overlay and clock control.
 * Logic: observe real drawing calls and deadlines. Constraints: not physical screen paint. */
function fixture() {
  const state={now:500,strokes:[],metrics:[],timers:[],dash:[]};
  const context={setTransform(){},clearRect(){state.strokes=[];},setLineDash(value){state.dash=value;},
    strokeRect(...coordinates){state.strokes.push({coordinates,dashed:state.dash.length>0});},
    measureText(){return {width:20};},fillRect(){},fillText(){}};
  const canvas={width:0,height:0,hidden:true,getContext:()=>context};
  const video={videoWidth:640,videoHeight:480,clientWidth:320,clientHeight:240};
  const overlay=new LiveCameraOverlay({video,canvas,label:{},maxAgeMs:2000,now:()=>state.now,
    onMetrics:value=>state.metrics.push(value),setTimer(fn,delay){state.timers.push({fn,delay});return state.timers.length;},clearTimer(){}});
  return {state,overlay,video,canvas};
}
/** Functionality: Build distinct source boxes. Inputs: count/index/epoch. Outputs: camera event.
 * Logic: valid independent geometry. Constraints: no inference claim. */
function detection(count=10,index=1,epoch=0){return {capturedAt:0,result:{metadata:{index,tracking_epoch:epoch,width:640,height:480,
  boxes:Array.from({length:count},(_,i)=>({xyxy:[10+i*20,20,25+i*20,80],label:'fixture'}))}}};}
/** Functionality: Attach moved estimates to original indices. Inputs: event/count. Outputs: motion event.
 * Logic: retain source identity/capture time. Constraints: only visual geometry changes. */
function tracked(event,count=6){return {...event,localTracking:true,result:{metadata:{...event.result.metadata,
  boxes:event.result.metadata.boxes.slice(0,count).map((box,i)=>({...box,source_box_index:i,xyxy:box.xyxy.map(v=>v+4)}))}}};}

test('dense scenes draw all source boxes, then six tracks plus remaining hints without duplicates',()=>{
  const {state,overlay,canvas}=fixture(),source=detection();
  overlay.show(source);assert.equal(state.strokes.length,10);assert.ok(state.strokes.every(s=>s.dashed));
  state.now=530;overlay.show(tracked(source));
  assert.equal(state.strokes.length,10);assert.equal(state.strokes.filter(s=>!s.dashed).length,6);
  assert.equal(state.metrics.at(-1).hint_count,4);assert.equal(state.metrics.at(-1).never_displayed_count,0);
  assert.equal(state.timers.at(-1).delay,120);
  state.now=650;state.timers.at(-1).fn();
  assert.equal(canvas.hidden,false);assert.equal(state.strokes.length,6);
  assert.deepEqual(state.metrics.at(-1).hidden_reasons,{hint_expired:4});
  assert.equal(state.metrics.at(-1).displayed_unique_count,10);
  state.now=660;overlay.show(tracked(source));assert.equal(state.strokes.length,6);
  state.now=810;state.timers.at(-1).fn();assert.equal(canvas.hidden,true);
});
test('loss of tracking does not clear or renew other source hints',()=>{
  const {state,overlay}=fixture(),source=detection();overlay.show(source);
  state.now=520;overlay.show(tracked(source));
  state.now=540;overlay.clearTracking();assert.equal(state.strokes.length,10);
  assert.equal(state.timers.at(-1).delay,110);
  state.now=650;state.timers.at(-1).fn();assert.equal(state.strokes.length,0);
  assert.equal(state.metrics.at(-1).never_displayed_count,0);
});
test('late worker results from previous frame or epoch cannot draw against a new source',()=>{
  const {state,overlay}=fixture(),old=detection(),current=detection(8,2,1);
  overlay.show(old);state.now=530;overlay.show(current);
  assert.equal(overlay.show(tracked(old)),false);assert.equal(state.strokes.length,8);
  assert.equal(overlay.show(tracked(detection(8,2,0))),false);
  overlay.show(tracked(current));assert.equal(state.strokes.length,8);
  assert.equal(state.metrics.at(-1).detected_count,8);assert.equal(state.metrics.at(-1).displayed_unique_count,8);
});
test('expired and rotated sources report explicit non-display reasons',()=>{
  const {state,overlay,video}=fixture();state.now=2100;overlay.show(detection(8));
  assert.equal(state.metrics.at(-1).never_displayed_count,8);
  assert.deepEqual(state.metrics.at(-1).hidden_reasons,{detection_expired:8});
  state.now=500;video.videoWidth=480;video.videoHeight=640;overlay.show(detection(8,2));
  assert.deepEqual(state.metrics.at(-1).hidden_reasons,{aspect_mismatch:8});
  assert.equal(state.metrics.at(-1).displayed_unique_count,0);
});
