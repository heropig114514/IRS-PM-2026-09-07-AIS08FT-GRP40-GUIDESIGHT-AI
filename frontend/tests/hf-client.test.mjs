/* Responsibilities: Verify explicit HF backend/precision validation in detection transport.
 * Implementation: Fake fetch and JPEG magic bytes; no browser, GPU, camera or network.
 * Related Modules: detection-client.js; core.hf_vision/hf_services.
 * Declaration Index: testHF; testHF.fetchFn.
 * Variable Index: None; all fixtures are local to the test.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { DetectionClient } from "../detection-client.js";

/* Functionality: Check HF response cannot silently come from CPU/FP16.
 * Inputs: fake response backend/precision fixtures. Outputs: accepted/rejected Promise assertions.
 * Logic: real client handles injected config and frame metadata.
 * Constraints: synthetic JPEG bytes are not a real encoded image or accuracy test. */
test("HF 模式严格匹配 PyTorch ZeroGPU FP32", async function testHF() {
  for (const [backend, precision, valid] of [
    ["PyTorch ZeroGPU", "FP32", true], ["CPU", "FP32", false], ["PyTorch ZeroGPU", "FP16", false],
  ]) {
    /* Functionality: Return controlled transport response. Inputs: url and ignored options.
     * Outputs: real Response; Logic: route suffix selects config or JPEG.
     * Constraints: no actual remote connection. */
    async function fetchFn(url) {
      if (url.endsWith("config/")) return Response.json({
        ready: true, model: "yolo26n", classes: 80, backend, precision,
      });
      return new Response(Uint8Array.from([255, 216, 1, 255, 217]), {headers: {
        "Content-Type": "image/jpeg", "X-Model": "yolo26n",
        "X-Detection-Count": "1", "X-Inference-Ms": "10",
        "X-Inference-Backend": backend, "X-Inference-Precision": precision,
      }});
    }
    const client = new DetectionClient("https://test.invalid/vision/hf/", {
      fetchFn, backend: "PyTorch ZeroGPU",
    });
    if (valid) {
      await client.connect();
      assert.equal((await client.sendFrame(new ArrayBuffer(5))).detections, 1);
    } else {
      await assert.rejects(client.connect(), /配置无效/);
      await assert.rejects(client.sendFrame(new ArrayBuffer(5)), /格式或模型不匹配/);
    }
  }
});
