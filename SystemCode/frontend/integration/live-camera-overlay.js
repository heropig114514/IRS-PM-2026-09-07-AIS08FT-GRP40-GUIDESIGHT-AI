/* Responsibilities: Combine brief detection hints and bounded local visual tracking.
 * Implementation: Separate 150 ms layer lifetimes; source-index matching prevents duplicate boxes.
 * Related Modules: live-latency.js, local-motion.js, tests/live-camera-overlay.test.mjs.
 * Declaration Index: overlayLifetime; validBox; LiveCameraOverlay; LiveCameraOverlay.constructor;
 * LiveCameraOverlay.clear; LiveCameraOverlay.clearTracking; LiveCameraOverlay.show;
 * LiveCameraOverlay.fits; LiveCameraOverlay.drawBox; LiveCameraOverlay.redraw.
 * Variable Index: source retains the newest detection; motion is a same-source visual estimate;
 * seen/hinted retain only source indices for scalar counts, never exported geometry.
 */
/** Functionality: Bound visibility. Inputs: capture/current time and deadline. Outputs: milliseconds.
 * Logic: reject expired/future captures. Constraints: tracking never refreshes original capture age. */
export function overlayLifetime(capturedAt, now, maxAgeMs) {
  const age = now - capturedAt;
  return Number.isFinite(capturedAt) && Number.isFinite(age) && age >= 0 && age < maxAgeMs
    ? Math.min(150, maxAgeMs - age) : 0;
}
/** Functionality: Validate drawable geometry. Inputs: box. Outputs: boolean.
 * Logic: finite coordinates and positive area. Constraints: validated metadata remains unchanged. */
function validBox(box) {
  return Array.isArray(box?.xyxy) && box.xyxy.length === 4 && box.xyxy.every(Number.isFinite)
    && box.xyxy[2] > box.xyxy[0] && box.xyxy[3] > box.xyxy[1];
}
/** Functionality: Show all recent detections, tracking selected targets between results.
 * Logic: independent source/track deadlines and source-index deduplication.
 * Constraints: visual estimates never update inference, risk, distance or speech. */
