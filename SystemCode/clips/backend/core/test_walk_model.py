"""Responsibilities: Verify walk-model selection, manifest identity and the pedestrian-light rule.
Implementation: Real core.detection_model / core.detector / integration.protocol functions with
the committed manifests and synthetic boxes; no Torch, GPU or model inference.
Related Modules: core.detection_model; core.detector.pedestrian_lights/mark_front_light;
integration.protocol.read_manifest/model_contract/validate_contract; WALK-MODEL.md.
Declaration Index: box; WalkModelTests; WalkModelTests.test_selection;
WalkModelTests.test_manifests; WalkModelTests.test_rejects_identity_changes;
WalkModelTests.test_light_states; WalkModelTests.test_light_grouping;
WalkModelTests.test_front_light; WalkModelTests.test_local_contract;
WalkModelTests.test_default_thresholds.
Variable Index: RULE — the committed walk manifest's colour rule; WALK_MANIFEST/COCO_MANIFEST —
committed manifests read once.
"""

import copy
import json
from unittest.mock import patch

from django.test import SimpleTestCase

from core import detection_model, detector
from integration import protocol
from integration.errors import IntegrationError

WALK_MANIFEST = json.loads((detection_model.ROOT / "models/yolo26n-walk.json").read_text("utf-8"))
COCO_MANIFEST = json.loads((detection_model.ROOT / "models/yolo26n.json").read_text("utf-8"))
RULE = detection_model.light_rule(WALK_MANIFEST)


def box(label, score, xyxy=(10, 10, 20, 40), class_id=0):
    """Functionality: Build one decoded-box fixture. Inputs: label/score/xyxy/class id.
    Outputs: dict like core.detector.decode_boxes. Logic: plain copy. Constraints: synthetic only.
    """
    return {"xyxy": list(xyxy), "score": score, "class_id": class_id, "label": label}


