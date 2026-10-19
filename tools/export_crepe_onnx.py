#!/usr/bin/env python3
"""Export torchcrepe's CREPE tiny weights (MIT) to ONNX for the analysis pack, and check the export.

    pip install torch torchcrepe onnx onnxruntime
    python tools/export_crepe_onnx.py OUT_DIR
    studio-one-ai-bridge analysis-setup --model-dir OUT_DIR

Input "frames" (n, 1024): 16 kHz frames, each normalised to zero mean and unit std.
Output "probs" (n, 360): pitch bin activations, 20 cents per bin from 1997.38 cents.
"""
import os
import sys

import numpy as np
import onnxruntime as ort
import torch
import torchcrepe


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for cap in ("tiny",):
        model = torchcrepe.Crepe(cap)
        weights = os.path.join(os.path.dirname(torchcrepe.__file__), "assets", cap + ".pth")
        model.load_state_dict(torch.load(weights, map_location="cpu"))
        model.eval()
        out = os.path.join(out_dir, "crepe-%s.onnx" % cap)
        torch.onnx.export(model, (torch.randn(64, 1024),), out, input_names=["frames"], output_names=["probs"],
                          dynamic_axes={"frames": {0: "n"}, "probs": {0: "n"}}, opset_version=17, dynamo=False)
        x = torch.randn(200, 1024)
        with torch.no_grad():
            want = model(x).numpy()
        got = ort.InferenceSession(out, providers=["CPUExecutionProvider"]).run(None, {"frames": x.numpy()})[0]
        err = float(np.abs(want - got).max())
        print("%s: max difference from torch %.2e" % (out, err))
        if err > 1e-4:
            sys.exit("export does not match torch")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
