#!/usr/bin/env python3
"""
Sat2Graph Docker Automation Script (Final)
- Automatically manages the Sat2Graph Docker container for QGIS integration
- Matches the official client request format
- Adds warm-up & robust readiness logic
- Surfaces useful diagnostics on failure
"""

import base64
import json
import os
import sys
import time
import tempfile
from pathlib import Path

import requests

try:
    import docker
except Exception as e:
    print("ERROR: python 'docker' SDK is required. Install with: pip install docker")
    raise

# 1x1 white PNG (base64) for warm-up; avoids adding Pillow/numpy as hard deps
_ONE_BY_ONE_WHITE_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAA"
    "AAC0lEQVR42mP8/x8AAwMCAO8cY7kAAAAASUVORK5CYII="
)

class Sat2GraphDockerManager:
    """
    Automatically manages the Sat2Graph Docker container.
    Handles installation, startup, communication, and basic diagnostics.
    """

    CPU_IMAGE = "songtaohe/sat2graph_inference_server_cpu:latest"
    CONTAINER_NAME = "sat2graph_qgis_inference"
    STATUS_PORT = 8010  # maps to 8000/tcp in container
    INFER_PORT = 8011   # maps to 8001/tcp in container

    def __init__(self):
        self.client = None
        self.container = None
        self.status_callbacks = []

    # ------------------ Utilities ------------------

    def add_status_callback(self, callback):
        """Add a callback function for status updates (e.g., print to QGIS message bar)."""
        self.status_callbacks.append(callback)

    def _update_status(self, message):
        """Send status updates to all registered callbacks."""
        for callback in self.status_callbacks:
            try:
                callback(message)
            except Exception:
                pass

    def _infer_url(self):
        return f"http://localhost:{self.INFER_PORT}"

    def _status_url(self):
        return f"http://localhost:{self.STATUS_PORT}"

    def _dump_container_logs(self, tail=200):
        """Print recent container logs to help diagnose issues."""
        if not self.container:
            return
        try:
            logs = self.container.logs(tail=tail).decode("utf-8", errors="ignore")
            print("\n--- Sat2Graph container logs (tail) ---")
            print(logs)
        except Exception:
            pass

    # ------------------ Docker Lifecycle ------------------

    def initialize_docker(self):
        """Initialize Docker client and check installation."""
        try:
            self._update_status("Checking Docker installation...")
            self.client = docker.from_env()
            self.client.ping()
            self._update_status("Docker is running ✓")
            return True
        except docker.errors.DockerException as e:
            self._update_status(f"Docker not available: {str(e)}")
            self._update_status("Please install and start Docker Desktop first.")
            return False
        except Exception as e:
            self._update_status(f"Error initializing Docker: {str(e)}")
            return False

    def is_container_running(self):
        """Check if Sat2Graph inference server responds quickly."""
        try:
            r = requests.get(f"{self._infer_url()}/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        try:
            r = requests.get(f"{self._status_url()}/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        return False

    def pull_image(self):
        """Download the Sat2Graph Docker image if not available."""
        try:
            self._update_status("Checking for Sat2Graph Docker image...")
            try:
                self.client.images.get(self.CPU_IMAGE)
                self._update_status("Docker image already available ✓")
                return True
            except docker.errors.ImageNotFound:
                pass

            self._update_status("Downloading Sat2Graph Docker image (this can take a while)...")
            image = self.client.images.pull(self.CPU_IMAGE)
            self._update_status(f"Docker image downloaded: {image.id[:12]} ✓")
            return True
        except Exception as e:
            self._update_status(f"Failed to download Docker image: {str(e)}")
            return False

    def stop_container(self):
        """Stop any running Sat2Graph containers (by name or image tag)."""
        try:
            containers = self.client.containers.list(all=True)
            for c in containers:
                name = (c.name or "").lower()
                tags = getattr(c.image, "tags", []) or []
                is_sat2graph = (
                    "sat2graph" in name
                    or any("songtaohe/sat2graph_inference_server" in t for t in tags)
                    or name == self.CONTAINER_NAME
                )
                if is_sat2graph:
                    self._update_status(f"Stopping existing container: {name}")
                    try:
                        c.stop(timeout=10)
                        time.sleep(1)
                    except Exception:
                        pass
        except Exception as e:
            self._update_status(f"Warning: Could not stop existing containers: {str(e)}")

    def start_container(self):
        """Start the Sat2Graph Docker container and wait for readiness."""
        try:
            self._update_status("Starting Sat2Graph container...")
            self.stop_container()

            self.container = self.client.containers.run(
                self.CPU_IMAGE,
                ports={
                    "8000/tcp": self.STATUS_PORT,  # status / files
                    "8001/tcp": self.INFER_PORT,   # inference API
                },
                detach=True,
                remove=True,  # auto-remove when stopped
                name=self.CONTAINER_NAME,
            )

            self._update_status("Container started, waiting for readiness...")
            if self._wait_for_container_ready(timeout=90):
                # Optional warm-up (some servers are responsive before models are hot)
                self._warm_up_once()
                self._update_status("Sat2Graph container is ready! ✓")
                return True
            else:
                self._update_status("Container failed to become ready within timeout.")
                self._dump_container_logs()
                return False

        except Exception as e:
            self._update_status(f"Failed to start container: {str(e)}")
            self._dump_container_logs()
            return False

    def _wait_for_container_ready(self, timeout=90):
        """
        Wait until the server endpoints respond, then wait a few seconds more
        so that models can finish initializing.
        """
        start = time.time()
        seen_ok = False
        while time.time() - start < timeout:
            ok = False
            try:
                r = requests.get(self._infer_url(), timeout=2)
                ok = ok or (r.status_code == 200)
            except Exception:
                pass
            try:
                r = requests.get(f"{self._status_url()}/health", timeout=2)
                ok = ok or (r.status_code == 200)
            except Exception:
                pass

            if ok:
                seen_ok = True
                # give it a little extra time to finish model init
                time.sleep(3)
                return True

            time.sleep(2)

        return seen_ok

    def _warm_up_once(self):
        """
        Send a tiny 1x1 PNG to "warm" the model once. Ignore any failure.
        This reduces the chance that the *first* real inference will return a
        minimal JSON without 'graph'.
        """
        try:
            msg = {
                "inputtype": "base64",
                "imagebase64": _ONE_BY_ONE_WHITE_PNG_B64,
                "imagetype": "png",
                "imagegsd": 1.0,
                "v_thr": 0.05,
                "e_thr": 0.01,
                "snap_dist": 15,
                "snap_w": 100,
                "model_id": 1,
                "stride": 176,
                "nPhase": 1,
            }
            # Match the official client: raw JSON bytes in body (no json=)
            _ = requests.post(self._infer_url(), data=json.dumps(msg), timeout=30)
        except Exception:
            pass

    def cleanup(self):
        """Clean up resources (stop container). Call this when you truly want to stop."""
        try:
            self.stop_container()
            self._update_status("Cleanup completed.")
        except Exception as e:
            self._update_status(f"Warning during cleanup: {str(e)}")

    # ------------------ Inference ------------------

    def extract_roads(self, image_path, gsd=1, model_id=3, allow_retry=True):
        """
        Extract road network from an image using the Docker container.

        Args:
            image_path (str): Path to the input image file.
            gsd (float): Ground sampling distance (m/pixel).
            model_id (int): Model selection as per Sat2Graph docs.
            allow_retry (bool): Internal flag for one-time auto-retry.

        Returns:
            str | None: Path to a JSON file containing the road graph (edges), or None if failed.
        """
        try:
            self._update_status("Starting road extraction...")

            # Read and base64-encode image
            with open(image_path, "rb") as fh:
                img_b64 = base64.b64encode(fh.read()).decode("utf-8")

            imagetype = os.path.splitext(image_path)[1][1:].lower() or "png"

            msg = {
                "inputtype": "base64",
                "imagebase64": img_b64,
                "imagetype": imagetype,
                "imagegsd": float(gsd),
                "v_thr": 0.05,
                "e_thr": 0.01,
                "snap_dist": 15,
                "snap_w": 100,
                "model_id": int(model_id),
                "stride": 176,
                "nPhase": 1,
            }
            if model_id == 3:
                msg["nPhase"] = 5

            self._update_status("Sending request to Sat2Graph...")
            # IMPORTANT: match the official client: data=json.dumps(msg)
            response = requests.post(self._infer_url(), data=json.dumps(msg), timeout=6000)

            raw = response.text
            if response.status_code != 200:
                # Show some of the server reply for diagnostics
                snippet = raw[:400].replace("\n", " ")
                raise Exception(f"HTTP {response.status_code}: {snippet}")

            # Parse JSON
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                raise Exception(f"Non-JSON response: {raw[:200]}")

            print(f"DEBUG: Response keys: {list(result.keys()) if isinstance(result, dict) else 'n/a'}")

            # The official client checks: if success == 'false' -> fail
            if isinstance(result, dict) and result.get("success") == "false":
                msg_txt = result.get("message") or result.get("error") or ""
                # If first call fails, optionally retry after a tiny wait (warm-up fallback)
                if allow_retry:
                    self._update_status(f"Inference reported failure: {msg_txt or 'unknown'}. Retrying once...")
                    time.sleep(3)
                    return self.extract_roads(image_path, gsd=gsd, model_id=model_id, allow_retry=False)
                self._dump_container_logs()
                raise Exception(f"Inference failed: {msg_txt or 'unknown error'}")

            # Unwrap graph content
            if isinstance(result, dict) and "graph" in result:
                graph_data = result["graph"]
                if isinstance(graph_data, dict) and "graph" in graph_data:
                    graph_data = graph_data["graph"]
                if isinstance(graph_data, list) and len(graph_data) > 0:
                    graph_data = graph_data[0]
            else:
                # Sometimes server returns minimal dict. Retry once.
                if allow_retry:
                    self._update_status("Server returned no 'graph' key; warming up & retrying once...")
                    time.sleep(3)
                    return self.extract_roads(image_path, gsd=gsd, model_id=model_id, allow_retry=False)
                self._dump_container_logs()
                raise Exception(f"Inference returned no graph. success={result.get('success') if isinstance(result, dict) else 'n/a'}")

            # Persist to temp file
            output_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False, prefix="sat2graph_result_").name
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(graph_data, f, indent=2)

            self._update_status("Road extraction completed successfully! ✓")
            return output_file

        except Exception as e:
            self._update_status(f"Road extraction failed: {str(e)}")
            try:
                # Show server raw response if available from above scope
                if "raw" in locals():
                    print("SERVER RAW RESPONSE (first 1000 chars):")
                    print(raw[:1000])
            except Exception:
                pass
            self._dump_container_logs()
            return None

# ------------------ Example CLI Test Harness ------------------

def print_status(message: str):
    print(f"[STATUS] {message}")

def _find_sample_image():
    """Return a plausible sample image name in current dir (sample.png/jpg), else None."""
    for name in ("sample.png", "sample.jpg", "sample.jpeg"):
        if os.path.exists(name):
            return name
    return None

def _cli_image_arg():
    """Use the first CLI argument as the image path if provided; else None."""
    return sys.argv[1] if len(sys.argv) > 1 else None

def test_automation():
    print("Testing Sat2Graph Docker Automation...")
    print("=" * 50)

    mgr = Sat2GraphDockerManager()
    mgr.add_status_callback(print_status)

    # 1) Docker up?
    if not mgr.initialize_docker():
        print("Docker initialization failed. Please install/start Docker Desktop.")
        return False

    # 2) Container running?
    if mgr.is_container_running():
        print("Container is already running ✓")
    else:
        # Pull image if needed
        if not mgr.pull_image():
            print("Image pull failed.")
            return False
        # Start container
        if not mgr.start_container():
            print("Container startup failed.")
            return False

    # 3) Choose image: CLI arg takes precedence; else fall back to sample.*
    img = _cli_image_arg() or _find_sample_image()
    if img:
        print(f"Testing with image: {img}")
        out = mgr.extract_roads(img, gsd=1, model_id=3)
        if out:
            print(f"Success! Results saved to: {out}")
            print(f"Browse intermediate files at: {mgr._status_url()}/")
        else:
            print("Road extraction test failed. See logs above.")
    else:
        print("No image provided and no sample image found; skipping inference. Container is healthy.")

    print("\n🔌 Container is still running. Use it for debugging.")
    print(f"To stop it later: docker stop {mgr.CONTAINER_NAME}")
    return True

if __name__ == "__main__":
    ok = test_automation()
    print("\n" + "=" * 50)
    if ok:
        print("✅ Docker automation test completed successfully!")
        print("The Sat2Graph container is ready for QGIS integration!")
    else:
        print("❌ Docker automation test failed.")
    print("\nNext steps:")
    print("1) Integrate Sat2GraphDockerManager into your QGIS plugin")
    print("2) Use extract_roads() with images from the map canvas")
    print("3) Convert the returned JSON to QGIS vector layers (roads)")
