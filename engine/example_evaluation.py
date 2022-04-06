# encoding: utf-8
"""
@author:  sherlock
@contact: sherlockliao01@gmail.com
"""
"""
@author:  davide zambrano
@contact: d.zambrano@sportradar.com

"""
from typing import List
import os
import json
import logging

from ignite.engine import Events
from ignite.engine import create_supervised_evaluator
from ignite.metrics import Loss, mIoU, confusion_matrix, MeanAbsoluteError
import torch.nn.functional as F
import numpy as np

import torch
from deepsport_utilities.calib import Point2D, Calib

from modeling.example_camera_model import compute_camera
from utils.intersections import find_intersections


TEST_2D_POINTS = Point2D(
    [
        [1, 1, 1 / 2, 1 / 2, 0, 0],
        [1, 1 / 2, 1, 1 / 2, 1, 1 / 2],
    ]
)


def save_predictions_to_json(
    results_list: List[np.ndarray],
    json_file: str = "predictions.json",
) -> None:
    """Create JSON format results

    Args:
        results_list (List[np.ndarray]): prediction results
        json_file (str, optional): JSON file. Defaults to "predictions.json".
    """
    with open(json_file, "w") as f:
        json.dump(results_list, f, indent=4)


def run_metrics(
    json_file: str = "predictions.json",
) -> None:
    """Compute metrics from JSON

    Args:
        json_file (str, optional): Results saved in JSON file. Defaults to "predictions.json".
    """
    with open(json_file, "r") as f:
        data = f.read()

    default_h = np.eye(3, 4)
    default_h[2, 3] = 1.0

    obj = json.loads(data)

    wid_, hei_ = (obj[0]["width"], obj[0]["height"])
    test_2d_ponts = TEST_2D_POINTS * [[wid_], [hei_]]

    obj = obj[1:]

    accuracy = 0
    mse_ = []

    for dat in obj:
        if dat["predicted_calib"] == default_h:
            continue
        else:
            accuracy += 1
            calib = Calib.from_P(
                np.array(dat["predicted_calib"]).reshape(3, 4),
                width=wid_,
                height=hei_,
            )
            calib_gt = Calib.from_P(
                np.array(dat["gt_calib"]).reshape(3, 4),
                width=wid_,
                height=hei_,
            )
            y_pred = calib.project_2D_to_3D(test_2d_ponts, Z=0)
            y = calib_gt.project_2D_to_3D(test_2d_ponts, Z=0)

            mse_.append(np.sqrt(np.square(np.subtract(y_pred, y)).mean()))
    print(f"Accuracy: {accuracy}, discounted MSE: {np.mean(mse_)} m")


def json_serialisable(array: np.ndarray) -> List[float]:
    """Takes a np array slice and makes it JSON serialisable.

    Args:
        array (np.ndarray): N-dim np.ndarray.

    Returns:
        List[float]: reformatted 1-dim list of floats.
    """
    array_slice = array.reshape(
        -1,
    )
    return list(map(float, array_slice))


class CameraTransform:
    """Callable to output transform."""

    def __init__(self, cfg) -> None:
        self.width, self.height = (
            cfg.INPUT.MULTIPLICATIVE_FACTOR * cfg.INPUT.GENERATED_VIEW_SIZE[0],
            cfg.INPUT.MULTIPLICATIVE_FACTOR * cfg.INPUT.GENERATED_VIEW_SIZE[1],
        )
        self.test_2d_ponts = TEST_2D_POINTS * [[self.width], [self.height]]
        params = {"width": self.width, "height": self.height}
        self.dumpable_list = [params]

    def __call__(self, x, y, y_pred):

        points2d, points3d = find_intersections(
            np.squeeze(y_pred["out"].cpu().numpy().astype(np.float32))
            # np.squeeze(y["target"].cpu().numpy().astype(np.float32))
        )  # here use actual prediction

        calib = compute_camera(points2d, points3d, (self.height, self.width))
        calib_gt = Calib.from_P(
            np.squeeze(y["calib"].cpu().numpy().astype(np.float32)),
            width=self.width,
            height=self.height,
        )
        data = {
            "numper of points2d": len(points2d),
            "predicted_calib": json_serialisable(calib.P),
            "gt_calib": json_serialisable(calib_gt.P),
        }
        self.dumpable_list.append(data)

        y_pred = calib.project_2D_to_3D(self.test_2d_ponts, Z=0)
        y = calib_gt.project_2D_to_3D(self.test_2d_ponts, Z=0)

        return (torch.as_tensor(y_pred), torch.as_tensor(y))


def evaluation(cfg, model, val_loader):
    device = cfg.MODEL.DEVICE

    logger = logging.getLogger("template_model.evaluation")
    logger.info("Start evaluation")
    cm = confusion_matrix.ConfusionMatrix(num_classes=21)
    camera_transform = CameraTransform(cfg)

    evaluator = create_supervised_evaluator(
        model,
        metrics={"mse": MeanAbsoluteError()},
        device=device,
        output_transform=camera_transform,
    )

    # adding handlers using `evaluator.on` decorator API
    @evaluator.on(Events.EPOCH_COMPLETED)
    def print_validation_results(engine):
        metrics = evaluator.state.metrics
        mse = metrics["mse"]
        logger.info(
            "Camera Evaluation Overall Results - MSE: {:.3f}".format(mse)
        )

    evaluator.run(val_loader)

    save_predictions_to_json(camera_transform.dumpable_list)
    run_metrics()
