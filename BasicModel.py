#!/usr/bin/env/python
#lite_basicmode.py
from __future__ import print_function
from typing import List, Any, Sequence
from utils import MLP, ThreadedIterator

import tensorflow as tf


import time
import os
import json
import numpy as np
import pickle
import random
import subprocess
import sys
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe 
from matplotlib.patches import Patch
from time import perf_counter
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable


plt.rcParams.update({
    "font.size": 11,
    "axes.labelsize": 13,
    "axes.titlesize": 13,
    "xtick.labelsize": 11,
    "ytick.labelsize": 11,
    "legend.fontsize": 11,
    "lines.linewidth": 2.2
})
from sklearn.metrics import roc_curve, auc, roc_auc_score, accuracy_score, precision_score, recall_score, f1_score

print(">>> FILE LOADED: BasicModel.py", flush=True)

class TimeMeter:
    def __init__(self):
        self.sums = {}
        self.counts = {}

    def add(self, key, dt, n=1):
        self.sums[key] = self.sums.get(key, 0.0) + dt
        self.counts[key] = self.counts.get(key, 0) + n

    def report(self):
        out = {}
        for k in self.sums:
            c = max(1, self.counts.get(k, 1))
            out[k] = {
                "total_sec": self.sums[k],
                "count": c,
                "avg_sec": self.sums[k] / c
            }
        return out




def smooth_curve(y, window_size=10):
    return np.convolve(y, np.ones(window_size) / window_size, mode='valid')

#  YAHAN PASTE KARO (helper functions)
def save_histories(out_path, train_loss, valid_loss, train_acc, valid_acc):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    data = {
        "train_loss": train_loss,
        "valid_loss": valid_loss,
        "train_acc": train_acc,
        "valid_acc": valid_acc
    }
    with open(out_path, "w") as f:
        json.dump(data, f)

def load_histories(in_path):
    with open(in_path, "r") as f:
        data = json.load(f)
    return data["train_loss"], data["valid_loss"], data["train_acc"], data["valid_acc"]

def save_metrics(out_path, metrics):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2)

def load_metrics(in_path):
    with open(in_path, "r") as f:
        return json.load(f)

def infer_dataset_name(train_file):
    lower = train_file.lower()
    if "reentrancy" in lower:
        return "reentrancy"
    if "timestamp" in lower:
        return "timestamp"
    if "integeroverflow" in lower:
        return "integeroverflow"
    return "run"

def compute_eval_metrics(y_true, y_prob, threshold=0.5):
    y_pred = (y_prob >= threshold).astype(int)
    metrics = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
    }
    if len(np.unique(y_true)) > 1:
        metrics["auc"] = float(roc_auc_score(y_true, y_prob))
    else:
        metrics["auc"] = 0.0
    return metrics


def compute_confusion_matrix_report(y_true, y_prob, threshold=0.5):
    y_true = np.asarray(y_true).reshape(-1).astype(int)
    y_prob = np.asarray(y_prob).reshape(-1)
    y_pred = (y_prob >= threshold).astype(int)

    tn = int(np.sum((y_true == 0) & (y_pred == 0)))
    fp = int(np.sum((y_true == 0) & (y_pred == 1)))
    fn = int(np.sum((y_true == 1) & (y_pred == 0)))
    tp = int(np.sum((y_true == 1) & (y_pred == 1)))

    total = int(y_true.size)
    matrix = [[tn, fp], [fn, tp]]

    row_normalized = []
    for row in matrix:
        denom = float(max(1, sum(row)))
        row_normalized.append([float(x / denom) for x in row])

    return {
        "threshold": float(threshold),
        "labels": ["negative", "positive"],
        "matrix_order": "[[TN, FP], [FN, TP]]",
        "matrix": matrix,
        "row_normalized_matrix": row_normalized,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "tp": tp,
        "total": total,
    }


def plot_confusion_matrix_report(report, out_path, title="Confusion Matrix"):
    matrix = np.asarray(report.get("matrix", [[0, 0], [0, 0]]), dtype=np.int32)
    out_path = str(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    plt.figure(figsize=(4.8, 4.2), dpi=300)
    plt.imshow(matrix, interpolation="nearest", cmap="Blues")
    plt.title(title)
    plt.colorbar(fraction=0.046, pad=0.04)

    tick_marks = np.arange(2)
    plt.xticks(tick_marks, ["Pred 0", "Pred 1"])
    plt.yticks(tick_marks, ["True 0", "True 1"])

    threshold = matrix.max() / 2.0 if matrix.size else 0.0
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            color = "white" if matrix[i, j] > threshold else "black"
            plt.text(j, i, str(int(matrix[i, j])),
                     ha="center", va="center", color=color, fontsize=12)

    plt.ylabel("True label")
    plt.xlabel("Predicted label")
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)

    root, _ = os.path.splitext(out_path)
    plt.savefig(root + ".pdf")
    plt.close()


def select_best_threshold(y_true, y_prob, threshold_candidates=None):
    y_true = np.asarray(y_true).reshape(-1)
    y_prob = np.asarray(y_prob).reshape(-1)

    if y_true.size == 0:
        default_threshold = 0.5
        metrics = {
            "accuracy": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "auc": 0.0,
        }
        metrics["threshold"] = float(default_threshold)
        return float(default_threshold), metrics

    if threshold_candidates is None:
        threshold_candidates = np.linspace(0.05, 0.95, 181)

    best_threshold = 0.5
    best_metrics = compute_eval_metrics(y_true, y_prob, threshold=best_threshold)
    best_score = (
        best_metrics["f1"],
        best_metrics["precision"],
        best_metrics["recall"],
        best_metrics["accuracy"],
    )

    for threshold in threshold_candidates:
        threshold = float(threshold)
        metrics = compute_eval_metrics(y_true, y_prob, threshold=threshold)
        score = (
            metrics["f1"],
            metrics["precision"],
            metrics["recall"],
            metrics["accuracy"],
        )
        if score > best_score:
            best_threshold = threshold
            best_metrics = metrics
            best_score = score

    best_metrics = dict(best_metrics)
    best_metrics["threshold"] = float(best_threshold)
    return float(best_threshold), best_metrics


def build_dataset_summary(
        dataset_name,
        metrics,
        inference_bench,
        total_detection_bench,
        timing_report,
        training_report=None):
    training_report = training_report or {}
    total_detection_sec = float(total_detection_bench.get("total_detection_sec_measured", 0.0))
    pure_inference_sec = float(inference_bench.get("total_infer_sec", 0.0))
    contracts_per_sec = float(total_detection_bench.get("contracts_per_sec", 0.0))

    return {
        "dataset": dataset_name,
        "accuracy": float(metrics.get("accuracy", 0.0)),
        "precision": float(metrics.get("precision", 0.0)),
        "recall": float(metrics.get("recall", 0.0)),
        "f1": float(metrics.get("f1", 0.0)),
        "auc": float(metrics.get("auc", 0.0)),
        "total_detection_time_sec": total_detection_sec,
        "pure_inference_time_sec": pure_inference_sec,
        "detection_throughput_contracts_per_sec": contracts_per_sec,
        "training_total_sec": float(training_report.get("training_total_sec", 0.0)),
        "avg_epoch_sec": float(training_report.get("avg_epoch_sec", 0.0)),
    }


def build_speed_details(dataset_name, inference_bench, total_detection_bench, timing_report, training_report=None):
    training_report = training_report or {}
    preprocess_avg_sec = None
    if isinstance(timing_report, dict):
        preprocess_avg_sec = (
            timing_report.get("total_preprocess_sec", {}).get("avg_sec")
            if isinstance(timing_report.get("total_preprocess_sec"), dict)
            else None
        )

    return {
        "dataset": dataset_name,
        "inference_time_ms_per_graph": float(inference_bench.get("avg_infer_ms_per_graph", 0.0)),
        "inference_speed_graphs_per_sec": float(inference_bench.get("throughput_graphs_per_sec", 0.0)),
        "total_detection_ms_per_contract": float(total_detection_bench.get("avg_total_detection_ms_per_contract", 0.0)),
        "contracts_per_sec": float(total_detection_bench.get("contracts_per_sec", 0.0)),
        "detection_speed_contracts_per_sec": float(total_detection_bench.get("contracts_per_sec", 0.0)),
        "preprocess_avg_sec": float(preprocess_avg_sec) if preprocess_avg_sec is not None else None,
        "total_detection_time_sec": float(total_detection_bench.get("total_detection_sec_measured", 0.0)),
        "pure_inference_time_sec": float(inference_bench.get("total_infer_sec", 0.0)),
        "json_load_sec": float(total_detection_bench.get("json_load_sec", 0.0)),
        "preprocess_sec": float(total_detection_bench.get("preprocess_sec", 0.0)),
        "warmup_infer_sec": float(total_detection_bench.get("warmup_infer_sec", 0.0)),
        "batch_plus_infer_sec": float(total_detection_bench.get("batch_plus_infer_sec", 0.0)),
        "measured_inference_graphs": int(inference_bench.get("measured_graphs", 0)),
        "measured_detection_contracts": int(total_detection_bench.get("measured_contracts", 0)),
        "training_total_sec": float(training_report.get("training_total_sec", 0.0)),
        "avg_epoch_sec": float(training_report.get("avg_epoch_sec", 0.0)),
        "epochs_ran": int(training_report.get("epochs_ran", 0)),
        "epoch_time_sec": training_report.get("epoch_time_sec", []),
    }

