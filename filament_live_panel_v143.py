"""Mac Tkinter live camera panel; imports no Pi-side camera dependencies."""
from __future__ import annotations
import io
import json
import queue
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox
import tkinter as tk

import cv2
from PIL import Image, ImageTk, ImageDraw


class LivePanel(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("v14.3.1 | Live Video, Focus & Exposure")
        self.geometry("820x860")
        self.minsize(560, 560)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.url = tk.StringVar(value="http://127.0.0.1:8765")
        self.lens = tk.StringVar(value="9.515")
        self.exposure_us = tk.StringVar(value="1000")
        self.analogue_gain = tk.StringVar(value="1.0")
        self.status_text = tk.StringVar(value="Not connected")
        self.info_text = tk.StringVar(value="Video is only preview. Capture produces full-resolution source frames.")
        self.frame_queue = queue.Queue(maxsize=1)
        self.events = queue.Queue()
        self.worker_stop = threading.Event()
        self.video_thread = None
        self.photo = None
        self.last_pil = None
        self.focus_id_for_background = None
        self.native_size_wh = None
        self.capture_dir = Path(__file__).resolve().parent / "captures_live"
        self._build()
        self.after(100, self._poll)

    def _build(self):
        header = ttk.Frame(self, padding=10)
        header.pack(fill="x")
        ttk.Label(header, text="Raspberry Pi URL:").grid(row=0, column=0, sticky="w")
        ttk.Entry(header, textvariable=self.url, width=32).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(header, text="Connect", command=self._connect).grid(row=0, column=2, padx=3)
        ttk.Button(header, text="Disconnect", command=self._disconnect).grid(row=0, column=3)
        header.columnconfigure(1, weight=1)

        focus = ttk.LabelFrame(self, text="Focus once, then keep the lens locked", padding=10)
        focus.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(focus, text="One-shot AUTO focus", command=self._auto_focus).pack(side="left", padx=(0, 8))
        ttk.Label(focus, text="Manual LensPosition:").pack(side="left")
        ttk.Entry(focus, textvariable=self.lens, width=8).pack(side="left", padx=4)
        ttk.Button(focus, text="Set & Lock", command=self._manual_focus).pack(side="left")

        exposure = ttk.LabelFrame(self, text="Manual Exposure | live adjustment (no camera restart)", padding=8)
        exposure.pack(fill="x", padx=10, pady=(0, 8))
        row1 = ttk.Frame(exposure)
        row1.pack(fill="x")
        ttk.Label(row1, text="Exposure (µs):").pack(side="left")
        ttk.Entry(row1, textvariable=self.exposure_us, width=9).pack(side="left", padx=4)
        ttk.Label(row1, text="Analogue Gain:").pack(side="left", padx=(10, 0))
        ttk.Entry(row1, textvariable=self.analogue_gain, width=6).pack(side="left", padx=4)
        ttk.Button(row1, text="Apply Exposure & Gain", command=self._manual_exposure).pack(side="left", padx=6)
        row2 = ttk.Frame(exposure)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Label(row2, text="Quick exposure:").pack(side="left")
        for us, label in ((100, "0.1 ms"), (500, "0.5 ms"), (1000, "1 ms"),
                          (5000, "5 ms"), (10000, "10 ms")):
            ttk.Button(row2, text=label,
                       command=lambda v=us: self._preset_exposure(v)).pack(side="left", padx=3)

        panel = ttk.Frame(self, padding=(10, 0, 10, 0))
        panel.pack(fill="both", expand=True)
        self.image_label = tk.Label(panel, bg="#141414", fg="white", text="Connect to Raspberry Pi live video")
        self.image_label.pack(fill="both", expand=True)
        self.image_label.bind("<Configure>", lambda _: self._render())
        ttk.Label(self, textvariable=self.status_text, wraplength=760).pack(fill="x", padx=12, pady=4)

        cap = ttk.LabelFrame(self, text="Capture from live camera (FULL 4608 × 2592)", padding=9)
        cap.pack(fill="x", padx=10, pady=5)
        ttk.Button(cap, text="1. Capture EMPTY background", command=lambda: self._capture("background")).grid(row=0, column=0, sticky="ew", padx=4)
        ttk.Button(cap, text="2. Capture CURRENT frame", command=lambda: self._capture("current")).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(cap, text="Capture + Measure", command=lambda: self._capture("measure")).grid(row=0, column=2, sticky="ew", padx=4)
        for x in range(3):
            cap.columnconfigure(x, weight=1)
        ttk.Label(self, textvariable=self.info_text, wraplength=780, justify="left").pack(fill="x", padx=12, pady=(3, 10))

    def _base_url(self):
        url = self.url.get().strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return url

    def _request(self, base_url, endpoint, payload=None, timeout=20):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        method = "GET" if data is None else "POST"
        req = urllib.request.Request(base_url + endpoint, data=data,
                                     method=method,
                                     headers={"Content-Type": "application/json"} if data is not None else {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), dict(r.headers)

    def _start_action(self, kind, endpoint, data=None, timeout=20):
        base_url = self._base_url()  # Tk variables may only be read on the main thread.
        def background():
            try:
                payload, headers = self._request(base_url, endpoint, data, timeout)
                self.events.put((kind, True, payload, headers))
            except Exception as exc:
                self.events.put((kind, False, str(exc), {}))
        threading.Thread(target=background, daemon=True).start()

    def _connect(self):
        self._disconnect()
        self.status_text.set("Connecting...")
        self.worker_stop = threading.Event()
        url = self._base_url()
        stop_event = self.worker_stop

        def worker():
            try:
                req = urllib.request.Request(url + "/stream.mjpg")
                with urllib.request.urlopen(req, timeout=8) as response:
                    self.events.put(("connected", True, b"", {}))
                    data = bytearray()
                    while not stop_event.is_set():
                        block = response.read(8192)
                        if not block:
                            raise ConnectionError("Video stream ended")
                        data.extend(block)
                        # The stream sends JPEG frames; parse SOI/EOI and discard stale frames.
                        while True:
                            start = data.find(b"\xff\xd8")
                            if start < 0:
                                if len(data) > 10_000_000:
                                    data.clear()
                                break
                            end = data.find(b"\xff\xd9", start + 2)
                            if end < 0:
                                if start > 0:
                                    del data[:start]
                                break
                            jpeg = bytes(data[start:end+2])
                            del data[:end+2]
                            try:
                                pil = Image.open(io.BytesIO(jpeg)).convert("RGB")
                                pil = pil.transpose(Image.Transpose.ROTATE_270)  # 90 deg CW
                            except Exception:
                                continue
                            while True:
                                try:
                                    self.frame_queue.get_nowait()
                                except queue.Empty:
                                    break
                            self.frame_queue.put(pil)
            except Exception as exc:
                self.events.put(("connection", False, str(exc), {}))
        self.video_thread = threading.Thread(target=worker, daemon=True)
        self.video_thread.start()
        self._start_action("status", "/status")

    def _disconnect(self):
        if hasattr(self, "worker_stop"):
            self.worker_stop.set()
        self.status_text.set("Disconnected")

    def _manual_focus(self):
        try:
            lens = float(self.lens.get())
        except ValueError:
            messagebox.showerror("LensPosition", "Enter a numeric lens position", parent=self)
            return
        self._start_action("manual_focus", "/focus_manual", {"lens_position": lens})

    def _manual_exposure(self):
        try:
            exposure_us = int(self.exposure_us.get().strip())
            gain = float(self.analogue_gain.get().strip())
            if not 50 <= exposure_us <= 100000:
                raise ValueError("Exposure must be 50–100000 µs")
            if not 1.0 <= gain <= 16.0:
                raise ValueError("Gain must be 1.0–16.0")
        except ValueError as exc:
            messagebox.showerror("Manual Exposure", str(exc), parent=self)
            return
        self._start_action("manual_exposure", "/exposure_manual",
                           {"exposure_us": exposure_us, "analogue_gain": gain})

    def _preset_exposure(self, us):
        self.exposure_us.set(str(us))
        self._manual_exposure()

    def _auto_focus(self):
        if not messagebox.askyesno("Focus will change",
            "Run a single autofocus cycle? This invalidates the previously captured background and may require checking calibration.", parent=self):
            return
        self._start_action("auto_focus", "/focus_once", {})

    def _capture(self, kind):
        self.info_text.set("Capturing full-resolution source frame (preview continues)...")
        self._start_action("capture:" + kind, "/snapshot.jpg", timeout=30)

    def _render(self):
        if self.last_pil is None or not self.image_label.winfo_exists():
            return
        size = (self.image_label.winfo_width(), self.image_label.winfo_height())
        if min(size) <= 3:
            return
        pil = self.last_pil.copy()
        # Show the same fixed three-view search boxes on the live preview when
        # the last loaded native frame matches the streaming sensor resolution.
        expected = self.native_size_wh
        raw = self.app.image_cv_raw
        if (expected and raw is not None and
                (raw.shape[1], raw.shape[0]) == tuple(expected) and
                hasattr(self.app, "views")):
            from filament_diameter_paper_method_ui_v13_three_view import PALETTE
            widths = {"direct": 2, "left": 2, "right": 2}
            draw = ImageDraw.Draw(pil)
            ref_work_w, ref_work_h = expected[1], expected[0]
            for key in ("direct", "left", "right"):
                rect = self.app.views[key].get("search_roi_xyxy")
                if not rect:
                    continue
                x0, y0, x1, y1 = rect
                coords = (int(x0*pil.width/ref_work_w),
                          int(y0*pil.height/ref_work_h),
                          int(x1*pil.width/ref_work_w),
                          int(y1*pil.height/ref_work_h))
                draw.rectangle(coords, outline=PALETTE[key], width=widths[key])
        pil.thumbnail(size, Image.Resampling.BILINEAR)
        self.photo = ImageTk.PhotoImage(pil)
        self.image_label.configure(image=self.photo, text="")

    def _handle_capture(self, kind, jpg, headers):
        if not jpg.startswith(b"\xff\xd8") or not jpg.endswith(b"\xff\xd9"):
            raise ValueError("Snapshot response is not a complete JPEG")
        # Source image remains unrotated and is ingested by the legacy 90-CW pipeline.
        session = headers.get("X-Session-Id", headers.get("X-Session-ID", ""))
        epoch = headers.get("X-Focus-Epoch", "")
        settings_epoch = headers.get("X-Settings-Epoch", "0")
        focus_key = (session, epoch, settings_epoch)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        name = "background" if kind == "background" else "current"
        path = self.capture_dir / (timestamp + "_" + name + ".jpg")
        path.write_bytes(jpg)
        meta = {"source": self._base_url(), "focus_session": session,
                "focus_epoch": epoch, "settings_epoch": settings_epoch,
                "exposure_us": headers.get("X-Exposure-Time-Us"),
                "analogue_gain": headers.get("X-Analogue-Gain"),
                "lens_position": headers.get("X-Lens-Position"),
                "kind": name, "captured_at": timestamp}
        path.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        if kind == "background":
            self.focus_id_for_background = focus_key
            self.app._load_background_from_live(path)
            self.info_text.set(f"Full-resolution BACKGROUND saved: {path.name} | focus epoch {epoch}")
            return
        if self.focus_id_for_background is not None and focus_key != self.focus_id_for_background:
            self.info_text.set("Focus or exposure/gain changed after background; recapture background before measuring")
            messagebox.showwarning("Background invalid", "Focus or exposure/gain changed after capturing the empty background. Recapture it before diameter measurement.", parent=self)
            self.app._load_image(path)
            self.app._clear_live_background()
            return
        self.app._load_image(path)
        self.info_text.set(f"Full-resolution CURRENT saved: {path.name} | focus epoch {epoch}")
        if kind == "measure":
            if self.focus_id_for_background is None:
                messagebox.showwarning("Background required", "Capture an empty background from this live session first.", parent=self)
            else:
                self.app._v14_process_all()

    def _poll(self):
        try:
            while True:
                try:
                    frame = self.frame_queue.get_nowait()
                except queue.Empty:
                    break
                self.last_pil = frame
                self._render()
            for _ in range(10):
                try:
                    kind, ok, payload, headers = self.events.get_nowait()
                except queue.Empty:
                    break
                if not ok:
                    self.status_text.set(f"{kind}: {payload}")
                    self.info_text.set(f"Operation failed: {kind}. Check Pi server/SSH connection.")
                    continue
                if kind.startswith("capture:"):
                    try:
                        self._handle_capture(kind.split(":", 1)[1], payload, headers)
                    except Exception as exc:
                        self.info_text.set(f"Capture failed: {exc}")
                    continue
                if kind in ("status", "manual_focus", "auto_focus", "manual_exposure"):
                    data = json.loads(payload)
                    self.lens.set(f"{data['lens_position_commanded']:.3f}")
                    if "exposure_us" in data:
                        self.exposure_us.set(str(data["exposure_us"]))
                        self.analogue_gain.set(f"{data['analogue_gain']:.3f}")
                    self.native_size_wh = tuple(data["original_image_size_wh"])
                    self.status_text.set(
                        f"Video status: {data['focus_mode']} | focus epoch {data['focus_epoch']} | "
                        f"native {data['original_image_size_wh']} | preview {data['preview_size_wh']} | "
                        f"Exposure {data.get('exposure_us', '?')} µs | Gain {data.get('analogue_gain', '?')}")
                    if kind in ("manual_focus", "auto_focus"):
                        self.focus_id_for_background = None
                        self.app._clear_live_background()
                        self.info_text.set("Focus changed. Recapture EMPTY background; recheck geometric calibration.")
                    elif kind == "manual_exposure":
                        self.focus_id_for_background = None
                        self.app._clear_live_background()
                        self.info_text.set("Exposure/gain applied without refocusing. Recapture EMPTY background before measuring.")
                elif kind == "connected":
                    self.status_text.set("Connected. Preview rotated 90° CW; sensor frames remain unrotated.")
        finally:
            if self.winfo_exists():
                self.after(100, self._poll)

    def _close(self):
        self._disconnect()
        self.app.live_panel = None
        self.destroy()
