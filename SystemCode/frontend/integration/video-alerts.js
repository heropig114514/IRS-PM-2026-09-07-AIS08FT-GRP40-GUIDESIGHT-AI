/** Responsibilities: Select video alerts and speak direction/category with server-confirmed fast approach.
 * Implementation: Server-required alerts only; priority, positive TTC, distance and track ordering;
 * short local speech without numeric estimates; no risk reclassification or new speed thresholds.
 * Related Modules: video-mvp.js, guidance.js, catalog.js; SystemCode/backend/realtime/risk.py;
 * tests/integration-video-alerts.test.mjs.
 * Declaration Index: usableTtc; usableDistance; selectVideoAlert; isFastApproaching; locationText; videoAlertText.
 * Variable Index: DIRECTIONS contains camera-relative positions; ZH_LABELS translates the 80 COCO labels plus the
 * walk model's obstacle and pedestrian-light labels;
 * PREFIXES contains existing grade words. No algorithm thresholds, scores, voice settings or cooldowns change.
 */
import {languageOf} from "./catalog.js";

const DIRECTIONS = {
  front: ["前方", "ahead"],
  front_left: ["左前方", "ahead left"],
  front_right: ["右前方", "ahead right"],
  left: ["左侧", "to the left"],
  right: ["右侧", "to the right"],
  behind: ["后方", "behind"],
  unknown: [null, "in an unknown direction"],
};
const ZH_LABELS = {
  person: "行人", bicycle: "自行车", car: "汽车", motorcycle: "摩托车", airplane: "飞机", bus: "巴士",
  train: "火车", truck: "货车", boat: "船", "traffic light": "交通信号灯", "fire hydrant": "消防栓",
  "stop sign": "停车标志", "parking meter": "停车计时器", bench: "长椅", bird: "鸟", cat: "猫", dog: "狗",
  horse: "马", sheep: "羊", cow: "牛", elephant: "大象", bear: "熊", zebra: "斑马", giraffe: "长颈鹿",
  backpack: "背包", umbrella: "雨伞", handbag: "手提包", tie: "领带", suitcase: "行李箱", frisbee: "飞盘",
  skis: "双板滑雪板", snowboard: "单板滑雪板", "sports ball": "球", kite: "风筝", "baseball bat": "棒球棒",
  "baseball glove": "棒球手套", skateboard: "滑板", surfboard: "冲浪板", "tennis racket": "网球拍",
  bottle: "瓶子", "wine glass": "高脚杯", cup: "杯子", fork: "叉子", knife: "刀", spoon: "勺子", bowl: "碗",
  banana: "香蕉", apple: "苹果", sandwich: "三明治", orange: "橙子", broccoli: "西兰花", carrot: "胡萝卜",
  "hot dog": "热狗", pizza: "披萨", donut: "甜甜圈", cake: "蛋糕", chair: "椅子", couch: "沙发",
  "potted plant": "盆栽", bed: "床", "dining table": "餐桌", toilet: "马桶", tv: "电视", laptop: "笔记本电脑",
  mouse: "鼠标", remote: "遥控器", keyboard: "键盘", "cell phone": "手机", microwave: "微波炉", oven: "烤箱",
  toaster: "烤面包机", sink: "水槽", refrigerator: "冰箱", book: "书", clock: "时钟", vase: "花瓶",
  scissors: "剪刀", "teddy bear": "泰迪熊玩偶", "hair drier": "吹风机", toothbrush: "牙刷",
  // Walking-aid model (yolo26n-walk) obstacles and its merged pedestrian-signal label.
  pole: "柱子", bollard: "护栏柱", traffic_cone: "锥桶", stairs: "台阶", "pedestrian light": "人行信号灯",
};
const PREFIXES = {
  medium: ["注意", "Caution. "],
  high: ["警告，", "Warning. "],
  critical: ["危险，", "Danger. "],
};

/** Functionality: Read an existing positive TTC without calculating or coercing a replacement.
 * Inputs: server box. Outputs: positive seconds or null.
 * Logic: finite numeric values greater than zero are usable; Constraints: invalid/missing values never mean zero or safety. */