def plot_accuracy_loss_curves(re_hist_path, ts_hist_path, out_dir="./result", max_epochs=None):
    """Save clean combined accuracy and loss curves without changing training."""
    if not (os.path.exists(re_hist_path) and os.path.exists(ts_hist_path)):
        print("Skipping accuracy/loss curves: history files missing.")
        return

    re_train_loss, re_valid_loss, re_train_acc, re_valid_acc = load_histories(re_hist_path)
    ts_train_loss, ts_valid_loss, ts_train_acc, ts_valid_acc = load_histories(ts_hist_path)

    min_len = min(
        len(re_train_acc), len(re_valid_acc), len(re_train_loss), len(re_valid_loss),
        len(ts_train_acc), len(ts_valid_acc), len(ts_train_loss), len(ts_valid_loss),
    )
    if max_epochs is not None:
        min_len = min(min_len, int(max_epochs))
    epochs = np.arange(1, min_len + 1)

    re_train_acc = np.asarray(re_train_acc[:min_len], dtype=float)
    re_valid_acc = np.asarray(re_valid_acc[:min_len], dtype=float)
    ts_train_acc = np.asarray(ts_train_acc[:min_len], dtype=float)
    ts_valid_acc = np.asarray(ts_valid_acc[:min_len], dtype=float)
    re_train_loss = np.asarray(re_train_loss[:min_len], dtype=float)
    re_valid_loss = np.asarray(re_valid_loss[:min_len], dtype=float)
    ts_train_loss = np.asarray(ts_train_loss[:min_len], dtype=float)
    ts_valid_loss = np.asarray(ts_valid_loss[:min_len], dtype=float)

    if max(np.nanmax(re_train_acc), np.nanmax(re_valid_acc),
           np.nanmax(ts_train_acc), np.nanmax(ts_valid_acc)) <= 1.5:
        re_train_acc *= 100.0
        re_valid_acc *= 100.0
        ts_train_acc *= 100.0
        ts_valid_acc *= 100.0

    os.makedirs(out_dir, exist_ok=True)
    train_acc_style = dict(color="#2f8f2f", linestyle="-", linewidth=1.35,
                           marker="o", markersize=3.0, markevery=10,
                           markerfacecolor="white", markeredgecolor="#2f8f2f",
                           markeredgewidth=0.8)
    valid_acc_style = dict(color="#e68a00", linestyle="-", linewidth=1.35,
                           marker="s", markersize=3.0, markevery=10,
                           markerfacecolor="white", markeredgecolor="#e68a00",
                           markeredgewidth=0.8)
    train_loss_style = dict(color="#2f6db0", linestyle="-", linewidth=1.35,
                            marker="o", markersize=3.0, markevery=10,
                            markerfacecolor="white", markeredgecolor="#2f6db0",
                            markeredgewidth=0.8)
    valid_loss_style = dict(color="#c62828", linestyle="-", linewidth=1.35,
                            marker="s", markersize=3.0, markevery=10,
                            markerfacecolor="white", markeredgecolor="#c62828",
                            markeredgewidth=0.8)

    def style_axis(ax):
        ax.grid(True, which="major", linestyle=":", linewidth=0.55, alpha=0.55)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(direction="out", length=2.5, width=0.65)
        ax.set_xlim(1, int(epochs[-1]))
        if len(epochs) >= 100:
            ax.set_xticks([1, 20, 40, 60, 80, 100])
        ax.set_xlabel("Epoch")

    fig, ax = plt.subplots(figsize=(6.8, 3.25), dpi=150)
    ax.plot(epochs, re_train_acc, label="Reentrancy Training", **train_acc_style)
    ax.plot(epochs, re_valid_acc, label="Reentrancy Validation", **valid_acc_style)
    ax.plot(epochs, ts_train_acc, label="Timestamp Training", **train_acc_style)
    ax.plot(epochs, ts_valid_acc, label="Timestamp Validation", **valid_acc_style)
    ax.set_title("Accuracy over Epochs", pad=7, fontsize=11)
    ax.set_ylabel("Accuracy (%)")
    style_axis(ax)
    ymin = min(np.nanmin(re_train_acc), np.nanmin(re_valid_acc),
               np.nanmin(ts_train_acc), np.nanmin(ts_valid_acc))
    ymax = max(np.nanmax(re_train_acc), np.nanmax(re_valid_acc),
               np.nanmax(ts_train_acc), np.nanmax(ts_valid_acc))
    margin = max(2.0, 0.08 * (ymax - ymin if ymax > ymin else 10.0))
    ax.set_ylim(max(0, ymin - margin), min(100, ymax + margin))
    ax.legend(frameon=True, framealpha=0.9, ncol=2, handlelength=2.2)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "acc_curve.pdf"))
    fig.savefig(os.path.join(out_dir, "acc_curve.png"), dpi=600)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.8, 3.25), dpi=150)
    ax.plot(epochs, re_train_loss, label="Reentrancy Training", **train_loss_style)
    ax.plot(epochs, re_valid_loss, label="Reentrancy Validation", **valid_loss_style)
    ax.plot(epochs, ts_train_loss, label="Timestamp Training", **train_loss_style)
    ax.plot(epochs, ts_valid_loss, label="Timestamp Validation", **valid_loss_style)
    ax.set_title("Loss over Epochs", pad=7, fontsize=11)
    ax.set_ylabel("Loss")
    style_axis(ax)
    ax.legend(frameon=True, framealpha=0.9, ncol=2, handlelength=2.2)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "loss_curve.pdf"))
    fig.savefig(os.path.join(out_dir, "loss_curve.png"), dpi=600)
    plt.close(fig)



def plot_final_accuracy_bars(re_hist_path, ts_hist_path, out_dir="./result"):
    if not (os.path.exists(re_hist_path) and os.path.exists(ts_hist_path)):
        print("Skipping final accuracy bars: history files missing.")
        return

    re_train_loss, re_valid_loss, re_train_acc, re_valid_acc = load_histories(re_hist_path)
    ts_train_loss, ts_valid_loss, ts_train_acc, ts_valid_acc = load_histories(ts_hist_path)

    re_train_final = float(re_train_acc[-1]) * 100.0
    re_valid_final = float(re_valid_acc[-1]) * 100.0
    ts_train_final = float(ts_train_acc[-1]) * 100.0
    ts_valid_final = float(ts_valid_acc[-1]) * 100.0

    labels = ["Reentrancy", "Timestamp"]
    train_vals = [re_train_final, ts_train_final]
    valid_vals = [re_valid_final, ts_valid_final]

    x = np.arange(len(labels))
    width = 0.12

    os.makedirs(out_dir, exist_ok=True)
    plt.figure(figsize=(8, 5))

    # ONLY TWO COLORS (train=blue, valid=orange)
    train_color = "#1f77b4"
    valid_color = "#ff7f0e"

    # hatches same
    train_hatches = ["///", "xx"]
    valid_hatches = ["\\\\", ".."]

    edge = "black"
    plt.rcParams["hatch.linewidth"] = 0.8

    for i in range(len(labels)):
        plt.bar(x[i] - width/2, train_vals[i], width,
                color=train_color, hatch=train_hatches[i],
                edgecolor=edge, linewidth=0.8,
                label="_nolegend_")   # no hatch in legend

        plt.bar(x[i] + width/2, valid_vals[i], width,
                color=valid_color, hatch=valid_hatches[i],
                edgecolor=edge, linewidth=0.8,
                label="_nolegend_")   # no hatch in legend

    plt.ylabel("Accuracy (%)")
    plt.title("Final Training vs Validation Accuracy")
    plt.xticks(x, labels)

    # LEGEND: plain colors only (NO hatch)
    legend_elements = [
        Patch(facecolor=train_color, edgecolor="black", label="Training"),
        Patch(facecolor=valid_color, edgecolor="black", label="Validation"),
    ]
    plt.legend(handles=legend_elements, loc="lower center", frameon=True)

    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "final_acc_bar.png"), dpi=150)
    plt.savefig(os.path.join(out_dir, "final_acc_bar.pdf"))
    plt.close()


def plot_single_dataset_curves(hist_path, dataset_label, out_dir="./result", max_epochs=30):
    """Save clean individual loss and accuracy curves from raw histories.

    This function changes only figure presentation; training values are read
    from the histories written by the training loop and are not smoothed.
    """
    if not os.path.exists(hist_path):
        print("Skipping %s curves: history file missing." % dataset_label)
        return

    train_loss, valid_loss, train_acc, valid_acc = load_histories(hist_path)
    min_len = min(len(train_loss), len(valid_loss), len(train_acc), len(valid_acc))
    if max_epochs is not None:
        min_len = min(min_len, int(max_epochs))

    epochs = np.arange(1, min_len + 1)
    train_loss = np.asarray(train_loss[:min_len], dtype=float)
    valid_loss = np.asarray(valid_loss[:min_len], dtype=float)
    train_acc = np.asarray(train_acc[:min_len], dtype=float)
    valid_acc = np.asarray(valid_acc[:min_len], dtype=float)

    if max(np.nanmax(train_acc), np.nanmax(valid_acc)) <= 1.5:
        train_acc *= 100.0
        valid_acc *= 100.0

    os.makedirs(out_dir, exist_ok=True)

    loss_train_style = dict(
        color="#2f6db0", linestyle="-", linewidth=1.35,
        marker="o", markersize=3.0, markevery=10,
        markerfacecolor="white", markeredgecolor="#2f6db0", markeredgewidth=0.8,
    )
    loss_valid_style = dict(
        color="#c62828", linestyle="-", linewidth=1.35,
        marker="s", markersize=3.0, markevery=10,
        markerfacecolor="white", markeredgecolor="#c62828", markeredgewidth=0.8,
    )
    acc_train_style = dict(
        color="#2f8f2f", linestyle="-", linewidth=1.35,
        marker="o", markersize=3.0, markevery=10,
        markerfacecolor="white", markeredgecolor="#2f8f2f", markeredgewidth=0.8,
    )
    acc_valid_style = dict(
        color="#e68a00", linestyle="-", linewidth=1.35,
        marker="s", markersize=3.0, markevery=10,
        markerfacecolor="white", markeredgecolor="#e68a00", markeredgewidth=0.8,
    )

    def style_axis(ax):
        ax.grid(True, which="major", linestyle=":", linewidth=0.55, alpha=0.55)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(direction="out", length=2.5, width=0.65)
        ax.set_xlim(1, int(epochs[-1]))
        if len(epochs) >= 100:
            ax.set_xticks([1, 20, 40, 60, 80, 100])
        else:
            count = min(6, len(epochs))
            ax.set_xticks(np.unique(np.linspace(1, int(epochs[-1]), count, dtype=int)))
        ax.set_xlabel("Epoch")

    # Loss: blue training and red validation.
    fig, ax = plt.subplots(figsize=(3.35, 2.55), dpi=150)
    ax.plot(epochs, train_loss, label="Training", **loss_train_style)
    ax.plot(epochs, valid_loss, label="Validation", **loss_valid_style)
    ax.set_title("Loss over Epochs (%s)" % dataset_label, pad=7, fontsize=9.6)
    ax.set_ylabel("Loss")
    style_axis(ax)
    ax.legend(loc="upper right", frameon=True, framealpha=0.9,
              borderpad=0.35, handlelength=2.2)
    fig.subplots_adjust(left=0.16, right=0.985, bottom=0.19, top=0.82)
    fig.savefig(os.path.join(out_dir, "%s_loss_curve.pdf" % dataset_label.lower()))
    fig.savefig(os.path.join(out_dir, "%s_loss_curve.png" % dataset_label.lower()), dpi=600)
    plt.close(fig)

    # Accuracy: green training and orange validation.
    fig, ax = plt.subplots(figsize=(3.35, 2.55), dpi=150)
    ax.plot(epochs, train_acc, label="Training", **acc_train_style)
    ax.plot(epochs, valid_acc, label="Validation", **acc_valid_style)
    ax.set_title("Accuracy over Epochs (%s)" % dataset_label, pad=7, fontsize=9.6)
    ax.set_ylabel("Accuracy (%)")
    style_axis(ax)
    ymin = min(np.nanmin(train_acc), np.nanmin(valid_acc))
    ymax = max(np.nanmax(train_acc), np.nanmax(valid_acc))
    margin = max(2.0, 0.08 * (ymax - ymin if ymax > ymin else 10.0))
    ax.set_ylim(max(0, ymin - margin), min(100, ymax + margin))
    ax.legend(loc="lower right", frameon=True, framealpha=0.9,
              borderpad=0.35, handlelength=2.2)
    fig.subplots_adjust(left=0.16, right=0.985, bottom=0.19, top=0.82)
    fig.savefig(os.path.join(out_dir, "%s_acc_curve.pdf" % dataset_label.lower()))
    fig.savefig(os.path.join(out_dir, "%s_acc_curve.png" % dataset_label.lower()), dpi=600)
    plt.close(fig)