export class LiveCameraOverlay {
  /** Functionality: Bind dependencies. Inputs: DOM, clock, timers, metrics callback. Outputs: idle overlay.
   * Logic: retain original deadline. Constraints: native timers require their global receiver. */
  constructor({video, canvas, label, maxAgeMs, onMetrics = () => {}, now = () => performance.now(),
    setTimer = (callback, delay) => globalThis.setTimeout(callback, delay),
    clearTimer = timer => globalThis.clearTimeout(timer)}) {
    Object.assign(this, {video, canvas, label, maxAgeMs, onMetrics, now, setTimer, clearTimer});
    this.timer = null; this.source = this.motion = null;
  }
  /** Functionality: Release both layers. Inputs: layer/timer state. Outputs: hidden overlay.
   * Logic: cancel callbacks and discard source references. Constraints: does not stop media. */
  clear() {
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null; this.source = this.motion = null;
    const context = this.canvas.getContext('2d'); context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.canvas.hidden = this.label.hidden = true;
  }
  /** Functionality: Remove only tracking estimates. Inputs: current layers. Outputs: remaining hints.
   * Logic: source hint keeps its original expiry. Constraints: does not extend or cancel its lifetime. */
  clearTracking() { this.motion = null; this.redraw(); }
  /** Functionality: Admit a detection or matching local estimate. Inputs: original capture and metadata.
   * Outputs: whether any box is drawn. Logic: a new source replaces both layers; tracking matches
   * frame index and epoch and owns a separate expiry. Constraints: no old-source resurrection. */
  show(event) {
    const meta = event.result?.metadata;
    if (!meta || !Array.isArray(meta.boxes)) return false;
    const now = this.now(), expiresAt = now + overlayLifetime(event.capturedAt, now, this.maxAgeMs);
    const layer = {meta, capturedAt: event.capturedAt, expiresAt};
    if (event.localTracking) {
      if (!this.source || meta.index !== this.source.meta.index ||
          meta.tracking_epoch !== this.source.meta.tracking_epoch || event.capturedAt !== this.source.capturedAt) return false;
      this.motion = layer;
    } else {
      this.source = layer; this.motion = null;
      this.seen = new Set(); this.hinted = new Set(); this.trackedPeak = 0;
    }
    return this.redraw();
  }
  /** Functionality: Validate image-to-preview mapping. Inputs: metadata. Outputs: boolean.
   * Logic: positive dimensions and aspect match. Constraints: rotation never reuses old geometry. */
  fits(meta) {
    return this.video.videoWidth > 0 && this.video.videoHeight > 0 &&
      Number.isFinite(meta.width) && Number.isFinite(meta.height) && meta.width > 0 && meta.height > 0 &&
      Math.abs((this.video.videoWidth / this.video.videoHeight) / (meta.width / meta.height) - 1) <= .03;
  }
  /** Functionality: Draw one hint or track. Inputs: context, box, dimensions and layer kind.
   * Outputs: canvas drawing. Logic: dashed unlabeled historical hints, solid labeled tracked estimates.
   * Constraints: short hint labels are omitted to reduce dense-scene clutter and main-thread work. */
  drawBox(context, box, meta, tracked) {
    const width = this.video.clientWidth, height = this.video.clientHeight;
    const scale = Math.min(width / meta.width, height / meta.height);
    const ox = (width - meta.width * scale) / 2, oy = (height - meta.height * scale) / 2;
    const [x1, y1, x2, y2] = box.xyxy;
    context.lineWidth = tracked ? 2 : 1.5;
    context.setLineDash(tracked ? [] : [5, 3]);
    context.strokeStyle = box.alert_required ? '#ff7043' : '#00ff8c';
    context.strokeRect(ox+x1*scale, oy+y1*scale, (x2-x1)*scale, (y2-y1)*scale);
    if (!tracked) return;
    const text = `${box.label || '目标'}${Number.isFinite(box.distance_m) ? ` ${box.distance_m.toFixed(1)}m` : ''}`;
    const x = Math.max(0, ox+x1*scale), y = Math.max(18, oy+y1*scale);
    context.font = '14px sans-serif';
    context.fillStyle = '#102019'; context.fillRect(x, y-18, Math.min(width-x, context.measureText(text).width+6), 18);
    context.fillStyle = 'white'; context.fillText(text, x+3, y-4, Math.max(1, width-x-6));
  }
  /** Functionality: Compose layers and report counts. Inputs: current time/layers. Outputs: drawing/metrics.
   * Logic: each source box is drawn once; expirations never reset during redraw. Track updates can
   * replace matching hints but cannot erase other live hints. Count unique boxes drawn during this
   * detection's lifetime separately from currently hidden boxes. Constraints: counts do not prove
   * sensor-to-screen paint or model accuracy; exports contain no identities or coordinates. */
  redraw() {
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
    const {canvas, video, label, source, motion} = this;
    const context = canvas.getContext('2d'), now = this.now();
    context.setTransform(1, 0, 0, 1, 0, 0); context.clearRect(0, 0, canvas.width, canvas.height);
    canvas.hidden = label.hidden = true;
    if (!source) return false;
    const width = video.clientWidth, height = video.clientHeight;
    const viewReady = width > 0 && height > 0 && video.videoWidth > 0 && video.videoHeight > 0;
    if (viewReady) {
      const ratio = Math.min(globalThis.devicePixelRatio || 1, 3);
      const cw = Math.round(width * ratio), ch = Math.round(height * ratio);
      if (canvas.width !== cw) canvas.width = cw;
      if (canvas.height !== ch) canvas.height = ch;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
    }
    const tracked = new Map();
    if (viewReady && motion && motion.expiresAt > now && this.fits(motion.meta)) {
      for (const box of motion.meta.boxes) {
        const index = box.source_box_index;
        if (Number.isInteger(index) && index >= 0 && index < source.meta.boxes.length && validBox(box)) tracked.set(index, box);
      }
    }
    const hiddenReasons = {};
    let hintCount = 0, trackCount = 0;
    source.meta.boxes.forEach((box, index) => {
      let reason = null;
      if (tracked.has(index)) {
        this.drawBox(context, tracked.get(index), motion.meta, true);
        trackCount++; this.seen.add(index);
      } else if (!viewReady) reason = 'preview_unavailable';
      else if (!this.fits(source.meta)) reason = 'aspect_mismatch';
      else if (!validBox(box)) reason = 'invalid_geometry';
      else if (!overlayLifetime(source.capturedAt, now, this.maxAgeMs)) reason = 'detection_expired';
      else if (source.expiresAt <= now) reason = 'hint_expired';
      else {
        this.drawBox(context, box, source.meta, false);
        hintCount++; this.seen.add(index); this.hinted.add(index);
      }
      if (reason) hiddenReasons[reason] = (hiddenReasons[reason] || 0) + 1;
    });
    this.trackedPeak = Math.max(this.trackedPeak, trackCount);
    const visible = hintCount + trackCount, total = source.meta.boxes.length;
    if (visible) {
      label.textContent = `${((now-source.capturedAt)/1000).toFixed(1)} 秒前检测 · `
        + (trackCount ? `本地跟踪（估计位置）${trackCount}` : '')
        + (hintCount ? `${trackCount ? ' · ' : ''}虚线提示 ${hintCount}` : '');
      canvas.hidden = label.hidden = false;
    }
    this.onMetrics({frame_index: source.meta.index, tracking_epoch: source.meta.tracking_epoch,
      detected_count: total, visible_count: visible, tracked_count: trackCount, hint_count: hintCount,
      tracked_peak_count: this.trackedPeak, displayed_unique_count: this.seen.size,
      hinted_unique_count: this.hinted.size, never_displayed_count: total-this.seen.size,
      hidden_count: total-visible, hidden_reasons: hiddenReasons});
    const deadlines = [source.expiresAt, motion?.expiresAt].filter(value => value > now);
    if (deadlines.length) this.timer = this.setTimer(() => this.redraw(), Math.max(1, Math.min(...deadlines)-now));
    return visible > 0;
  }
}
