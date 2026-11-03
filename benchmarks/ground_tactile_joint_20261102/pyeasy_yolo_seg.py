#!/usr/bin/env python3
"""YOLO11-seg decoder and RDK X5 pyeasy_dnn runtime adapter."""
from dataclasses import dataclass, field
from typing import List

import cv2
import numpy as np
from runtime_utils import postprocess as post_utils
from runtime_utils import preprocess as pre_utils


@dataclass
class SegConfig:
    model_path: str = ""
    classes_num: int = 1
    score_thres: float = 0.50
    nms_thres: float = 0.70
    reg: int = 16
    mc: int = 32
    resize_type: int = 1
    strides: List[int] = field(default_factory=lambda: [8, 16, 32])
    input_w: int = 640
    input_h: int = 640


class YOLOSegDecoder:
    def __init__(self, cfg: SegConfig):
        self.cfg = cfg
        self.weights_static = np.arange(cfg.reg, dtype=np.float32)[None, None, :]

    def decode(self, raw_outputs, ori_img_w, ori_img_h):
        if len(raw_outputs) != 10:
            raise RuntimeError(f"Expected 10 YOLO segmentation outputs, got {len(raw_outputs)}")
        boxes_all, scores_all, cls_all, mces_all = [], [], [], []
        conf_raw = -np.log(1.0 / self.cfg.score_thres - 1.0)
        for level_index, stride in enumerate(self.cfg.strides):
            base = level_index * 3
            cls_output = np.asarray(raw_outputs[base]).reshape(-1, self.cfg.classes_num)
            box_output = np.asarray(raw_outputs[base + 1])
            mc_output = np.asarray(raw_outputs[base + 2])
            scores, cls_ids, valid = post_utils.filter_classification(cls_output, conf_raw)
            if valid.size == 0:
                continue
            grid_size = self.cfg.input_h // stride
            boxes_all.append(post_utils.decode_boxes(
                box_output, valid, grid_size, stride, self.weights_static))
            scores_all.append(scores)
            cls_all.append(cls_ids.astype(np.int32))
            mces_all.append(post_utils.filter_mces(mc_output, valid))

        if not boxes_all:
            return (np.empty((0, 4), np.float32), np.empty((0,), np.float32),
                    np.empty((0,), np.int32), [])

        boxes = np.concatenate(boxes_all).astype(np.float32)
        scores = np.concatenate(scores_all).astype(np.float32)
        cls_ids = np.concatenate(cls_all).astype(np.int32)
        mces = np.concatenate(mces_all).astype(np.float32)
        keep = post_utils.NMS(boxes, scores, cls_ids, self.cfg.nms_thres)
        if not keep:
            return (np.empty((0, 4), np.float32), np.empty((0,), np.float32),
                    np.empty((0,), np.int32), [])
        boxes, scores, cls_ids, mces = boxes[keep], scores[keep], cls_ids[keep], mces[keep]
        proto = np.asarray(raw_outputs[9])
        if proto.shape[0] == 1:
            proto = proto[0]
        if proto.shape[-1] != self.cfg.mc and proto.shape[0] == self.cfg.mc:
            proto = np.transpose(proto, (1, 2, 0))
        scaled_boxes = post_utils.scale_coords_back(
            boxes.copy(), ori_img_w, ori_img_h, self.cfg.input_w,
            self.cfg.input_h, self.cfg.resize_type)
        masks = restore_masks(mces, proto, scaled_boxes, ori_img_w, ori_img_h,
                              self.cfg.input_w, self.cfg.input_h, self.cfg.resize_type)
        return scaled_boxes, scores, cls_ids, masks


def restore_masks(coefficients, proto, boxes, width, height, input_w=640, input_h=640, resize_type=1):
    """Restore continuous mask logits through letterbox before threshold/cropping.

    A probability threshold of 0.5 is logit 0. Cropping a proto patch and
    stretching it into a clipped box distorts instances crossing image edges.
    """
    masks = []
    if resize_type == 1:
        scale = min(input_w / width, input_h / height)
        nw, nh = int(width * scale), int(height * scale)
        left, top = (input_w - nw)//2, (input_h - nh)//2
    else:
        left, top, nw, nh = 0, 0, input_w, input_h
    for coefficient, box in zip(coefficients, boxes):
        logits = np.sum(proto * coefficient[None, None, :], axis=2)
        square = cv2.resize(logits, (input_w, input_h), interpolation=cv2.INTER_LINEAR)
        restored = cv2.resize(square[top:top+nh, left:left+nw], (width, height), interpolation=cv2.INTER_LINEAR)
        x1, y1, x2, y2 = [int(v) for v in box]
        x1, y1, x2, y2 = max(0,x1), max(0,y1), min(width,x2), min(height,y2)
        masks.append((restored[y1:y2, x1:x2] > 0).astype(np.uint8))
    return masks


def masks_to_full(masks, boxes, width, height):
    merged = np.zeros((height, width), np.uint8)
    for mask, box in zip(masks, boxes):
        x1, y1, x2, y2 = [int(v) for v in box]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(width, x2), min(height, y2)
        if x2 <= x1 or y2 <= y1:
            continue
        if mask.shape != (y2 - y1, x2 - x1):
            mask = cv2.resize(mask, (x2 - x1, y2 - y1), interpolation=cv2.INTER_NEAREST)
        merged[y1:y2, x1:x2] |= (mask > 0).astype(np.uint8)
    return merged


def analyze_path(mask):
    height, width = mask.shape
    ys, xs = np.nonzero(mask)
    if len(xs) < max(80, width * height // 1000):
        return {"visible": False, "direction": "NONE", "offset": 0.0,
                "coverage": 0.0, "reaches_bottom": False}
    lower = ys >= int(height * 0.55)
    use_x = xs[lower] if lower.any() else xs
    center_x = float(np.median(use_x))
    offset = (center_x - width / 2) / (width / 2)
    direction = "LEFT" if offset < -0.16 else "RIGHT" if offset > 0.16 else "CENTER"
    return {
        "visible": True,
        "direction": direction,
        "offset": offset,
        "coverage": float(mask.mean()),
        "reaches_bottom": bool(ys.max() >= int(height * 0.90)),
    }


class PyeasyYOLOSeg(YOLOSegDecoder):
    def __init__(self, cfg: SegConfig):
        try:
            from hobot_dnn import pyeasy_dnn
        except ImportError:
            from hobot_dnn_rdkx5 import pyeasy_dnn
        self.model = pyeasy_dnn.load(cfg.model_path)[0]
        prop = self.model.inputs[0].properties
        shape = tuple(prop.shape)
        if getattr(prop, "layout", "NCHW") == "NCHW":
            cfg.input_h, cfg.input_w = int(shape[2]), int(shape[3])
        else:
            cfg.input_h, cfg.input_w = int(shape[1]), int(shape[2])
        super().__init__(cfg)
        if len(self.model.outputs) != 10:
            raise RuntimeError(f"Compiled model has {len(self.model.outputs)} outputs; expected 10")

    def predict(self, bgr):
        resized = pre_utils.resized_image(
            bgr, self.cfg.input_w, self.cfg.input_h, self.cfg.resize_type)
        y, uv = pre_utils.bgr_to_nv12_planes(resized)
        nv12 = np.concatenate([y.reshape(-1), uv.reshape(-1)]).astype(np.uint8)
        outputs = [np.asarray(out.buffer) for out in self.model.forward(nv12)]
        return self.decode(outputs, bgr.shape[1], bgr.shape[0])
