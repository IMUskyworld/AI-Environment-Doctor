"""Generated smoke scripts that run inside the throwaway environment.

These are envdoctor's own code (never the project's training scripts): a device
inventory and a minimal forward/backward pass per installed framework.
"""

from __future__ import annotations

DEVICE_SCRIPT = """
import json, platform, sys

payload = {"python": sys.version.split()[0], "platform": platform.platform()}

try:
    import torch
    payload["torch"] = torch.__version__
    payload["torch_cuda"] = torch.version.cuda
    payload["cuda_available"] = bool(torch.cuda.is_available())
    if torch.cuda.is_available():
        payload["device_count"] = torch.cuda.device_count()
        payload["devices"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
except ImportError:
    pass
except Exception as exc:
    payload["torch_error"] = f"{type(exc).__name__}: {exc}"

try:
    import tensorflow as tf
    payload["tensorflow"] = tf.__version__
    payload["tensorflow_gpus"] = [d.name for d in tf.config.list_physical_devices("GPU")]
except ImportError:
    pass
except Exception as exc:
    payload["tensorflow_error"] = f"{type(exc).__name__}: {exc}"

try:
    import jax
    payload["jax"] = jax.__version__
    payload["jax_devices"] = [str(d) for d in jax.devices()]
except ImportError:
    pass
except Exception as exc:
    payload["jax_error"] = f"{type(exc).__name__}: {exc}"

print(json.dumps(payload, ensure_ascii=False))
"""

MINIMAL_MODEL_SCRIPT = """
import json

results = {}
failed = False

try:
    import torch

    try:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = torch.nn.Sequential(
            torch.nn.Linear(16, 32),
            torch.nn.ReLU(),
            torch.nn.Linear(32, 4),
        ).to(device)
        x = torch.randn(8, 16, device=device)
        loss = model(x).sum()
        loss.backward()
        matmul = (torch.randn(64, 64, device=device) @ torch.randn(64, 64, device=device)).sum().item()
        results["torch"] = {"ok": True, "device": device, "matmul": round(float(matmul), 3)}
    except Exception as exc:
        results["torch"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        failed = True
except ImportError:
    pass

try:
    import tensorflow as tf

    try:
        a = tf.random.normal((8, 16))
        w = tf.random.normal((16, 4))
        product = (a @ w).numpy()
        results["tensorflow"] = {"ok": True, "devices": [d.name for d in tf.config.list_physical_devices()]}
    except Exception as exc:
        results["tensorflow"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        failed = True
except ImportError:
    pass

try:
    import jax.numpy as jnp

    try:
        product = jnp.matmul(jnp.ones((8, 16)), jnp.ones((16, 4)))
        float(product.sum())
        results["jax"] = {"ok": True}
    except Exception as exc:
        results["jax"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        failed = True
except ImportError:
    pass

print(json.dumps(results, ensure_ascii=False))
raise SystemExit(1 if failed else 0)
"""