def plot_metrics_comparison(re_metrics_path, ts_metrics_path, out_dir="./result"):
    if not (os.path.exists(re_metrics_path) and os.path.exists(ts_metrics_path)):
        print("Skipping metrics comparison: metrics files missing.")
        return

    re_metrics = load_metrics(re_metrics_path)
    ts_metrics = load_metrics(ts_metrics_path)

    metric_names = ["accuracy", "precision", "recall", "f1", "auc"]
    re_vals = [re_metrics.get(m, 0.0) * 100.0 for m in metric_names]
    ts_vals = [ts_metrics.get(m, 0.0) * 100.0 for m in metric_names]

    x = np.arange(len(metric_names))
    width = 0.15

    os.makedirs(out_dir, exist_ok=True)
    plt.figure(figsize=(7.5, 4.5))

    # ONLY TWO COLORS
    re_color = "#1f77b4"   # blue
    ts_color = "#ff7f0e"   # golden/orange

    # clean edges
    edge = "black"
    plt.rcParams["hatch.linewidth"] = 0.8

    re_hatches = ["///", "xx", "\\\\", "..", "++"]
    ts_hatches = ["\\\\", "oo", "--", "xx", "///"]

    for i in range(len(metric_names)):
        # IMPORTANT: label="_nolegend_" so legend doesn't pick hatched bars
        plt.bar(x[i] - width/2, re_vals[i], width,
                color=re_color, hatch=re_hatches[i],
                edgecolor=edge, linewidth=0.8,
                label="_nolegend_")

        plt.bar(x[i] + width/2, ts_vals[i], width,
                color=ts_color, hatch=ts_hatches[i],
                edgecolor=edge, linewidth=0.8,
                label="_nolegend_")

    plt.ylabel("Score (%)")
    plt.title("Evaluation Metrics Comparison")
    plt.xticks(x, [m.upper() for m in metric_names])

    # LEGEND: only two plain color boxes (NO hatch)
    legend_elements = [
        Patch(facecolor=re_color, edgecolor="black", label="Reentrancy"),
        Patch(facecolor=ts_color, edgecolor="black", label="Timestamp"),
    ]
    plt.legend(handles=legend_elements, loc="upper right", frameon=True)

    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "metrics_comparison.png"), dpi=150)
    plt.savefig(os.path.join(out_dir, "metrics_comparison.pdf"))
    plt.close()


def generate_propagation_round_3d_report():
    """Regenerate the propagation-round figure after a model run.

    The generator consumes the precomputed R1/R2/R3 report JSON files. Missing
    reports are reported and do not affect model training or evaluation.
    """
    generator = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "paper_experiment_tools",
        "propagation_round_3d_creation.py",
    )
    if not os.path.exists(generator):
        print("Skipping propagation-round 3D figure: generator missing.")
        return

    try:
        completed = subprocess.run(
            [sys.executable, generator],
            check=False,
        )
    except Exception as exc:
        print("Skipping propagation-round 3D figure:", exc)
        return

    if completed.returncode != 0:
        print(
            "Propagation-round 3D generator returned code %d; "
            "model results are retained." % completed.returncode
        )
    else:
        print("Propagation-round 3D figure generation completed.")

def plot_all_comparisons(out_dir="./result"):
    re_hist_path = os.path.join(out_dir, "reentrancy_hist.json")
    ts_hist_path = os.path.join(out_dir, "timestamp_hist.json")
    re_metrics_path = os.path.join(out_dir, "reentrancy_metrics.json")
    ts_metrics_path = os.path.join(out_dir, "timestamp_metrics.json")

    plot_accuracy_loss_curves(re_hist_path, ts_hist_path, out_dir=out_dir)
    plot_final_accuracy_bars(re_hist_path, ts_hist_path, out_dir=out_dir)
    plot_metrics_comparison(re_metrics_path, ts_metrics_path, out_dir=out_dir)
    plot_single_dataset_curves(re_hist_path, "Reentrancy", out_dir=out_dir, max_epochs=None)
    plot_single_dataset_curves(ts_hist_path, "Timestamp", out_dir=out_dir, max_epochs=None)

def moving_average(x, w=15):
    x = np.array(x, dtype=float)
    if len(x) < w:
        return x
    return np.convolve(x, np.ones(w)/w, mode="valid")


def plot_clean_curves(hist_path, dataset_name, out_dir="./result",
                      max_epochs=250, smooth_window=15, loss_log=False):
    if not os.path.exists(hist_path):
        print("Missing:", hist_path)
        return

    train_loss, valid_loss, train_acc, valid_acc = load_histories(hist_path)

    n = min(len(train_loss), len(valid_loss), len(train_acc), len(valid_acc), max_epochs)
    train_loss, valid_loss = train_loss[:n], valid_loss[:n]
    train_acc, valid_acc   = train_acc[:n], valid_acc[:n]
    epochs = np.arange(1, n+1)

    os.makedirs(out_dir, exist_ok=True)

    # ---- ACCURACY ----
    plt.figure(figsize=(5.0, 3.8), dpi=300)
    plt.plot(epochs, np.array(train_acc) * 100.0, label="Training",
            linestyle="-", marker="o", linewidth=2.2, markersize=4,
            markevery=max(1,n //8))
    plt.plot(epochs, np.array(valid_acc) * 100.0, label="Validation",
            linestyle="--", marker="s", linewidth=2.2, markersize=4,
            markevery=max(1, n//8))

    plt.xlabel("Epochs", fontsize=13)
    plt.ylabel("Accuracy (\%)", fontsize=13)
    plt.title(f"Accuracy over Epochs ({dataset_name})", fontsize=13)

    plt.xticks(fontsize=11)
    plt.yticks(fontsize=11)

    plt.legend(loc="lower right", frameon=True, fontsize=11)
    plt.grid(True, linestyle="--", alpha=0.4)

    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, f"{dataset_name.lower()}_acc_curve.pdf"))
    plt.savefig(os.path.join(out_dir, f"{dataset_name.lower()}_acc_curve.png"), dpi=300)
    plt.close()


    # ---- LOSS ----
    plt.figure(figsize=(10,6))
    plt.plot(epochs, train_loss, alpha=0.25, linewidth=1, label="Train (raw)")
    plt.plot(epochs, valid_loss, alpha=0.25, linewidth=1, label="Valid (raw)")

    tl_s = moving_average(train_loss, smooth_window)
    vl_s = moving_average(valid_loss, smooth_window)
    ep_s2 = epochs[len(epochs)-len(tl_s):]

    plt.plot(ep_s2, tl_s, linewidth=2.5, label=f"Train (smooth w={smooth_window})")
    plt.plot(ep_s2, vl_s, linewidth=2.5, label=f"Valid (smooth w={smooth_window})")

    if loss_log:
        plt.yscale("log")

    plt.title(f"Loss over Epochs ({dataset_name})")
    plt.xlabel("Epoch")
    plt.ylabel("Loss")
    plt.grid(True, linewidth=0.7, alpha=0.6)
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, f"{dataset_name.lower()}_loss_clean.png"), dpi=200)
    plt.close()

    print("Saved clean curves for", dataset_name)



