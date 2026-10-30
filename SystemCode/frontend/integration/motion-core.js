/* Responsibilities: Reproject validated detection boxes through recent local image motion.
 * Implementation: Bounded grayscale pyramids, sparse Lucas-Kanade forward/backward checks,
 * robust feature displacement and fail-closed occlusion/gap handling. No risk or model updates.
 * Related Modules: motion-worker.js, jsfeat.min.js, tests/local-motion.test.mjs.
 * Declaration Index: median; PrsMotionTracker; PrsMotionTracker.constructor; PrsMotionTracker.reset;
 * PrsMotionTracker.pyramid; PrsMotionTracker.frame; PrsMotionTracker.seed;
 * PrsMotionTracker.advance; PrsMotionTracker.lose; PrsMotionTracker.drop; PrsMotionTracker.result.
 * Variable Index: cv is the pinned vision library; history owns at most 28 local frames;
 * tracks retain at most six boxes and twenty features per box, never uploaded or persisted.
 */
(() => {
  /** Functionality: Find middle displacement. Inputs: numbers. Outputs: median.
   * Logic: sort a copy. Constraints: nonempty input. */
  const median = values => {const sorted=[...values].sort((a,b)=>a-b);return sorted[Math.floor(sorted.length/2)];};
  /** Functionality: Estimate visual box translation. Logic: sparse optical flow.
   * Constraints: no classification; deformation, occlusion or weak texture can invalidate tracks. */
  class PrsMotionTracker {
    /** Functionality: Bind CV library. Inputs: cv. Outputs: tracker.
     * Logic: reset. Constraints: worker owns lifecycle. */
    constructor(cv) {this.cv=cv;this.reset();}
    /** Functionality: Release state. Inputs: None. Outputs: empty history.
     * Logic: replace all buffers. Constraints: no persistence. */
    reset() {this.history=[];this.tracks=[];this.width=this.height=0;this.sourceAt=null;this.current=null;this.index=-1;this.corners=null;
      this.diagnostics={detected_count:0,seeded_count:0,excluded:{},lost:{}};}
    /** Functionality: Prepare image levels. Inputs: RGBA/dimensions. Outputs: gray pyramid.
     * Logic: three levels. Constraints: dimensions validated by caller. */
    pyramid(rgba,width,height) {
      const cv=this.cv,pyramid=new cv.pyramid_t(3);pyramid.allocate(width,height,cv.U8_t|cv.C1_t);
      cv.imgproc.grayscale(rgba,width,height,pyramid.data[0]);pyramid.build(pyramid.data[0],true);return pyramid;
    }
    /** Functionality: Advance local image history. Inputs: bounded RGBA/dimensions/time. Outputs: estimates.
     * Logic: reset on orientation change. Constraints: at most 28 frames and 2100 ms of history. */
    frame({rgba,width,height,at}) {
      if (!Number.isFinite(at)||width<32||height<32||width>320||height>320||rgba.length!==width*height*4) throw new TypeError('Invalid motion frame');
      if (this.width!==width||this.height!==height) {this.reset();this.width=width;this.height=height;}
      if (this.history.length&&at<=this.history.at(-1).at) return this.result();
      const frame={at,pyramid:this.pyramid(rgba,width,height)};
      this.history.push(frame);
      while(this.history.length>28||this.history.length>1&&at-this.history[0].at>2100) this.history.shift();
      if(this.current&&this.sourceAt!==null&&at-this.sourceAt<2000) this.advance(frame);
      else {this.drop('detection_expired');this.current=frame;}
      return this.result();
    }
    /** Functionality: Catch a delayed detection up to current pixels. Inputs: exact retained source JPEG,
     * original time/index and boxes. Outputs: estimates. Logic: seed features and replay image history.
     * Constraints: six boxes, twenty features each; two-second original-capture deadline. */
    seed({rgba,width,height,at,index,boxes,sourceWidth,sourceHeight}) {
      if(width!==this.width||height!==this.height||!Number.isInteger(index)||index<=this.index||!this.history.length) return this.result();
      if(rgba.length!==width*height*4||!Array.isArray(boxes)) throw new TypeError('Invalid motion seed');
      if (!Number.isFinite(at)||at>this.history.at(-1).at+100||this.history.at(-1).at-at>=2000||
          !Number.isFinite(sourceWidth)||!Number.isFinite(sourceHeight)||sourceWidth<=0||sourceHeight<=0) return this.result();
      this.index=index;this.sourceAt=at;this.tracks=[];
      this.diagnostics={detected_count:boxes.length,seeded_count:0,excluded:{},lost:{}};
      const source={at,pyramid:this.pyramid(rgba,width,height)};
      const corners=this.corners??=Array.from({length:width*height},()=>new this.cv.keypoint_t());
      this.cv.fast_corners.set_threshold(18);
      const count=this.cv.fast_corners.detect(source.pyramid.data[0],corners,8);
      const available=corners.slice(0,count).sort((a,b)=>b.score-a.score);
      const candidates=boxes.map((box,sourceIndex)=>({box,sourceIndex}))
        .sort((a,b)=>(b.box.alert_priority||0)-(a.box.alert_priority||0)).slice(0,6);
      const excluded=this.diagnostics.excluded;
      if(boxes.length>6)excluded.tracking_budget=boxes.length-6;
      for(const {box,sourceIndex} of candidates) {
        if(!Array.isArray(box.xyxy)||box.xyxy.length!==4||!box.xyxy.every(Number.isFinite)) {
          excluded.invalid_geometry=(excluded.invalid_geometry||0)+1;continue;
        }
        const rect=box.xyxy.map((v,i)=>v*(i%2?height/sourceHeight:width/sourceWidth));
        const [x1,y1,x2,y2]=rect,w=x2-x1,h=y2-y1;
        if(w<14||h<14){excluded.small_target=(excluded.small_target||0)+1;continue;}
        const points=[];
        for(const point of available) {
          if(point.x<x1+w*.12||point.x>x2-w*.12||point.y<y1+h*.12||point.y>y2-h*.12) continue;
          if(points.some(p=>(p[0]-point.x)**2+(p[1]-point.y)**2<36)) continue;
          points.push([point.x,point.y]);if(points.length===20) break;
        }
        if(points.length>=5) {
          this.tracks.push({box:{...box,xyxy:rect,source_box_index:sourceIndex},points,initial:points.length});
          this.diagnostics.seeded_count++;
        } else excluded.insufficient_features=(excluded.insufficient_features||0)+1;
      }
      this.current=source;
      for(const frame of this.history) if(frame.at>at) this.advance(frame);
      return this.result();
    }
    /** Functionality: Estimate one image step. Inputs: next pyramid/time. Outputs: updated visual tracks.
     * Logic: forward/backward agreement and displacement consensus reject ambiguous patches.
     * Constraints: gaps over 220 ms or loss of feature support clear estimates; translation only. */
    advance(frame) {
      const gap=frame.at-this.current.at;
      if(gap<=0) return;
      if(gap>220||frame.at-this.sourceAt>=2000) {
        this.drop(frame.at-this.sourceAt>=2000?'detection_expired':'sampling_gap');this.current=frame;return;
      }
      for(const track of this.tracks) {
        const count=track.points.length,previous=new Float32Array(track.points.flat());
        const next=new Float32Array(count*2),back=new Float32Array(count*2);
        const ok=new Uint8Array(count),backOk=new Uint8Array(count);
        this.cv.optical_flow_lk.track(this.current.pyramid,frame.pyramid,previous,next,count,15,15,ok,.01,.001);
        this.cv.optical_flow_lk.track(frame.pyramid,this.current.pyramid,next,back,count,15,15,backOk,.01,.001);
        const pairs=[];
        for(let i=0;i<count;i++) {
          const x=next[i*2],y=next[i*2+1],dx=x-previous[i*2],dy=y-previous[i*2+1];
          if(ok[i]&&backOk[i]&&Number.isFinite(x)&&Number.isFinite(y)&&x>=4&&y>=4&&x<this.width-4&&y<this.height-4&&
             Math.hypot(back[i*2]-previous[i*2],back[i*2+1]-previous[i*2+1])<=1.5&&Math.hypot(dx,dy)<=30)
            pairs.push({x,y,dx,dy});
        }
        if(pairs.length<Math.max(5,Math.ceil(track.initial*.4))) {this.lose(track,'flow_unreliable');continue;}
        const dx=median(pairs.map(p=>p.dx)),dy=median(pairs.map(p=>p.dy));
        const inliers=pairs.filter(p=>Math.hypot(p.dx-dx,p.dy-dy)<=2.5);
        if(inliers.length<Math.max(5,Math.ceil(pairs.length*.6))) {this.lose(track,'inconsistent_motion');continue;}
        track.box.xyxy=track.box.xyxy.map((value,i)=>value+(i%2?dy:dx));
        const [x1,y1,x2,y2]=track.box.xyxy;
        if(x2<=0||y2<=0||x1>=this.width||y1>=this.height)this.lose(track,'outside_frame');
        else track.points=inliers.map(p=>[p.x,p.y]);
      }
      this.tracks=this.tracks.filter(track=>track.points.length>=5);this.current=frame;
    }
    /** Functionality: Account for one lost visual track. Inputs: track/reason. Outputs: empty points/count.
     * Logic: count only the first loss. Constraints: source detection and risk metadata stay intact. */
    lose(track,reason) {
      if(track.points.length)this.diagnostics.lost[reason]=(this.diagnostics.lost[reason]||0)+1;
      track.points=[];
    }
    /** Functionality: Invalidate current visual estimates. Inputs: reason. Outputs: loss counts.
     * Logic: visit active tracks once. Constraints: no duplicate cumulative losses on later frames. */
    drop(reason){for(const track of this.tracks)this.lose(track,reason);this.tracks=[];}
    /** Functionality: Return isolated estimates. Inputs: current tracks. Outputs: copied boxes and times.
     * Logic: preserve metadata/capture age. Constraints: caller must not treat positions as new inference. */
    result() {return {boxes:this.tracks.map(track=>({...track.box,xyxy:[...track.box.xyxy]})),
      width:this.width,height:this.height,capturedAt:this.sourceAt,displayAt:this.current?.at??null,index:this.index,
      diagnostics:{...this.diagnostics,active_count:this.tracks.length,
        excluded:{...this.diagnostics.excluded},lost:{...this.diagnostics.lost}}};}
  }
  globalThis.PrsMotionTracker=PrsMotionTracker;
})();
