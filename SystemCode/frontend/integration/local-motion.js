/* Responsibilities: Sample a small local preview for visual box tracking, independently of inference.
 * Implementation: At most 12 local frames/s and one outstanding sample; worker owns a two-second history.
 * Related Modules: motion-worker.js, live-latency.js, live-camera-overlay.js.
 * Declaration Index: LocalMotion; LocalMotion.constructor; LocalMotion.start; LocalMotion.pixels;
 * LocalMotion.sample; LocalMotion.ingest; LocalMotion.reportSkipped; LocalMotion.receive; LocalMotion.failed;
 * LocalMotion.stop; LocalMotion.describe.
 * Variable Index: generation rejects old workers; counters contain timings only. No frames leave the phone.
 */
/** Functionality: Maintain a visual-only worker alongside original inference.
 * Logic: bounded sampling and generation/epoch guards. Constraints: no risk or distance updates. */
export class LocalMotion {
  /** Functionality: Bind media and callbacks. Inputs: video, sourceImage, callbacks. Outputs: idle instance.
   * Logic: two reusable small canvases. Constraints: no media permissions or network here. */
  constructor({video,sourceImage,onResult,onState=()=>{},onDiagnostic=()=>{}}) {
    Object.assign(this,{video,sourceImage,onResult,onState,onDiagnostic});this.generation=0;this.worker=null;
    this.canvas=document.createElement('canvas');this.seedCanvas=document.createElement('canvas');this.raf=null;
    this.stats={state:'idle',local_frames:0,tracked_updates:0,work_max_ms:0};
  }
  /** Functionality: Start a fresh worker. Inputs: callbacks. Outputs: local samples.
   * Logic: invalidate old generation. Constraints: failure is visible, no provider fallback. */
  start() {
    this.stop();const generation=++this.generation;this.stats={state:'running',local_frames:0,tracked_updates:0,work_max_ms:0};
    this.busy=false;this.lastSample=-Infinity;this.epoch=null;this.latestInput=null;this.pendingSeed=false;
    try {
      this.worker=new Worker(new URL('./motion-worker.js',import.meta.url));
      this.worker.onmessage=event=>this.receive(event.data);
      this.worker.onerror=()=>{if(generation===this.generation)this.failed();};
      this.worker.postMessage({type:'reset',generation});
      this.raf=requestAnimationFrame(at=>this.sample(at));this.onState(this.describe());
    } catch {this.failed();}
  }
  /** Functionality: Downsample pixels. Inputs: media/canvas/dimensions. Outputs: RGBA.
   * Logic: reuse context. Constraints: maximum edge 320; no upload or persistence. */
  pixels(source,canvas,width,height) {
    if(canvas.width!==width)canvas.width=width;if(canvas.height!==height)canvas.height=height;
    const context=canvas.getContext('2d',{willReadFrequently:true});context.drawImage(source,0,0,width,height);
    return context.getImageData(0,0,width,height).data;
  }
  /** Functionality: Admit local frames. Inputs: animation timestamp. Outputs: worker message.
   * Logic: 12 Hz ceiling and one outstanding frame. Constraints: no queued captures. */
  sample(at) {
    if(!this.worker)return;
    this.raf=requestAnimationFrame(now=>this.sample(now));
    if(this.busy||at-this.lastSample<1000/12||!this.video.videoWidth||this.video.paused) return;
    const scale=Math.min(1,320/Math.max(this.video.videoWidth,this.video.videoHeight));
    const width=Math.round(this.video.videoWidth*scale),height=Math.round(this.video.videoHeight*scale);
    try {
      const began=performance.now();
      const rgba=this.pixels(this.video,this.canvas,width,height);
      this.stats.sample_max_ms=Math.max(this.stats.sample_max_ms||0,performance.now()-began);
      this.lastSample=at;this.busy=true;this.stats.local_frames++;
      this.worker.postMessage({type:'frame',generation:this.generation,width,height,at:performance.now(),rgba},[rgba.buffer]);
    } catch {this.failed();}
  }
  /** Functionality: Associate detection with its exact JPEG. Inputs: camera event. Outputs: seed.
   * Logic: dimensions/age/epoch checks. Constraints: original capture timestamp remains unchanged. */
  ingest(event) {
    const metadata=event.result?.metadata;
    if(!metadata)return;
    if(this.pendingSeed)this.reportSkipped('superseded_detection');
    this.latestInput={index:metadata.index,epoch:metadata.tracking_epoch,count:metadata.boxes.length};
    this.epoch=metadata.tracking_epoch;
    if(!this.worker){this.reportSkipped('tracking_unavailable');return;}
    if(performance.now()-event.capturedAt>=2000){this.reportSkipped('detection_expired');return;}
    if(!this.canvas.width){this.reportSkipped('preview_unavailable');return;}
    if(this.sourceImage.naturalWidth!==metadata.width||this.sourceImage.naturalHeight!==metadata.height){this.reportSkipped('source_mismatch');return;}
    const width=this.canvas.width,height=this.canvas.height;
    if(Math.abs(width/height/(metadata.width/metadata.height)-1)>.03){this.reportSkipped('aspect_mismatch');return;}
    try {
      const rgba=this.pixels(this.sourceImage,this.seedCanvas,width,height);
      this.pendingSeed=true;
      this.worker.postMessage({type:'seed',generation:this.generation,width,height,rgba,at:event.capturedAt,
        epoch:this.epoch,index:metadata.index,boxes:metadata.boxes,sourceWidth:metadata.width,sourceHeight:metadata.height},[rgba.buffer]);
    } catch {this.failed();}
  }
  /** Functionality: Explain a seed rejected before worker processing. Inputs: stable reason code.
   * Outputs: scalar diagnostic callback. Logic: attribute all source boxes to the rejection.
   * Constraints: these boxes may still have appeared as short historical hints. */
  reportSkipped(reason) {
    if(!this.latestInput)return;
    this.pendingSeed=false;
    const {index,epoch,count}=this.latestInput;
    this.onDiagnostic({index,epoch,status:'skipped',detected_count:count,seeded_count:0,active_count:0,
      excluded:count?{[reason]:count}:{},lost:{}});
  }
  /** Functionality: Publish recent visual estimates. Inputs: message. Outputs: overlay/timings.
   * Logic: generation/epoch and age guards. Constraints: never updates speech or distances. */
  receive(message) {
    if(message.generation!==this.generation||!this.worker)return;
    if(message.ack==='frame')this.busy=false;
    if(message.type==='error'){this.failed();return;}
    this.stats.work_max_ms=Math.max(this.stats.work_max_ms,message.workMs);
    this.stats.work_total_ms=(this.stats.work_total_ms||0)+message.workMs;
    this.stats.work_count=(this.stats.work_count||0)+1;
    const result=message.result;
    if(message.epoch!==this.epoch||result.index!==this.latestInput?.index)return;
    if(message.ack==='seed')this.pendingSeed=false;
    const now=performance.now();
    this.onDiagnostic({index:result.index,epoch:message.epoch,status:'updated',...result.diagnostics,
      visual_sample_stale:now-result.displayAt>=220});
    if(result.boxes.length&&now-result.capturedAt<2000&&now-result.displayAt<220){
      this.stats.tracked_updates++;this.stats.last_update_at_ms=now;
      this.stats.first_update_at_ms??=now;
      this.stats.local_frame_age_max_ms=Math.max(this.stats.local_frame_age_max_ms||0,now-result.displayAt);
      this.onResult({...result,epoch:message.epoch});
    }
    else this.onResult(null);
  }
  /** Functionality: Expose failure. Inputs: worker state. Outputs: unavailable state.
   * Logic: terminate and clear. Constraints: inference continues independently. */
  failed() {
    this.reportSkipped('worker_error');
    this.stop();this.stats.state='unavailable';this.stats.errors=(this.stats.errors||0)+1;this.onState(this.describe());
  }
  /** Functionality: Release history and callbacks. Inputs: worker/RAF. Outputs: idle state.
   * Logic: terminate worker. Constraints: preserves the camera owned by CameraApp. */
  stop() {
    if(this.pendingSeed)this.reportSkipped('tracking_stopped');
    this.generation++;if(this.raf!==null)cancelAnimationFrame(this.raf);this.raf=null;
    this.worker?.terminate();this.worker=null;this.stats.state='idle';this.onResult(null);this.onState(this.describe());
  }
  /** Functionality: Export scalar diagnostics. Inputs: counters. Outputs: statistics copy.
   * Logic: overlay refresh differs from inference FPS. Constraints: no images or boxes. */
  describe(){const elapsed=this.stats.last_update_at_ms-this.stats.first_update_at_ms;
    return {...this.stats,visual_updates_fps:elapsed>0?(this.stats.tracked_updates-1)*1000/elapsed:null};}
}