class DetectModel(object):
    @classmethod
    def default_params(cls):
        return {
            # Defaults used by the paper's final GLT-MGA reproduction.
            'num_epochs': 500,
            'patience': 50,
            'learning_rate': 0.001,
            'clamp_gradient_norm': 0.9,  # [0.8, 1.0]
            'out_layer_dropout_keep_prob': 0.8,  # [0.8, 1.0]

            'hidden_size': 256,  # 256/512/1024/2048
            'use_graph': True,

            'tie_fwd_bkwd': False,  # True or False
            'task_ids': [0],
            'propagation_rounds': 2,
            # Timestamp uses 15; reentrancy is reduced to 10 after task
            # selection in __init__. RQ3 configs may override this value.
            'propagation_substeps': 15,

            'readout_num_heads': 4,
            'readout_attn_hidden': 128,
            'fc_dropout_keep_prob': 0.8,   # 0.7–0.8 try
            'weight_decay': 5e-5,          # L2 regularization
            'fusion_mode': 'local_global_gate',
            'gate_hidden_size': 128,
            'gate_dropout_keep_prob': 0.8,
            'use_initial_feature_residual_mha': True,
            'initial_feature_residual_scale': 1.0,

            # The paper uses equal class weights for both classes.
            'class_weight_negative': 1.0,
            'class_weight_positive': 1.0,

            # The fixed outer training partition is split by contract. The
            # legacy outer "valid" file is reserved for final testing only.
            'internal_validation_ratio': 0.10,
            'internal_split_seed': 9930,
        }

    def __init__(self, args):
        self.args = args
        self.time_meter = TimeMeter()
        self.project_dir = os.path.dirname(os.path.abspath(__file__))
        default_result_dir = os.path.join(self.project_dir, "result")
        log_dir = args.get('--log_dir')
        # Keep all run artifacts together when a log_dir is provided.
        self.result_dir = os.path.abspath(log_dir) if log_dir else default_result_dir
        os.makedirs(self.result_dir, exist_ok=True)

        # Collect argument things:
        data_dir = ''
        if '--data_dir' in args and args['--data_dir'] is not None:
            data_dir = args['--data_dir']
        self.data_dir = data_dir

        # random_seed = None
        random_seed = args.get('--random_seed')
        self.random_seed = int(random_seed) if random_seed is not None else 9930

        threshold = args.get('--thresholds')
        self.threshold = float(threshold) if threshold is not None else 0.45

        self.run_id = "_".join([time.strftime("%Y-%m-%d-%H-%M-%S"), str(os.getpid())])
        self.log_file = os.path.join(self.result_dir, "%s_log.json" % self.run_id)
        self.best_model_file = os.path.join(self.result_dir, "%s_model_best.pickle" % self.run_id)

        task_name = self._normalize_task_name(args.get('--task'))
        if task_name is None:
            raise ValueError("Please pass --task reentrancy or --task timestamp.")

        # Collect parameters. These defaults make the final paper reproduction
        # self-contained; explicit config files still override them for RQ3.
        params = self.default_params()
        task_train, task_valid = self._paper_default_data_paths(task_name)
        params.update({
            'train_file': task_train,
            'valid_file': task_valid,
            'propagation_substeps': 10 if task_name == 'reentrancy' else 15,
        })
        config_file = args.get('--config-file')
        if config_file is not None:
            with open(config_file, 'r') as f:
                params.update(self._resolve_task_config(json.load(f), task_name, config_file))
        config = args.get('--config')
        if config is not None:
            params.update(self._resolve_task_config(json.loads(config), task_name, '--config'))
        # Existing configs call the held-out partition "valid_file". Preserve
        # that key for compatibility while making its scientific role explicit.
        params['test_file'] = params.get('test_file') or params['valid_file']
        self.params = params
        self.task_name = task_name or infer_dataset_name(self.params.get('train_file', ''))
        if self.task_name:
            print("Using task config:", self.task_name)
        print("debug Starting data loading...")
        print("OUTER TRAIN:", os.path.join(self.data_dir, self.params['train_file']))
        print(
            "OUTER TEST (legacy valid_file):",
            os.path.join(self.data_dir, self.params['test_file'])
        )

        print("Run %s starting with following parameters:\n%s" % (self.run_id, json.dumps(self.params)))
        random.seed(self.random_seed)
        np.random.seed(self.random_seed)
        print("Run with current seed %s " % self.random_seed)

        # Load baseline:
        self.max_num_vertices = 0
        self.num_edge_types = 0
        self.annotation_size = 0
        self.num_graph = 1
        self.outer_train_num_graph = 0
        self.train_num_graph = 0
        self.valid_num_graph = 0
        self.test_num_graph = 0

        # Build an internal validation set exclusively from the outer training
        # partition. The outer held-out file is loaded as test data and is not
        # used by the training loop, early stopping, or checkpoint selection.
        outer_train_data, self.outer_train_num_graph = self.load_data(
            params['train_file'],
            is_training_data=True
        )
        (
            self.train_data,
            self.valid_data,
            self.internal_split_report,
        ) = self._split_train_validation_by_contract(outer_train_data)
        self.train_num_graph = len(self.train_data)
        self.valid_num_graph = len(self.valid_data)
        self.test_data, self.test_num_graph = self.load_data(
            params['test_file'],
            is_training_data=False
        )
        self.internal_split_report = self._validate_held_out_test(
            outer_train_data,
            self.test_data,
            self.internal_split_report
        )
        split_report_path = os.path.join(
            self.result_dir,
            "%s_internal_split_report.json" % self.task_name
        )
        save_metrics(split_report_path, self.internal_split_report)
        print("Saved:", split_report_path)
        self._configure_class_weights()

        # Build the actual model.
        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True
        self.graph = tf.Graph()
        self.sess = tf.Session(graph=self.graph, config=config)
        with self.graph.as_default():
            tf.set_random_seed(self.random_seed)
            self.placeholders = {}
            self.weights = {}
            self.ops = {}
            self.make_model()
            self.make_train_step()

            # Restore/initialize variables:
            restore_file = args.get('--restore')
            if restore_file is not None:
                self.restore_model(restore_file)
            else:
                self.initialize_model()
        
    @staticmethod
    def _normalize_task_name(task_name):
        if task_name is None:
            return None
        task_name = str(task_name).strip().lower()
        if task_name not in {'reentrancy', 'timestamp'}:
            raise ValueError("Unsupported --task '%s'. Use 'reentrancy' or 'timestamp'." % task_name)
        return task_name

    @staticmethod
    def _paper_default_data_paths(task_name):
        """Return the outer train and legacy-named held-out test files."""
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        task_dir = os.path.join(repo_dir, "train_data", task_name)
        return (
            os.path.join(task_dir, "train.json"),
            os.path.join(task_dir, "valid.json"),
        )

    @classmethod
    def _resolve_task_config(cls, config_obj, task_name, source_label):
        if not isinstance(config_obj, dict):
            raise ValueError("Config from %s must be a JSON object." % source_label)

        section_keys = {'common', 'reentrancy', 'timestamp'}
        has_task_sections = any(key in config_obj for key in section_keys)
        if not has_task_sections:
            return config_obj

        if task_name is None:
            raise ValueError(
                "Task-aware config detected in %s. Please pass --task reentrancy or --task timestamp."
                % source_label
            )

        scoped = {}
        # Treat top-level non-section keys as shared defaults so existing flat
        # parameters can coexist with task-specific file paths.
        for key, value in config_obj.items():
            if key not in section_keys:
                scoped[key] = value

        common_cfg = config_obj.get('common', {})
        if common_cfg is not None:
            if not isinstance(common_cfg, dict):
                raise ValueError("The 'common' section in %s must be a JSON object." % source_label)
            scoped.update(common_cfg)

        task_cfg = config_obj.get(task_name)
        if task_cfg is None:
            raise ValueError("Missing '%s' section in %s." % (task_name, source_label))
        if not isinstance(task_cfg, dict):
            raise ValueError("The '%s' section in %s must be a JSON object." % (task_name, source_label))
        scoped.update(task_cfg)
        return scoped


    @staticmethod
    def _graph_binary_label(graph):
        labels = []
        for target_val in graph.get('target_values', []):
            if target_val is None:
                continue
            labels.append(int(float(target_val)))
        if not labels:
            raise ValueError("Every graph must have a binary target before splitting.")
        return int(any(label == 1 for label in labels))

    def _split_train_validation_by_contract(self, outer_train_data):
        ratio = float(self.params.get('internal_validation_ratio', 0.10))
        split_seed = int(self.params.get('internal_split_seed', 9930))
        if not 0.0 < ratio < 1.0:
            raise ValueError(
                "internal_validation_ratio must be between 0 and 1, got %s."
                % ratio
            )

        contract_graphs = {}
        contract_labels = {}
        for graph in outer_train_data:
            contract_key = graph.get('contract_key')
            if not contract_key:
                raise ValueError(
                    "Contract-level internal splitting requires 'contract_key' "
                    "in every processed graph."
                )
            contract_graphs.setdefault(contract_key, []).append(graph)
            graph_label = self._graph_binary_label(graph)
            contract_labels[contract_key] = max(
                graph_label,
                contract_labels.get(contract_key, 0)
            )

        rng = random.Random(split_seed)
        valid_contracts = set()
        stratum_report = {}
        for label in (0, 1):
            keys = [
                key for key, contract_label in contract_labels.items()
                if contract_label == label
            ]
            rng.shuffle(keys)
            if len(keys) > 1:
                valid_count = int((len(keys) * ratio) + 0.5)
                valid_count = min(max(1, valid_count), len(keys) - 1)
            else:
                valid_count = 0
            valid_contracts.update(keys[:valid_count])
            stratum_report[str(label)] = {
                "outer_train_contracts": len(keys),
                "internal_train_contracts": len(keys) - valid_count,
                "internal_validation_contracts": valid_count,
            }

        train_data = []
        valid_data = []
        for graph in outer_train_data:
            if graph['contract_key'] in valid_contracts:
                valid_data.append(graph)
            else:
                train_data.append(graph)

        if not train_data or not valid_data:
            raise ValueError(
                "Contract-level split produced an empty train or validation set. "
                "Adjust internal_validation_ratio."
            )

        train_contracts = {graph['contract_key'] for graph in train_data}
        inner_valid_contracts = {graph['contract_key'] for graph in valid_data}
        overlap = sorted(train_contracts.intersection(inner_valid_contracts))
        if overlap:
            raise AssertionError(
                "Contract leakage detected between internal train and validation."
            )

        def sample_label_counts(data):
            counts = {"0": 0, "1": 0}
            for graph in data:
                counts[str(self._graph_binary_label(graph))] += 1
            return counts

        report = {
            "protocol": "outer_train_internal_validation_outer_test",
            "split_unit": "contract_key",
            "internal_validation_ratio": ratio,
            "internal_split_seed": split_seed,
            "outer_train_samples": len(outer_train_data),
            "outer_train_contracts": len(contract_graphs),
            "internal_train_samples": len(train_data),
            "internal_train_contracts": len(train_contracts),
            "internal_validation_samples": len(valid_data),
            "internal_validation_contracts": len(inner_valid_contracts),
            "realized_validation_sample_ratio": (
                float(len(valid_data)) / float(max(1, len(outer_train_data)))
            ),
            "realized_validation_contract_ratio": (
                float(len(inner_valid_contracts)) / float(max(1, len(contract_graphs)))
            ),
            "sample_label_counts": {
                "outer_train": sample_label_counts(outer_train_data),
                "internal_train": sample_label_counts(train_data),
                "internal_validation": sample_label_counts(valid_data),
            },
            "contract_label_strata": stratum_report,
            "train_validation_contract_overlap_count": len(overlap),
        }
        print(
            "Internal contract split: train=%d samples/%d contracts, "
            "validation=%d samples/%d contracts (seed=%d, ratio=%.3f)"
            % (
                len(train_data),
                len(train_contracts),
                len(valid_data),
                len(inner_valid_contracts),
                split_seed,
                ratio,
            )
        )
        return train_data, valid_data, report

    def _validate_held_out_test(self, outer_train_data, test_data, report):
        outer_train_contracts = {
            graph.get('contract_key') for graph in outer_train_data
        }
        test_contracts = {graph.get('contract_key') for graph in test_data}
        if None in outer_train_contracts or None in test_contracts:
            raise ValueError(
                "Contract-level outer split verification requires 'contract_key' "
                "in both outer train and test data."
            )

        overlap = sorted(outer_train_contracts.intersection(test_contracts))
        if overlap:
            raise ValueError(
                "Contract leakage detected between outer train and held-out test: %s"
                % overlap[:10]
            )

        test_label_counts = {"0": 0, "1": 0}
        for graph in test_data:
            test_label_counts[str(self._graph_binary_label(graph))] += 1

        report.update({
            "outer_test_file": self.params.get('test_file'),
            "outer_test_samples": len(test_data),
            "outer_test_contracts": len(test_contracts),
            "outer_test_sample_label_counts": test_label_counts,
            "outer_train_test_contract_overlap_count": len(overlap),
        })
        print(
            "Held-out test verified: %d samples/%d contracts, "
            "outer train-test contract overlap=0"
            % (len(test_data), len(test_contracts))
        )
        return report


    def _configure_class_weights(self) -> None:
        w0 = float(self.params.get('class_weight_negative', 1.0))
        w1 = float(self.params.get('class_weight_positive', 1.0))

        has_configured_negative = 'class_weight_negative' in self.params
        has_configured_positive = 'class_weight_positive' in self.params

        # If the config explicitly provides class weights, respect them exactly.
        # This makes 1.0 / 1.0 a true no-weight baseline instead of silently
        # switching to automatic class balancing.
        if has_configured_negative or has_configured_positive:
            self.params['class_weight_negative'] = w0
            self.params['class_weight_positive'] = w1
            print(
                "Using configured class weights: negative=%.6f positive=%.6f"
                % (w0, w1),
                flush=True,
            )
            return

        neg = 0
        pos = 0
        for graph in self.train_data:
            for target_val in graph.get('target_values', []):
                if target_val is None:
                    continue
                label = int(float(target_val))
                if label == 0:
                    neg += 1
                elif label == 1:
                    pos += 1

        if neg > 0 and pos > 0:
            total = float(neg + pos)
            self.params['class_weight_negative'] = total / (2.0 * float(neg))
            self.params['class_weight_positive'] = total / (2.0 * float(pos))
        else:
            self.params['class_weight_negative'] = 1.0
            self.params['class_weight_positive'] = 1.0

        print(
            "Auto class weights from training data: negative=%.6f positive=%.6f "
            "(neg=%d pos=%d)"
            % (
                float(self.params['class_weight_negative']),
                float(self.params['class_weight_positive']),
                neg,
                pos,
            ),
            flush=True,
        )


    def load_data(self, file_name, is_training_data: bool):
        full_path = os.path.join(self.data_dir, file_name)
        t_total0 = perf_counter()


        print("Loading baseline from %s" % full_path)
        #---json load time measurement..
        t0 = perf_counter()
        with open(full_path, 'r') as f:
            data = json.load(f)
        t1 = perf_counter()
        self.time_meter.add("json_load_sec", (t1 - t0), n=1)


        restrict = self.args.get("--restrict_data")
        if restrict is not None:
            restrict = int(restrict)
            if restrict > 0:
                data = data[:restrict]
        #  ADD THIS BLOCK (RIGHT HERE)
        if len(data) == 0:
            print("WARNING: empty dataset:", full_path)
            return [], 0    

        # Get some common baseline out:
        num_fwd_edge_types = 0

        def iter_sample_streams(sample):
            if "local_graph" in sample or "global_graph" in sample:
                yield sample.get("local_graph", []), sample.get("local_node_features", [])
                yield sample.get("global_graph", []), sample.get("global_node_features", [])
            else:
                yield sample.get("graph", []), sample.get("node_features", [])

        for g in data:
            for edges, node_features in iter_sample_streams(g):
                if edges:
                    mx = max(v for e in edges for v in (e[0], e[2]))
                    self.max_num_vertices = max(self.max_num_vertices, mx + 1)
                    num_fwd_edge_types = max(num_fwd_edge_types, 1 + max(e[1] for e in edges))
                else:
                    # No edges: infer vertex count from node feature count.
                    self.max_num_vertices = max(self.max_num_vertices, len(node_features or []))
        self.num_edge_types = max(self.num_edge_types, num_fwd_edge_types * (1 if self.params['tie_fwd_bkwd'] else 2))
        first_non_empty = None
        for sample in data:
            for _edges, node_features in iter_sample_streams(sample):
                if len(node_features or []) > 0:
                    first_non_empty = node_features[0]
                    break
            if first_non_empty is not None:
                break
        if first_non_empty is None:
            raise ValueError("node_features/local_node_features/global_node_features missing/empty for all samples: " + full_path)
        self.annotation_size = max(self.annotation_size, len(first_non_empty))

        print("DEBUG annotation_size (feature_dim) =", self.annotation_size, flush=True)
        print("DEBUG hidden_size (model dim)      =", self.params['hidden_size'], flush=True)

         # ---- PREPROCESS / SCHEDULE TIME ----

        # ---- PREPROCESS / SCHEDULE TIME ----
        t0 = perf_counter()
        out = self.process_raw_graphs(data, is_training_data)
        t1 = perf_counter()
        self.time_meter.add("process_raw_graphs_sec", (t1 - t0), n=1)
        t_total1 = perf_counter()
        self.time_meter.add("total_preprocess_sec", (t_total1 - t_total0), n=1)

        if isinstance(out, tuple):
            processed, num_graphs = out
        else:
            processed = out
            num_graphs = len(processed) if hasattr(processed, "__len__") else None

        return processed, num_graphs


    

    @staticmethod
    def graph_string_to_array(graph_string: str) -> List[List[int]]:
        return [[int(v) for v in s.split(' ')]
                for s in graph_string.split('\n')]

    def process_raw_graphs(self, raw_data: Sequence[Any], is_training_data: bool) -> Any:
        raise Exception("Models have to implement process_raw_graphs!")

    def gated_local_global_fusion(self, h_local, h_global, scope="local_global_gate"):
        """
        h_local:  [G, H]
        h_global: [G, H]
        return: h_final [G, H]
        """
        with tf.variable_scope(scope):
            global_mask = self.placeholders['global_mask']
            h_global_masked = h_global * global_mask

            gate_input = tf.concat(
                [
                    h_local,
                    h_global_masked,
                    tf.abs(h_local - h_global_masked),
                    h_local * h_global_masked,
                    global_mask
                ],
                axis=-1
            )  # [G, 4H + 1]

            gate_hidden = tf.layers.dense(
                gate_input,
                self.params.get('gate_hidden_size', 128),
                activation=tf.nn.relu,
                name="gate_hidden",
                kernel_regularizer=tf.keras.regularizers.l2(self.params['weight_decay'])
            )

            gate_hidden = tf.layers.dropout(
                gate_hidden,
                rate=1.0 - self.params.get('gate_dropout_keep_prob', 0.8),
                training=tf.less(self.placeholders['out_layer_dropout_keep_prob'], 1.0)
            )

            # scalar gate: [G, 1]
            gate = tf.layers.dense(
                gate_hidden,
                1,
                activation=tf.nn.sigmoid,
                name="gate_scalar",
                kernel_regularizer=tf.keras.regularizers.l2(self.params['weight_decay'])
            )

            h_final = h_local + gate * h_global_masked

            self.ops['fusion_gate'] = gate
            self.ops['h_local'] = h_local
            self.ops['h_global'] = h_global_masked
            self.ops['h_final'] = h_final

            return h_final

    def make_model(self):
        self.placeholders['target_values'] = tf.placeholder(tf.float32, [len(self.params['task_ids']), None],
                                                            name='target_values')
        self.placeholders['target_mask'] = tf.placeholder(tf.float32, [len(self.params['task_ids']), None],
                                                          name='target_mask')
        self.placeholders['num_graphs'] = tf.placeholder(tf.int32, [], name='num_graphs')

        self.placeholders['out_layer_dropout_keep_prob'] = tf.placeholder(tf.float32, [],
                                                                          name='out_layer_dropout_keep_prob')
        self.placeholders['global_mask'] = tf.placeholder(tf.float32, [None, 1], name='global_mask')

        with tf.variable_scope("graph_model"):
            self.prepare_specific_graph_model()

            if self.params.get('fusion_mode') == 'local_global_gate':
                h_local, h_global = self.compute_local_global_graph_embeddings()
                graph_emb_shared = self.gated_local_global_fusion(h_local, h_global)

                # Debug/compatibility ops use the local stream in gated mode.
                self.ops['final_node_representations'] = self.ops['local_final_node_representations']
                self.placeholders['graph_nodes_list'] = self.local_ph['graph_nodes_list']
            else:
                # This does the actual graph work: (message)
                if self.params['use_graph']:
                    self.ops['final_node_representations'] = self.compute_final_node_representations()   #TMP call
                else:
                    self.ops['final_node_representations'] = tf.zeros_like(self.placeholders['process_raw_graphs'])

                graph_emb_shared = self.multihead_attention_readout(
                    self.ops['final_node_representations'],
                    self.placeholders['graph_nodes_list'],
                    self.placeholders['num_graphs']
                )

        # Build once: avoid creating this op repeatedly inside run loop.
        self.ops['final_graph_emb_sum'] = tf.unsorted_segment_sum(
            data=self.ops['final_node_representations'],
            segment_ids=self.placeholders['graph_nodes_list'],
            num_segments=self.placeholders['num_graphs']
        )

        self.ops['losses'] = []
        for (internal_id, task_id) in enumerate(self.params['task_ids']):     
            with tf.variable_scope("out_layer_task%i" % task_id):
                graph_emb = graph_emb_shared
         #       with tf.variable_scope("regression_gate"):
         #           self.weights['regression_gate_task%i' % task_id] = MLP(2 * self.params['hidden_size'], 1, [],
          #                                                                 self.placeholders[
           #                                                                    'out_layer_dropout_keep_prob'])
        #        with tf.variable_scope("regression"):
         #           self.weights['regression_transform_task%i' % task_id] = MLP(self.params['hidden_size'], 1, [],
          #                                                                      self.placeholders[
           #                                                                         'out_layer_dropout_keep_prob'])
                # ---- Simple classifier (FC -> logit)
                with tf.variable_scope("mh_attn_classifier"):      #classifier call
                    graph_emb_drop = tf.layers.dropout(
                        graph_emb,
                        rate=1.0 - self.params['fc_dropout_keep_prob'],
                        training=tf.less(self.placeholders['out_layer_dropout_keep_prob'], 1.0)  

                    )
                    computed_values_2d = tf.layers.dense(
                        graph_emb_drop, 1, activation=None, name="fc_out",
                        kernel_regularizer=tf.keras.regularizers.l2(self.params['weight_decay'])
                    )  # [G,1]
                computed_values = tf.squeeze(computed_values_2d, axis=1)  # [G]
                new_computed_values = tf.nn.sigmoid(computed_values)
                self.ops['logits_task%i' % task_id] = computed_values
                self.ops['prob_task%i' % task_id] = new_computed_values   # sigmoid probs


                # same as before  loss computation
                
                labels = tf.cast(self.placeholders['target_values'][internal_id, :], tf.float32)  # [G]
                mask = tf.cast(self.placeholders['target_mask'][internal_id, :], tf.float32)  # [G]

                per_ex_loss = tf.nn.sigmoid_cross_entropy_with_logits(
                    logits=computed_values,
                    labels=labels
                )  # [G]

                w0 = tf.constant(float(self.params['class_weight_negative']), dtype=tf.float32)
                w1 = tf.constant(float(self.params['class_weight_positive']), dtype=tf.float32)
                class_weights = tf.where(
                    tf.equal(labels, 1.0),
                    tf.fill(tf.shape(labels), w1),
                    tf.fill(tf.shape(labels), w0)
                )

                weighted_mask = class_weights * mask
                per_ex_loss = per_ex_loss * weighted_mask
                new_loss = tf.reduce_sum(per_ex_loss) / (tf.reduce_sum(weighted_mask) + 1e-9)

                #computed_values, sigm_val, initial_re = self.gated_regression(self.ops['final_node_representations'],
                 #                                                             self.weights[
                  #                                                                'regression_gate_task%i' % task_id],
                   #                                                           self.weights[
                    #                                                              'regression_transform_task%i' % task_id])

                def f(x):
                    x = 1 * x
                    x = x.astype(np.float32)
                    return x

               # new_computed_values = tf.nn.sigmoid(computed_values)
               # new_loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=computed_values,
                #                                                                  labels=self.placeholders[
                 #                                                                            'target_values'][
                  #                                                                       internal_id, :]))
                a = tf.cast(new_computed_values >= self.threshold, tf.float32)
                correct_pred = tf.equal(a, self.placeholders['target_values'][internal_id, :])
                self.ops['new_computed_values'] = new_computed_values
                #self.ops['sigm_val'] = sigm_val  # QP:graph feature
                self.ops['sigm_val'] = graph_emb
                #self.ops['initial_re'] = initial_re  # QP:inital nodes
                self.ops['initial_re'] = self.placeholders['initial_node_representation']  # QP:initial nodes

                current_mask = self.placeholders['target_mask'][internal_id, :]  # [G]
                correct_f = tf.cast(correct_pred, tf.float32)
                masked_accuracy = tf.reduce_sum(correct_f * current_mask) / (tf.reduce_sum(current_mask) + 1e-9)
                self.ops['accuracy_task%i' % task_id] = masked_accuracy

                self.ops['sigm_c'] = correct_pred

                mask_f = tf.cast(self.placeholders['target_mask'][internal_id, :], tf.float32)  # [G]
                y = tf.cast(self.placeholders['target_values'][internal_id, :], tf.float32)
                p = tf.cast(a, tf.float32)

                TP = tf.reduce_sum(mask_f * tf.cast(tf.logical_and(tf.equal(p, 1.0), tf.equal(y, 1.0)), tf.float32))
                FP = tf.reduce_sum(mask_f * tf.cast(tf.logical_and(tf.equal(p, 1.0), tf.equal(y, 0.0)), tf.float32))
                FN = tf.reduce_sum(mask_f * tf.cast(tf.logical_and(tf.equal(p, 0.0), tf.equal(y, 1.0)), tf.float32))
                TN = tf.reduce_sum(mask_f * tf.cast(tf.logical_and(tf.equal(p, 0.0), tf.equal(y, 0.0)), tf.float32))
                self.ops['sigm_sum'] = tf.add_n([TP, FN, FP, TN])
                self.ops['sigm_TP'] = TP
                self.ops['sigm_FN'] = FN
                self.ops['sigm_FP'] = FP
                self.ops['sigm_TN'] = TN

                # ---- SAFE metrics (avoid division by zero -> NaN) ----
                den_R = tf.maximum(1.0, TP + FN)
                den_P = tf.maximum(1.0, TP + FP)
                den_FPR = tf.maximum(1.0, TN + FP)
                den_F1 = tf.maximum(1.0, (2.0 * TP) + FP + FN)

                R = tf.cast(tf.divide(TP, den_R), tf.float32)          # Recall
                P = tf.cast(tf.divide(TP, den_P), tf.float32)          # Precision
                FPR = tf.cast(tf.divide(FP, den_FPR), tf.float32)      # False Positive Rate
                F1 = tf.cast(tf.divide(2.0 * TP, den_F1), tf.float32)  # F1
                self.ops['sigm_Recall'] = R
                self.ops['sigm_Precision'] = P
                self.ops['sigm_F1'] = F1
                self.ops['sigm_FPR'] = FPR
                self.ops['losses'].append(new_loss)
        self.ops['loss'] = tf.reduce_sum(self.ops['losses'])
        reg_losses = tf.get_collection(tf.GraphKeys.REGULARIZATION_LOSSES)
        if reg_losses:
            self.ops['loss'] = self.ops['loss'] + tf.add_n(reg_losses)


    def make_train_step(self):
        trainable_vars = self.sess.graph.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES)
        if self.args.get('--freeze-graph-model'):
            graph_vars = set(self.sess.graph.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope="graph_model"))
            filtered_vars = []
            for var in trainable_vars:
                if var not in graph_vars:
                    filtered_vars.append(var)
                else:
                    print("Freezing weights of variable %s." % var.name)
            trainable_vars = filtered_vars
        optimizer = tf.train.AdamOptimizer(self.params['learning_rate'])
        grads_and_vars = optimizer.compute_gradients(self.ops['loss'], var_list=trainable_vars)
        clipped_grads = []
        for grad, var in grads_and_vars:
            if grad is not None:
                clipped_grads.append((tf.clip_by_norm(grad, self.params['clamp_gradient_norm']), var))
            else:
                clipped_grads.append((grad, var))
        self.ops['train_step'] = optimizer.apply_gradients(clipped_grads)
        # Initialize newly-introduced variables:
        self.sess.run(tf.local_variables_initializer())

    def gated_regression(self, last_h, regression_gate, regression_transform):
        raise Exception("Models have to implement gated_regression!")

    def prepare_specific_graph_model(self) -> None:
        raise Exception("Models have to implement prepare_specific_graph_model!")

    def compute_final_node_representations(self) -> tf.Tensor:
        raise Exception("Models have to implement compute_final_node_representations!")

    def compute_local_global_graph_embeddings(self):
        raise Exception("Models have to implement compute_local_global_graph_embeddings!")

    def make_minibatch_iterator(self, data: Any, is_training: bool):

        raise Exception("Models have to implement make_minibatch_iterator!")

    def run_epoch(self, epoch_name: str, data, epoch, is_training: bool, log_every=50, quiet=False):
        

        loss = 0
        accuracies = []
        start_time = time.time()
        processed_graphs = 0
        accuracy_ops = [self.ops['accuracy_task%i' % task_id] for task_id in self.params['task_ids']]
        batch_iterator = ThreadedIterator(self.make_minibatch_iterator(data, is_training), max_queue_size=5)
        for step, batch_data in enumerate(batch_iterator):
            num_graphs = batch_data[self.placeholders['num_graphs']]
            processed_graphs += num_graphs

            # dropout
            if is_training:
                batch_data[self.placeholders['out_layer_dropout_keep_prob']] = self.params['out_layer_dropout_keep_prob']
            else:
                batch_data[self.placeholders['out_layer_dropout_keep_prob']] = 1.0

            # --- ONE forward pass: fetch everything together ---
            fetch_list = [
                self.ops['loss'],
                accuracy_ops,

                # metrics/debug
                self.ops['sigm_c'], self.ops['sigm_sum'],
                self.ops['sigm_TP'], self.ops['sigm_FN'], self.ops['sigm_FP'], self.ops['sigm_TN'],
                self.ops['sigm_Recall'], self.ops['sigm_Precision'], self.ops['sigm_F1'], self.ops['sigm_FPR'],
            ]

            # training step in same run
            if is_training:
                fetch_list.append(self.ops['train_step'])

            #  single sess.run
            result = self.sess.run(fetch_list, feed_dict=batch_data)

            # unpack
            if is_training:
                (batch_loss, batch_accuracies,
                val_1, val_6,
                val_2, val_3, val_4, val_5,
                val_R, val_P, val_F1, val_FPR,
                _) = result
            else:
                (batch_loss, batch_accuracies,
                val_1, val_6,
                val_2, val_3, val_4, val_5,
                val_R, val_P, val_F1, val_FPR) = result

            # OPTIONAL: feature dump on a single batch only.
            if epoch == 150 and step == 0:
                var_fn, var_final_node = self.sess.run(
                    [self.ops['sigm_val'], self.ops['final_graph_emb_sum']],
                    feed_dict=batch_data
                )
                os.makedirs("./features/timestamp", exist_ok=True)

                if is_training:
                    np.savetxt("./features/timestamp/timestamp_train_feature.txt", var_final_node, fmt="%.6f")
                else:
                    np.savetxt("./features/timestamp/timestamp_valid_feature.txt", var_final_node, delimiter=", ", fmt="%.6f")

                print("feature dump shapes: sigm_val={} final_graph_emb_sum={}".format(
                    np.shape(var_fn), np.shape(var_final_node)))

            # accumulate
            loss += batch_loss * num_graphs
            accuracies.append(np.array(batch_accuracies) * num_graphs)

            # prints (same as before)
            if (not quiet) and is_training and (log_every > 0) and (step % log_every == 0):
                print(f"[{epoch_name}] step={step} graphs={num_graphs} "
                    f"loss_avg={loss/processed_graphs:.4f} "
                    f"TP={val_2} FP={val_4} FN={val_3} TN={val_5} "
                    f"P={val_P:.3f} R={val_R:.3f} F1={val_F1:.3f} FPR={val_FPR:.3f}")


        if processed_graphs == 0:
            return 0.0, np.zeros(len(self.params['task_ids'])), np.zeros(len(self.params['task_ids'])), 0.0
        accuracies = np.sum(accuracies, axis=0) / processed_graphs
        loss = loss / processed_graphs
        error_ratios = accuracies
        instance_per_sec = processed_graphs / (time.time() - start_time)
        return loss, accuracies, error_ratios, instance_per_sec

    def collect_probs_and_labels(self, data):
        """
        Returns:
          y_true: [N]
          y_prob: [N]  (sigmoid probabilities)
        """
        y_true_all = []
        y_prob_all = []

        task_internal_id = 0  # since task_ids=[0]
        task_id = self.params['task_ids'][0]
        prob_op = self.ops['new_computed_values']

        batch_iterator = ThreadedIterator(
            self.make_minibatch_iterator(data, is_training=False),
            max_queue_size=5
        )

        for batch_data in batch_iterator:
            batch_data[self.placeholders['out_layer_dropout_keep_prob']] = 1.0

            # sigmoid probabilities
            probs = self.sess.run(prob_op, feed_dict=batch_data)
            probs = np.array(probs).reshape(-1)

            # true labels
            y_batch = batch_data[self.placeholders['target_values']][task_internal_id]
            y_batch = np.array(y_batch).reshape(-1)
            mask_batch = batch_data[self.placeholders['target_mask']][task_internal_id]
            mask_batch = np.array(mask_batch).reshape(-1).astype(bool)

            probs = probs[mask_batch]
            y_batch = y_batch[mask_batch]

            # Convert y_batch to integers (0 and 1)
            y_batch = y_batch.astype(int)
            y_prob_all.extend(probs.tolist())
            y_true_all.extend(y_batch.tolist())

        return np.array(y_true_all), np.array(y_prob_all)

    def collect_gate_report(self, data, split_name="valid"):
        """
        Collect gate statistics for gated local-global fusion.

        Reports:
          - average gate for all samples
          - average gate for positive samples
          - average gate for negative samples
          - average gate for global_mask=1 samples
          - average gate for global_mask=0 samples
        """
        if 'fusion_gate' not in self.ops:
            return {
                "enabled": False,
                "reason": "fusion_gate op not found. This run may not be using local_global_gate fusion."
            }

        gates_all = []
        labels_all = []
        probs_all = []
        global_masks_all = []

        task_internal_id = 0

        batch_iterator = ThreadedIterator(
            self.make_minibatch_iterator(data, is_training=False),
            max_queue_size=5
        )

        for batch_data in batch_iterator:
            batch_data[self.placeholders['out_layer_dropout_keep_prob']] = 1.0

            gate_val, probs = self.sess.run(
                [self.ops['fusion_gate'], self.ops['new_computed_values']],
                feed_dict=batch_data
            )

            gate_val = np.asarray(gate_val).reshape(-1)
            probs = np.asarray(probs).reshape(-1)

            y_batch = np.asarray(
                batch_data[self.placeholders['target_values']][task_internal_id]
            ).reshape(-1).astype(int)

            mask_batch = np.asarray(
                batch_data[self.placeholders['target_mask']][task_internal_id]
            ).reshape(-1).astype(bool)

            global_mask_batch = np.asarray(
                batch_data[self.placeholders['global_mask']]
            ).reshape(-1)

            gate_val = gate_val[mask_batch]
            probs = probs[mask_batch]
            y_batch = y_batch[mask_batch]
            global_mask_batch = global_mask_batch[mask_batch]

            gates_all.extend(gate_val.tolist())
            probs_all.extend(probs.tolist())
            labels_all.extend(y_batch.tolist())
            global_masks_all.extend(global_mask_batch.tolist())

        gates = np.asarray(gates_all, dtype=np.float32)
        labels = np.asarray(labels_all, dtype=np.int32)
        probs = np.asarray(probs_all, dtype=np.float32)
        global_masks = np.asarray(global_masks_all, dtype=np.float32)

        def safe_mean(x):
            x = np.asarray(x, dtype=np.float32)
            if x.size == 0:
                return None
            return float(np.mean(x))

        def safe_std(x):
            x = np.asarray(x, dtype=np.float32)
            if x.size == 0:
                return None
            return float(np.std(x))

        def safe_percentiles(x):
            x = np.asarray(x, dtype=np.float32)
            if x.size == 0:
                return None
            return {
                "p10": float(np.percentile(x, 10)),
                "p25": float(np.percentile(x, 25)),
                "p50": float(np.percentile(x, 50)),
                "p75": float(np.percentile(x, 75)),
                "p90": float(np.percentile(x, 90)),
            }

        pos_mask = labels == 1
        neg_mask = labels == 0
        gm1_mask = global_masks >= 0.5
        gm0_mask = global_masks < 0.5

        avg_gate_positive = safe_mean(gates[pos_mask])
        avg_gate_negative = safe_mean(gates[neg_mask])

        report = {
            "enabled": True,
            "split": split_name,
            "num_samples": int(gates.size),

            "avg_gate_all": safe_mean(gates),
            "std_gate_all": safe_std(gates),
            "gate_percentiles": safe_percentiles(gates),

            "positive_count": int(np.sum(pos_mask)),
            "negative_count": int(np.sum(neg_mask)),
            "avg_gate_positive": avg_gate_positive,
            "avg_gate_negative": avg_gate_negative,
            "gate_positive_minus_negative": (
                None if avg_gate_positive is None or avg_gate_negative is None
                else float(avg_gate_positive - avg_gate_negative)
            ),

            "global_mask_1_count": int(np.sum(gm1_mask)),
            "global_mask_0_count": int(np.sum(gm0_mask)),
            "avg_gate_global_mask_1": safe_mean(gates[gm1_mask]),
            "avg_gate_global_mask_0": safe_mean(gates[gm0_mask]),

            "high_gate_ratio_ge_0_70": float(np.mean(gates >= 0.70)) if gates.size > 0 else None,
            "mid_gate_ratio_0_30_to_0_70": float(np.mean((gates >= 0.30) & (gates < 0.70))) if gates.size > 0 else None,
            "low_gate_ratio_lt_0_30": float(np.mean(gates < 0.30)) if gates.size > 0 else None,

            "avg_prob_positive_label": safe_mean(probs[pos_mask]),
            "avg_prob_negative_label": safe_mean(probs[neg_mask]),
        }

        return report
    
    
    def benchmark_inference(self, data, warmup_batches=5, measure_batches=50):
        prob_op = self.ops['new_computed_values']  # sigmoid probs
        batch_iterator = ThreadedIterator(self.make_minibatch_iterator(data, is_training=False), max_queue_size=5)

        # warmup
        w = 0
        for batch_data in batch_iterator:
            batch_data[self.placeholders['out_layer_dropout_keep_prob']] = 1.0
            _ = self.sess.run(prob_op, feed_dict=batch_data)
            w += 1
            if w >= warmup_batches:
                break

        # measure
        total_sec = 0.0
        total_graphs = 0
        m = 0
        batch_iterator = ThreadedIterator(self.make_minibatch_iterator(data, is_training=False), max_queue_size=5)

        for batch_data in batch_iterator:
            batch_data[self.placeholders['out_layer_dropout_keep_prob']] = 1.0
            num_graphs = int(batch_data[self.placeholders['num_graphs']])

            t0 = perf_counter()
            _ = self.sess.run(prob_op, feed_dict=batch_data)
            t1 = perf_counter()

            total_sec += (t1 - t0)
            total_graphs += num_graphs
            m += 1
            if m >= measure_batches:
                break

        avg_ms_per_graph = (total_sec / max(1, total_graphs)) * 1000.0
        graphs_per_sec = total_graphs / max(1e-9, total_sec)

        return {
            "measured_batches": m,
            "measured_graphs": total_graphs,
            "total_infer_sec": total_sec,
            "avg_infer_ms_per_graph": avg_ms_per_graph,
            "throughput_graphs_per_sec": graphs_per_sec
        }

    def benchmark_total_detection_from_file(self, file_name, warmup_batches=3, measure_batches=None):
        """
        End-to-end detection time:
        JSON load + process_raw_graphs (schedule) + batching + forward pass.
        Returns per-contract ms too.
        """
        full_path = os.path.join(self.data_dir, file_name)

        # ---- 1) LOAD + PREPROCESS ----
        t0 = perf_counter()
        with open(full_path, 'r') as f:
            raw = json.load(f)
        t1 = perf_counter()

        out = self.process_raw_graphs(raw, is_training_data=False)

        if isinstance(out, tuple):
            processed, num_graphs = out
        else:
            processed = out
            num_graphs = len(processed) if hasattr(processed, "__len__") else None

  # TMGA returns (processed_graphs, count)
        t2 = perf_counter()

       # ---- 2) WARMUP (batching + forward) ----
        prob_op = self.ops['new_computed_values']
        warmup_sec = 0.0

        it = iter(self.make_minibatch_iterator(processed, is_training=False))
        w = 0
        while w < warmup_batches:
            try:
                batch_data = next(it)   # batching included
            except StopIteration:
                break
            batch_data[self.placeholders['out_layer_dropout_keep_prob']] = 1.0

            t_w0 = perf_counter()                               
            _ = self.sess.run(prob_op, feed_dict=batch_data)
            t_w1 = perf_counter()                              
            warmup_sec += (t_w1 - t_w0)                         

            w += 1



        # ---- 3) MEASURE E2E BATCH LOOP (batching+sess.run) ----
        # Note: iterator itself does batching work (numpy concat/pad) + then sess.run forward
        total_e2e_sec = 0.0
        total_graphs_seen = 0
        m = 0

        it = iter(self.make_minibatch_iterator(processed, is_training=False))

        while True:
            if (measure_batches is not None) and (m >= measure_batches):
                break

            t_batch0 = perf_counter()
            try:
                batch_data = next(it)   # batching included
            except StopIteration:
                break

            batch_data[self.placeholders['out_layer_dropout_keep_prob']] = 1.0
            num_g = int(batch_data[self.placeholders['num_graphs']])

            _ = self.sess.run(prob_op, feed_dict=batch_data)
            t_batch1 = perf_counter()

            total_e2e_sec += (t_batch1 - t_batch0)
            total_graphs_seen += num_g
            m += 1



        # Times
        # Times
        json_sec = (t1 - t0)
        preprocess_sec = (t2 - t1)
        total_sec = (t2 - t0) + total_e2e_sec

        # Per-contract (STRICT: only contracts actually inferred)
        den = max(1, total_graphs_seen)
        per_contract_ms = (total_sec / den) * 1000.0

        contracts_in_file = int(num_graphs) if num_graphs is not None else int(total_graphs_seen)

        return {
            "file": file_name,
            "contracts_in_file": contracts_in_file,
            "measured_batches": int(m),
            "measured_contracts": int(total_graphs_seen),
            "json_load_sec": float(json_sec),
            "preprocess_sec": float(preprocess_sec),
            "warmup_infer_sec": float(warmup_sec),
            "batch_plus_infer_sec": float(total_e2e_sec),
            "total_detection_sec_measured": float(total_sec),
            "avg_total_detection_ms_per_contract": float(per_contract_ms),
            "contracts_per_sec": float(total_graphs_seen / max(1e-9, total_sec))
        }




    def train(self):
        train_loss_hist, valid_loss_hist = [], []
        train_acc_hist, valid_acc_hist = [], []
        epoch_time_hist = []
        val_acc1 = []
        log_to_save = []
        total_time_start = time.time()
        best_val_loss = float("inf")
        best_val_epoch = 0
        with self.graph.as_default():
            if self.args.get('--restore') is not None:
                _, valid_accs, _, _ = self.run_epoch("Resumed (validation)", self.valid_data, 0, False)
                best_val_acc = np.sum(valid_accs)
                best_val_acc_epoch = 0
                print("\r\x1b[KResumed operation, initial cum. val. acc: %.5f" % best_val_acc)
            else:
                (best_val_acc, best_val_acc_epoch) = (0.0, 0)
            for epoch in tqdm(range(1, self.params['num_epochs'] + 1), desc="Training epochs", unit="epoch"):
                print("== Epoch %i" % epoch)
                train_start = time.time()
                self.num_graph = self.train_num_graph
                train_loss, train_accs, train_errs, train_speed = self.run_epoch("epoch %i (training)" % epoch, self.train_data, epoch, True,log_every=50, quiet=False)
                accs_str = " ".join(["%i:%.5f" % (id, acc) for (id, acc) in zip(self.params['task_ids'], train_accs)])
                errs_str = " ".join(["%i:%.5f" % (id, err) for (id, err) in zip(self.params['task_ids'], train_errs)])
                print("\r\x1b[K Train: loss: %.5f | acc: %s | error_ratio: %s | instances/sec: %.2f" % (train_loss,
                                                                                                        accs_str,
                                                                                                        errs_str,
                                                                                                        train_speed))
                train_loss_hist.append(float(train_loss))
                train_acc_hist.append(float(np.sum(train_accs)))  # single task => same

                epoch_time_train = time.time() - train_start
                print(epoch_time_train)

                valid_start = time.time()
                self.num_graph = self.valid_num_graph
                valid_loss, valid_accs, valid_errs, valid_speed = self.run_epoch("epoch %i (validation)" % epoch, self.valid_data, epoch, False,log_every=0, quiet=True)
                accs_str = " ".join(["%i:%.5f" % (id, acc) for (id, acc) in zip(self.params['task_ids'], valid_accs)])
                errs_str = " ".join(["%i:%.5f" % (id, err) for (id, err) in zip(self.params['task_ids'], valid_errs)])
                print("\r\x1b[K Valid: loss: %.5f | acc: %s | error_ratio: %s | instances/sec: %.2f" % (valid_loss,
                                                                                                        accs_str,
                                                                                                        errs_str,
                                                                                                        valid_speed))
                valid_loss_hist.append(float(valid_loss))
                valid_acc_hist.append(float(np.sum(valid_accs)))
                if valid_loss < best_val_loss:
                    print("Validation loss improved from %.5f to %.5f. Saving model."
                         % (best_val_loss, valid_loss))
                    best_val_loss = valid_loss
                    best_val_epoch = epoch
                    self.save_model(self.best_model_file)
                elif epoch - best_val_epoch >= self.params['patience']:
                    print("Early stopping! Validation loss did not improve for %d epochs."
                          % self.params['patience'])
                    print("  Best epoch was:", best_val_epoch)
                    break

                epoch_time_valid = time.time() - valid_start
                print(epoch_time_valid)
                val_acc1.append(valid_accs)

                epoch_time_total = time.time() - total_time_start
                print(epoch_time_total)
                epoch_time_hist.append(float(epoch_time_total - (log_to_save[-1]['time'] if log_to_save else 0.0)))
                log_entry = {
                    'epoch': epoch,
                    'time': epoch_time_total,
                    'train_results': (train_loss, train_accs.tolist(), train_errs.tolist(), train_speed),
                    'valid_results': (valid_loss, valid_accs.tolist(), valid_errs.tolist(), valid_speed),
                }
                log_to_save.append(log_entry)

            training_total_sec = time.time() - total_time_start
            epochs_ran = len(train_loss_hist)
            training_report = {
                "training_total_sec": float(training_total_sec),
                "avg_epoch_sec": float(training_total_sec / max(1, epochs_ran)),
                "epochs_ran": int(epochs_ran),
                "epoch_time_sec": epoch_time_hist,
            }

            dataset_name = infer_dataset_name(self.params['train_file'])
            if dataset_name == "run":
                dataset_name = infer_dataset_name(self.params['test_file'])
            histories_path = os.path.join(self.result_dir, "%s_hist.json" % dataset_name)
            save_histories(
                histories_path,
                train_loss_hist,
                valid_loss_hist,
                train_acc_hist,
                valid_acc_hist
            )
            print("Saved:", histories_path)

            if os.path.exists(self.best_model_file):
                print("Restoring best checkpoint before final evaluation:", self.best_model_file)
                self.restore_model(self.best_model_file)
            else:
                print("WARNING: best checkpoint file not found, evaluating current in-memory weights.")

            internal_y_true, internal_y_prob = self.collect_probs_and_labels(
                self.valid_data
            )
            internal_validation_metrics = compute_eval_metrics(
                internal_y_true,
                internal_y_prob,
                threshold=float(self.threshold)
            )
            internal_validation_metrics.update({
                "evaluation_split": "internal_validation",
                "configured_threshold": float(self.threshold),
                "best_val_loss": (
                    float(best_val_loss) if np.isfinite(best_val_loss) else None
                ),
                "best_val_epoch": int(best_val_epoch),
            })
            internal_validation_metrics_path = os.path.join(
                self.result_dir,
                "%s_internal_validation_metrics.json" % dataset_name
            )
            save_metrics(
                internal_validation_metrics_path,
                internal_validation_metrics
            )
            print("Saved:", internal_validation_metrics_path)

            print(
                "Evaluating the restored checkpoint once on the held-out outer test set."
            )
            y_true, y_prob = self.collect_probs_and_labels(self.test_data)
            configured_threshold = float(self.threshold)
            configured_metrics = compute_eval_metrics(y_true, y_prob, threshold=configured_threshold)

            self.threshold = configured_threshold
            metrics = dict(configured_metrics)
            threshold_source = "configured_fixed"
            print(
                "Using fixed configured threshold %.3f "
                "(auto threshold sweep disabled)." % (
                    configured_threshold,
                )
            )

            metrics["configured_threshold"] = configured_threshold
            metrics["selected_threshold"] = float(self.threshold)
            metrics["threshold_source"] = threshold_source
            metrics["configured_metrics"] = configured_metrics
            metrics["best_validation_threshold_metrics"] = None
            metrics["best_val_loss"] = (
                float(best_val_loss) if np.isfinite(best_val_loss) else None
            )
            metrics["best_val_epoch"] = int(best_val_epoch)
            metrics["best_checkpoint_file"] = (
                self.best_model_file if os.path.exists(self.best_model_file) else None
            )
            metrics["evaluation_split"] = "held_out_test"
            metrics["internal_validation_metrics"] = internal_validation_metrics

            confusion_report = compute_confusion_matrix_report(
                y_true,
                y_prob,
                threshold=configured_threshold
            )
            metrics["confusion_matrix"] = confusion_report
            metrics["experiment_config"] = {
                "task": dataset_name,
                "random_seed": self.random_seed,
                "configured_threshold": configured_threshold,
                "num_epochs": self.params.get("num_epochs"),
                "patience": self.params.get("patience"),
                "learning_rate": self.params.get("learning_rate"),
                "hidden_size": self.params.get("hidden_size"),
                "propagation_rounds": self.params.get("propagation_rounds"),
                "propagation_substeps": self.params.get("propagation_substeps"),
                "readout_num_heads": self.params.get("readout_num_heads"),
                "readout_attn_hidden": self.params.get("readout_attn_hidden"),
                "fusion_mode": self.params.get("fusion_mode"),
                "gate_hidden_size": self.params.get("gate_hidden_size"),
                "outer_train_file": self.params.get("train_file"),
                "test_file": self.params.get("test_file"),
                "legacy_valid_file": self.params.get("valid_file"),
                "internal_validation_ratio": self.params.get("internal_validation_ratio"),
                "internal_split_seed": self.params.get("internal_split_seed"),
                "outer_train_samples": self.outer_train_num_graph,
                "train_samples": self.train_num_graph,
                "valid_samples": self.valid_num_graph,
                "test_samples": self.test_num_graph,
                "internal_train_samples": self.train_num_graph,
                "internal_validation_samples": self.valid_num_graph,
            }

            confusion_path = os.path.join(
                self.result_dir,
                "confusion_matrix_%s.json" % dataset_name
            )
            save_metrics(confusion_path, confusion_report)
            print("Saved:", confusion_path)

            confusion_png_path = os.path.join(
                self.result_dir,
                "confusion_matrix_%s.png" % dataset_name
            )
            plot_confusion_matrix_report(
                confusion_report,
                confusion_png_path,
                title="Confusion Matrix (%s)" % dataset_name.capitalize()
            )
            print("Saved:", confusion_png_path)
            print("Saved:", os.path.splitext(confusion_png_path)[0] + ".pdf")

            metrics_path = os.path.join(self.result_dir, "%s_metrics.json" % dataset_name)
            save_metrics(metrics_path, metrics)
            print("Saved:", metrics_path)

            # ===== GATE INTERPRETABILITY REPORT =====
            if 'fusion_gate' in self.ops:
                print("Collecting gate interpretability report...")

                gate_train_report = self.collect_gate_report(
                    self.train_data,
                    split_name="train"
                )

                gate_valid_report = self.collect_gate_report(
                    self.valid_data,
                    split_name="internal_validation"
                )

                gate_test_report = self.collect_gate_report(
                    self.test_data,
                    split_name="test"
                )

                gate_report = {
                    "dataset": dataset_name,
                    "fusion_mode": self.params.get("fusion_mode"),
                    "gate_hidden_size": self.params.get("gate_hidden_size"),
                    "gate_dropout_keep_prob": self.params.get("gate_dropout_keep_prob"),
                    "train": gate_train_report,
                    "internal_validation": gate_valid_report,
                    "test": gate_test_report,
                }

                gate_report_path = os.path.join(
                    self.result_dir,
                    "%s_gate_report.json" % dataset_name
                )

                save_metrics(gate_report_path, gate_report)
                print("Saved:", gate_report_path)
            else:
                print("Skipping gate report: fusion_gate op not found.")

            plot_all_comparisons(out_dir=self.result_dir)
            generate_propagation_round_3d_report()


            bench = self.benchmark_inference(self.test_data, warmup_batches=5, measure_batches=50)
            print("Inference benchmark:", bench)
            inference_path_dataset = os.path.join(self.result_dir, f"inference_bench_{dataset_name}.json")
            save_metrics(inference_path_dataset, bench)
            print(f"Saved: {inference_path_dataset}")


            # ===== TIMING REPORT =====
            timing_report = self.time_meter.report()

            print("\n===== TIMING REPORT =====")
            print(json.dumps(timing_report, indent=2))

            # save to file
            timing_path_dataset = os.path.join(self.result_dir, f"timing_report_{dataset_name}.json")
            save_metrics(timing_path_dataset, timing_report)
            print("Timing report saved to:", timing_path_dataset)

            training_path_dataset = os.path.join(self.result_dir, f"training_time_{dataset_name}.json")
            save_metrics(training_path_dataset, training_report)
            print("Training time report saved to:", training_path_dataset)

            det = self.benchmark_total_detection_from_file(
                self.params['test_file'],
                warmup_batches=3,
                measure_batches=None
            )

            print("TOTAL detection benchmark:", det)
            save_metrics(
                 os.path.join(self.result_dir, f"total_detection_bench_{dataset_name}.json"),
                 det
            )
            print(f"Saved: {os.path.join(self.result_dir, f'total_detection_bench_{dataset_name}.json')}")

            speed_details = build_speed_details(
                dataset_name=dataset_name,
                inference_bench=bench,
                total_detection_bench=det,
                timing_report=timing_report,
                training_report=training_report
            )
            speed_details_path = os.path.join(self.result_dir, f"speed_details_{dataset_name}.json")
            save_metrics(speed_details_path, speed_details)
            print(f"Saved: {speed_details_path}")

            summary = build_dataset_summary(
                dataset_name=dataset_name,
                metrics=metrics,
                inference_bench=bench,
                total_detection_bench=det,
                timing_report=timing_report,
                training_report=training_report
            )
            summary["experiment_config"] = metrics["experiment_config"]
            summary_path = os.path.join(self.result_dir, f"summary_{dataset_name}.json")
            save_metrics(summary_path, summary)
            print(f"Saved: {summary_path}")




    def save_model(self, path: str) -> None:
        weights_to_save = {}
        for variable in self.sess.graph.get_collection(tf.GraphKeys.GLOBAL_VARIABLES):
            assert variable.name not in weights_to_save
            weights_to_save[variable.name] = self.sess.run(variable)

        data_to_save = {
            "params": self.params,
            "weights": weights_to_save
        }

        with open(path, 'wb') as out_file:
            pickle.dump(data_to_save, out_file, pickle.HIGHEST_PROTOCOL)

    def initialize_model(self) -> None:
        init_op = tf.group(tf.global_variables_initializer(),
                           tf.local_variables_initializer())
        self.sess.run(init_op)

    def restore_model(self, path: str) -> None:
        print("Restoring weights from file %s." % path)
        with open(path, 'rb') as in_file:
            data_to_load = pickle.load(in_file)

        # Assert that we got the same model configuration
        assert len(self.params) == len(data_to_load['params'])
        for (par, par_value) in self.params.items():
            # Fine to have different task_ids:
            if par not in ['task_ids', 'num_epochs']:
                assert par_value == data_to_load['params'][par]

        variables_to_initialize = []
        with tf.name_scope("restore"):
            restore_ops = []
            used_vars = set()
            for variable in self.sess.graph.get_collection(tf.GraphKeys.GLOBAL_VARIABLES):
                used_vars.add(variable.name)
                if variable.name in data_to_load['weights']:
                    restore_ops.append(variable.assign(data_to_load['weights'][variable.name]))
                else:
                    print('Freshly initializing %s since no saved value was found.' % variable.name)
                    variables_to_initialize.append(variable)
            for var_name in data_to_load['weights']:
                if var_name not in used_vars:
                    print('Saved weights for %s not used by model.' % var_name)
            restore_ops.append(tf.variables_initializer(variables_to_initialize))
            self.sess.run(restore_ops)