function usableTtc(box) {
  return Number.isFinite(box.ttc_sec) && box.ttc_sec > 0 ? box.ttc_sec : null;
}

/** Functionality: Read the current server distance for selection only.
 * Inputs: server box. Outputs: positive meters or null.
 * Logic: reject absent/nonfinite/nonpositive values; Constraints: no cached distance, coercion or new depth estimate. */
function usableDistance(box) {
  return Number.isFinite(box.distance_m) && box.distance_m > 0 ? box.distance_m : null;
}

/** Functionality: Select one algorithm-required video alert without mutating its prediction.
 * Inputs: validated server boxes. Outputs: original selected box or null.
 * Logic: priority descending, known positive TTC ascending, distance ascending, track ID ascending.
 * Constraints: TTC alone never creates an alert or overrides a higher server priority; no new risk thresholds. */
export function selectVideoAlert(boxes) {
  return boxes.filter(box => box.alert_required === true && box.alert_priority > 0)
    .sort((a, b) => b.alert_priority - a.alert_priority ||
      (usableTtc(a) ?? Infinity) - (usableTtc(b) ?? Infinity) ||
      (usableDistance(a) ?? Infinity) - (usableDistance(b) ?? Infinity) || a.track_id - b.track_id)[0] || null;
}

/** Functionality: Reuse the backend's confirmed faster-closing band for the approach word.
 * Inputs: validated box motion state, signed relative speed and risk reasons. Outputs: boolean.
 * Logic: require approaching state, finite negative speed, effective approaching reason and closing_mps_ge_2.
 * Constraints: that reason identifies the existing default >=2m/s band; no copied numeric comparison, new threshold,
 * inferred motion from TTC or changed risk grade; inconsistent/unknown motion never claims fast approach. */
function isFastApproaching(box) {
  return box.motion_state === "approaching" && Number.isFinite(box.relative_speed_mps) &&
    box.relative_speed_mps < 0 && Array.isArray(box.risk_reasons) &&
    box.risk_reasons.includes("approaching") && box.risk_reasons.includes("closing_mps_ge_2");
}

/** Functionality: Describe the target's camera-relative location with an optional confirmed approach word.
 * Inputs: validated server box, Chinese-language flag and fast-approach flag. Outputs: one short target sentence.
 * Logic: slow medium alerts use a notice phrase; other alerts state presence, adding approach only for the faster band.
 * Constraints: unmapped Chinese labels explicitly say object; English keeps the validated label; unknown direction
 * stays explicit, and no distance, TTC, speed value, compass bearing or safe movement direction is spoken. */
function locationText(box, zh, fastApproach) {
  const direction = DIRECTIONS[box.direction] || DIRECTIONS.unknown;
  if (zh) {
    const label = ZH_LABELS[box.label] || "物体";
    if (box.risk_level === "medium" && !fastApproach)
      return direction[0] ? `${direction[0]}的${label}。` : `${label}，方向未知。`;
    const target = `有${label}${fastApproach ? "靠近" : ""}`;
    return direction[0] ? `${direction[0]}${target}。` : `${target}，方向未知。`;
  }
  const label = box.label.charAt(0).toUpperCase() + box.label.slice(1);
  return `${label} ${direction[1]}${fastApproach ? " is approaching" : ""}.`;
}

/** Functionality: Format short video speech from existing grade, direction, category and faster-closing evidence.
 * Inputs: selected validated medium/high/critical alert and explicit page language. Outputs: spoken text.
 * Logic: retain the server grade; only confirmed faster approach adds the approach word, regardless of grade.
 * Constraints: no numeric estimates, new urgency rules, risk downgrade, action long sentence or text logging;
 * distance/TTC remain available to the existing selection and display paths. */
export function videoAlertText(box, language) {
  const zh = languageOf(language) === "zh-CN";
  const prefixes = PREFIXES[box.risk_level];
  if (!prefixes) throw new TypeError("Video speech requires a validated alert grade");
  const fastApproach = isFastApproaching(box);
  const prefix = prefixes[zh ? 0 : 1] + (zh && box.risk_level === "medium" && fastApproach ? "，" : "");
  return prefix + locationText(box, zh, fastApproach);
}