class WalkModelTests(SimpleTestCase):
    """Functionality: Check the opt-in walk model without touching the COCO default.
    Logic: environment mappings are explicit; manifests are the committed files.
    Constraints: no accuracy, latency or GPU behaviour is established here.
    """

    def test_selection(self):
        """Functionality: Default, explicit and unknown PRS_DETECTION_MODEL values.
        Inputs: environment mappings. Outputs: profile names / files or ValueError.
        Logic: blank means COCO. Constraints: unknown names never fall back.
        """
        self.assertEqual(detection_model.selected_model({}), "yolo26n")
        self.assertEqual(detection_model.selected_model({"PRS_DETECTION_MODEL": " "}), "yolo26n")
        self.assertEqual(detection_model.selected_model({"PRS_DETECTION_MODEL": "yolo26n-walk"}),
                         "yolo26n-walk")
        with self.assertRaises(ValueError):
            detection_model.selected_model({"PRS_DETECTION_MODEL": "yolo26s"})
        weights, manifest = detection_model.model_files("yolo26n-walk")
        self.assertEqual((weights.name, manifest.name), ("yolo26n-walk.pt", "yolo26n-walk.json"))

    def test_manifests(self):
        """Functionality: Both committed manifests pass; only the walk model has a colour rule.
        Inputs: committed JSON. Outputs: rule values. Logic: real validator.
        Constraints: the 0.364 threshold is the documented ImVisible calibration.
        """
        detection_model.validate_manifest(COCO_MANIFEST)
        detection_model.validate_manifest(WALK_MANIFEST)
        self.assertIsNone(detection_model.light_rule(COCO_MANIFEST))
        self.assertEqual(RULE["classes"], ("ped_red", "ped_green", "ped_off"))
        self.assertEqual(RULE["green_threshold"], 0.364)
        self.assertEqual(WALK_MANIFEST["names"][RULE["class_id"]], "ped_red")
        self.assertEqual(len(WALK_MANIFEST["names"]), 87)

    def test_rejects_identity_changes(self):
        """Functionality: Class count, COCO order and colour-rule changes are refused.
        Inputs: mutated manifest copies. Outputs: ValueError. Logic: one change per case.
        Constraints: identity only.
        """
        short = copy.deepcopy(WALK_MANIFEST)
        short["names"] = short["names"][:86]
        moved = copy.deepcopy(WALK_MANIFEST)
        moved["names"][0], moved["names"][1] = moved["names"][1], moved["names"][0]
        rule = copy.deepcopy(WALK_MANIFEST)
        rule["pedestrian_lights"]["green_threshold"] = 1.5
        unknown = dict(COCO_MANIFEST, model="yolo26s")
        for manifest in (short, moved, rule, unknown):
            with self.assertRaises(ValueError):
                detection_model.validate_manifest(manifest)

    def test_light_states(self):
        """Functionality: Green needs the threshold and to beat red; otherwise red, else unknown.
        Inputs: single colour boxes. Outputs: merged neutral boxes.
        Logic: real pedestrian_lights. Constraints: the class name never carries the colour.
        """
        cases = [([box("ped_green", 0.40)], "green"), ([box("ped_green", 0.30)], "unknown"),
                 ([box("ped_red", 0.06)], "red"), ([box("ped_off", 0.50)], "unknown"),
                 ([box("ped_green", 0.50), box("ped_red", 0.55)], "red")]
        for boxes, state in cases:
            merged = detector.pedestrian_lights(boxes, RULE)
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["label"], detector.PEDESTRIAN_LIGHT)
            self.assertEqual(merged[0]["light_state"], state)
            self.assertEqual(merged[0]["class_id"], RULE["class_id"])
        self.assertIs(detector.pedestrian_lights(cases[0][0], None), cases[0][0])

    def test_light_grouping(self):
        """Functionality: Overlapping colour boxes of one lamp merge; separate lamps stay apart.
        Inputs: two overlapping and one distant colour box plus a person. Outputs: three boxes.
        Logic: kept geometry is the highest-score box. Constraints: other classes untouched.
        """
        person = box("person", 0.9, (0, 0, 5, 5))
        boxes = [person, box("ped_red", 0.2, (10, 10, 20, 40)),
                 box("ped_green", 0.7, (11, 11, 21, 41)), box("ped_green", 0.8, (60, 10, 70, 40))]
        merged = detector.pedestrian_lights(boxes, RULE)
        self.assertEqual(merged[0], person)
        lights = merged[1:]
        self.assertEqual([light["light_state"] for light in lights], ["green", "green"])
        self.assertEqual(lights[1]["xyxy"], [11, 11, 21, 41])
        self.assertEqual(lights[1]["light_scores"], {"red": 0.2, "green": 0.7, "off": 0.0})

    def test_front_light(self):
        """Functionality: The merged signal takes part in front-light marking.
        Inputs: an off-centre pedestrian light and a centred generic traffic light.
        Outputs: the pedestrian light is front. Logic: real mark_front_light.
        Constraints: no deletion.
        """
        merged = detector.pedestrian_lights([box("ped_red", 0.3, (20, 10, 30, 40)),
                                             box("traffic light", 0.9, (45, 10, 55, 40))], RULE)
        marked = {item["label"]: item["front_light"]
                  for item in detector.mark_front_light(merged, (100, 100))}
        # The pedestrian light wins even though the generic light is closer to the centre.
        self.assertEqual(marked, {detector.PEDESTRIAN_LIGHT: True, "traffic light": False})

    def test_local_contract(self):
        """Functionality: The local hello follows the selected model and validates both ways.
        Inputs: PRS_DETECTION_MODEL and committed manifests. Outputs: 87-class walk contract.
        Logic: read_manifest default path, model_contract, validate_contract. Constraints: a walk
        hello with COCO names or a forged class count is refused.
        """
        with patch.dict("os.environ", {"PRS_DETECTION_MODEL": "yolo26n-walk"}):
            manifest = protocol.read_manifest()
            contract = protocol.model_contract("a" * 32, manifest, session_seconds=55)
        self.assertEqual((contract["model"], contract["classes"]), ("yolo26n-walk", 87))
        self.assertIs(protocol.validate_contract(contract), contract)
        coco = protocol.model_contract("a" * 32, protocol.read_manifest(), session_seconds=55)
        self.assertEqual((coco["model"], coco["classes"]), ("yolo26n", 80))
        for forged in (dict(contract, names=COCO_MANIFEST["names"]), dict(contract, classes=80)):
            with self.assertRaises(IntegrationError):
                protocol.validate_contract(forged)

    def test_default_thresholds(self):
        """Functionality: The COCO default keeps exactly its traffic-light threshold.
        Inputs: shared CLASS_CONF and the walk rule. Outputs: default dict and walk threshold.
        Logic: walk colour thresholds come from the manifest, not the shared table.
        Constraints: guards the unchanged default metadata.
        """
        self.assertEqual(detector.CLASS_CONF, {"traffic light": 0.12})
        self.assertEqual(RULE["class_confidence"], 0.05)
