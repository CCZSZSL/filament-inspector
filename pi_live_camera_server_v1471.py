#!/usr/bin/env python3
"""v14.5 Pi monochrome dual-stream, focus-locked server.

On Raspberry Pi only. Lores YUV420 uses the native Y plane for monochrome
MJPEG preview. Main 4608x2592 is converted to 8-bit grayscale and sent as
lossless PNG for subpixel metrology; no mode switch on captures.
The server defaults to loopback and should be reached over an SSH tunnel.
"""
from __future__ import annotations
import argparse
import io
import json
import logging
import socketserver
import threading
import time
import uuid
import itertools
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit, parse_qs

# Imported only on Raspberry Pi; the Mac client must NOT need libcamera.
from picamera2 import Picamera2
from libcamera import controls
import cv2

LOG = logging.getLogger("pi_live_camera")


class FrameHub:
    def __init__(self):
        self.frame = None
        self.version = 0
        self.condition = threading.Condition()

    def publish(self, data):
        with self.condition:
            self.frame = data
            self.version += 1
            self.condition.notify_all()

    def wait_next(self, last_version, timeout=3.0):
        with self.condition:
            self.condition.wait_for(lambda: self.version != last_version, timeout)
            return self.version, self.frame


class CameraService:
    def __init__(self, args):
        self.args = args
        self.session_id = uuid.uuid4().hex
        self.focus_epoch = 0
        self.settings_epoch = 0  # Exposure/gain changes invalidate subtraction backgrounds.
        self.exposure_us = int(args.exposure_us)
        self.analogue_gain = float(args.gain)
        self.lens_position = float(args.lens_position)
        self.focus_busy = False
        self.focus_settle_until = 0.0
        self.focus_error = None
        self.running = True
        self.camera_lock = threading.Lock()
        self.fast_frame_counter = itertools.count(1)
        self.frame_hub = FrameHub()
        self.last_preview_time = 0.0
        self.camera = Picamera2()
        interval_us = int(round(1_000_000 / args.fps))
        config = self.camera.create_video_configuration(
            main={"size": tuple(args.main_size), "format": "RGB888"},
            lores={"size": tuple(args.preview_size), "format": "YUV420"},
            display=None,
            buffer_count=4,
            controls={
                "AfMode": controls.AfModeEnum.Manual,
                "LensPosition": self.lens_position,
                "AeEnable": False,
                "ExposureTime": self.exposure_us,
                "AnalogueGain": self.analogue_gain,
                "AwbEnable": False,
                "ColourGains": (1.0, 1.0),
                "FrameDurationLimits": (interval_us, interval_us),
            },
        )
        self.camera.configure(config)
        self.camera.start()
        # Explicitly restore manual AF AFTER starting the camera.
        self.camera.set_controls({"AfMode": controls.AfModeEnum.Manual,
                                  "LensPosition": self.lens_position})
        self.preview_thread = threading.Thread(target=self._preview_worker, daemon=True)
        self.preview_thread.start()

    def _preview_worker(self):
        period = 1.0 / self.args.fps
        while self.running:
            try:
                start = time.monotonic()
                with self.camera_lock:
                    yuv = self.camera.capture_array("lores")
                # YUV420 planar data: first h rows are full-resolution Y/luma.
                # Keep the 8-bit Y plane; DO NOT round-trip through RGB or threshold it.
                w,h = self.args.preview_size
                if yuv.shape[0] < h or yuv.shape[1] < w:
                    raise RuntimeError(f"Unexpected lores YUV shape {yuv.shape}, expected {w}x{h} luma")
                gray = yuv[:h, :w]
                ok, encoded = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, self.args.jpeg_quality])
                if not ok:
                    raise RuntimeError("JPEG encoding failed")
                self.frame_hub.publish(encoded.tobytes())
                self.last_preview_time = time.monotonic()
                remain = period - (time.monotonic() - start)
                if remain > 0:
                    time.sleep(remain)
            except Exception:
                LOG.exception("Preview worker error")
                time.sleep(0.5)

    def status(self):
        return {
            "ok": True,
            "session_id": self.session_id,
            "focus_epoch": self.focus_epoch,
            "settings_epoch": self.settings_epoch,
            "focus_busy": self.focus_busy,
            "focus_error": self.focus_error,
            "lens_position_commanded": self.lens_position,
            "focus_mode": "manual_locked" if not self.focus_busy else "focusing_once",
            "original_image_size_wh": list(self.args.main_size),
            "preview_size_wh": list(self.args.preview_size),
            "preview_age_sec": round(time.monotonic() - self.last_preview_time, 3) if self.last_preview_time else None,
            "exposure_us": self.exposure_us,
            "analogue_gain": self.analogue_gain,
            "preview_pixel_format": "GRAY8 (Y from YUV420, JPEG MJPEG)",
            "measurement_pixel_format": "GRAY8 (RGB888-to-luma, lossless PNG)",
            "note": "No autofocus is triggered for regular video frames or full-resolution snapshots",
        }

    def set_manual_focus(self, lens):
        if not 0.0 <= lens <= 32.0:
            raise ValueError("LensPosition outside allowed software range 0-32")
        if self.focus_busy:
            raise RuntimeError("Autofocus is currently running")
        # Even an identical command may trigger movement; skip exactly equal values.
        if abs(lens - self.lens_position) > 1e-9:
            with self.camera_lock:
                self.camera.set_controls({"AfMode": controls.AfModeEnum.Manual, "LensPosition": lens})
            self.lens_position = lens
            self.focus_epoch += 1
            self.focus_settle_until = time.monotonic() + 0.6
        return self.status()

    def set_manual_exposure(self, exposure_us, gain):
        """Update exposure and analogue gain without stopping or refocusing the camera.

        The exposure limit is conservative for the 8 FPS test setup; sensor
        firmware may clamp an out-of-range exposure to a supported value.
        """
        exposure_us = int(exposure_us)
        gain = float(gain)
        if not 50 <= exposure_us <= 100000:
            raise ValueError("ExposureTime must be in [50, 100000] microseconds")
        if not 1.0 <= gain <= 16.0:
            raise ValueError("AnalogueGain must be in [1.0, 16.0]")
        if self.focus_busy:
            raise RuntimeError("Cannot change exposure during autofocus")
        if exposure_us != self.exposure_us or abs(gain - self.analogue_gain) > 1e-9:
            with self.camera_lock:
                self.camera.set_controls({"AeEnable": False,
                                          "ExposureTime": exposure_us,
                                          "AnalogueGain": gain})
            self.exposure_us = exposure_us
            self.analogue_gain = gain
            self.settings_epoch += 1
            LOG.info("Manual exposure: %s us, gain %.3f; settings epoch %s",
                     exposure_us, gain, self.settings_epoch)
        return self.status()

    def autofocus_once(self):
        if self.focus_busy:
            raise RuntimeError("Autofocus already running")
        self.focus_busy = True
        self.focus_error = None
        self.focus_epoch += 1  # Invalidate old backgrounds as soon as focus changes.

        def run():
            try:
                with self.camera_lock:
                    self.camera.set_controls({"AfMode": controls.AfModeEnum.Auto,
                                              "AfRange": controls.AfRangeEnum.Macro})
                result = self.camera.autofocus_cycle(wait=True)
                with self.camera_lock:
                    metadata = self.camera.capture_metadata()
                lens = metadata.get("LensPosition", self.lens_position)
                if isinstance(lens, (float, int)) and float(lens) >= 0:
                    self.lens_position = float(lens)
                with self.camera_lock:
                    self.camera.set_controls({"AfMode": controls.AfModeEnum.Manual,
                                              "LensPosition": self.lens_position})
                LOG.info("One-shot AF result: %s; locked lens=%s", result, self.lens_position)
            except Exception as exc:
                self.focus_error = repr(exc)
                LOG.exception("Autofocus cycle failed")
                try:
                    with self.camera_lock:
                        self.camera.set_controls({"AfMode": controls.AfModeEnum.Manual,
                                                  "LensPosition": self.lens_position})
                except Exception:
                    LOG.exception("Manual focus recovery failed")
            finally:
                self.focus_settle_until = time.monotonic() + 0.6
                self.focus_busy = False
        threading.Thread(target=run, daemon=True).start()
        return self.status()

    def snapshot(self, lossless=True):
        """Capture true main-resolution pixels; grayscale PNG avoids JPEG edge artefacts.

        This is not an upscaled lores preview. RGB888 main is retained to avoid
        reconfiguring the proven IMX708 sensor pipeline during the first v14.5 test.
        """
        if self.focus_busy or time.monotonic() < self.focus_settle_until:
            raise RuntimeError("Lens moving or settling; try snapshot again shortly")
        with self.camera_lock:
            main = self.camera.capture_array("main")
        # OpenCV convention is BGR; this agrees with the existing RGB888 capture
        # pipeline's cv2.imencode() behaviour. Only intensity is used downstream.
        if main.ndim == 3 and main.shape[2] >= 3:
            gray = cv2.cvtColor(main, cv2.COLOR_BGR2GRAY)
        elif main.ndim == 2:
            gray = main
        else:
            raise RuntimeError(f"Unexpected main stream shape: {main.shape}")
        w,h = self.args.main_size
        if gray.shape != (h,w):
            raise RuntimeError(f"Full-res shape mismatch: {gray.shape}, expected {(h,w)}")
        ext = ".png" if lossless else ".jpg"
        params = [cv2.IMWRITE_PNG_COMPRESSION, 1] if lossless else [cv2.IMWRITE_JPEG_QUALITY, 98]
        ok, encoded = cv2.imencode(ext, gray, params)
        if not ok:
            raise RuntimeError("Grayscale image encoding failed")
        return encoded.tobytes()

    def snapshot_working_band(self, x0, width):
        """Return original-resolution pixels for X band in the 90-degree-CW WORKING view.

        Original sensor is (H=2592,W=4608). Working rotation maps
        working x -> original row H-1-x, so a vertical WORKING band is a
        horizontal sensor strip. Cropping happens before RGB-to-gray and PNG.
        Never rescale or change camera configuration.
        """
        x0 = int(x0); width = int(width)
        full_w, full_h = self.args.main_size
        if not (40 <= width <= 256 and 0 <= x0 and x0 + width <= full_h):
            raise ValueError('Invalid working band: width 40-256 and fully within rotated frame')
        if self.focus_busy or time.monotonic() < self.focus_settle_until:
            raise RuntimeError('Lens moving or settling')
        t0 = time.monotonic_ns()
        with self.camera_lock:
            rgb = self.camera.capture_array('main')
            session = self.session_id
            focus_epoch = self.focus_epoch
            settings_epoch = self.settings_epoch
            exposure_us = self.exposure_us
            analogue_gain = self.analogue_gain
            lens_position = self.lens_position
        t_capture = time.monotonic_ns()
        if rgb.shape[:2] != (full_h, full_w):
            raise RuntimeError(f'Unexpected camera shape: {rgb.shape}, expected {(full_h,full_w)}')
        # Work-rotated x in [x0, x0+width) -> sensor rows [H-x0-width,H-x0)
        y_start = full_h - x0 - width
        band = rgb[y_start:full_h-x0, :, :]
        if band.ndim != 3 or band.shape[2] < 3:
            raise RuntimeError('Expected RGB888 source image')
        gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
        ok, encoded = cv2.imencode('.png', gray, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        if not ok:
            raise RuntimeError('ROI grayscale PNG encoding failed')
        info = {
            'X-ROI-Working-X0': str(x0),
            'X-ROI-Width': str(width),
            'X-Full-Image-Width': str(full_w),
            'X-Full-Image-Height': str(full_h),
            'X-Focus-Epoch': str(focus_epoch),
            'X-Settings-Epoch': str(settings_epoch),
            'X-Exposure-Time-Us': str(exposure_us),
            'X-Analogue-Gain': str(analogue_gain),
            'X-Pixel-Format': 'GRAY8',
            'X-Session-ID': session,
            'X-Lens-Position': str(lens_position),
            'X-Fast-Frame-ID': str(next(self.fast_frame_counter)),
            'X-Capture-Monotonic-Ns': str(t_capture),
            'X-Capture-Elapsed-Ms': f'{(t_capture-t0)*1e-6:.3f}',
        }
        return encoded.tobytes(), info

    def close(self):
        self.running = False
        try:
            with self.camera_lock:
                self.camera.stop()
            self.preview_thread.join(timeout=2.0)
        finally:
            self.camera.close()


class ThreadServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        service = self.server.camera_service
        try:
            if path == "/status":
                return self._json(service.status())
            if path in ("/snapshot.png", "/snapshot.jpg"):
                lossless = path == "/snapshot.png"
                blob = service.snapshot(lossless=lossless)
                self.send_response(200)
                self.send_header("Content-Type", "image/png" if lossless else "image/jpeg")
                self.send_header("Content-Length", str(len(blob)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Focus-Epoch", str(service.focus_epoch))
                self.send_header("X-Settings-Epoch", str(service.settings_epoch))
                self.send_header("X-Exposure-Time-Us", str(service.exposure_us))
                self.send_header("X-Analogue-Gain", str(service.analogue_gain))
                self.send_header("X-Pixel-Format", "GRAY8")
                self.send_header("X-Session-ID", service.session_id)
                self.send_header("X-Lens-Position", str(service.lens_position))
                self.end_headers()
                self.wfile.write(blob)
                return
            if path == "/snapshot_roi.png":
                qs = parse_qs(urlsplit(self.path).query)
                if len(qs.get('x0', [])) != 1 or len(qs.get('width', [])) != 1:
                    raise ValueError('Supply x0 and width in WORKING rotated coordinates')
                blob, headers = service.snapshot_working_band(int(qs['x0'][0]), int(qs['width'][0]))
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(blob)))
                self.send_header('Cache-Control', 'no-store')
                for key, val in headers.items():
                    self.send_header(key, val)
                self.end_headers()
                self.wfile.write(blob)
                return
            if path == "/stream.mjpg":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                last = -1
                while service.running:
                    last, frame = service.frame_hub.wait_next(last)
                    if frame is None:
                        continue
                    self.wfile.write(b"--FRAME\r\nContent-Type: image/jpeg\r\nContent-Length: " +
                                     str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
                return
            self._json({"error": "Unknown route"}, 404)
        except (ConnectionError, BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            LOG.exception("Request error: %s", self.path)
            try:
                self._json({"error": str(exc)}, 409)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_POST(self):
        path = urlsplit(self.path).path
        service = self.server.camera_service
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 2048:
                return self._json({"error": "Request too large"}, 413)
            obj = json.loads(self.rfile.read(size) or b"{}")
            if path == "/focus_once":
                return self._json(service.autofocus_once(), 202)
            if path == "/focus_manual":
                return self._json(service.set_manual_focus(float(obj["lens_position"])))
            if path == "/exposure_manual":
                return self._json(service.set_manual_exposure(obj["exposure_us"], obj["analogue_gain"]))
            return self._json({"error": "Unknown route"}, 404)
        except Exception as exc:
            LOG.exception("Control error")
            self._json({"error": str(exc)}, 409)

    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)


def parse_resolution(value):
    try:
        w, h = [int(x) for x in value.lower().split("x")]
    except Exception as exc:
        raise argparse.ArgumentTypeError("Format should be WIDTHxHEIGHT") from exc
    if w < 32 or h < 32 or w % 2 or h % 2:
        raise argparse.ArgumentTypeError("Sizes must be even and at least 32x32")
    return (w, h)


def main():
    p = argparse.ArgumentParser(description="Camera Module 3 focus-locked dual stream")
    p.add_argument("--host", default="127.0.0.1", help="Defaults to SSH tunnel only")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--main-size", type=parse_resolution, default=(4608, 2592))
    p.add_argument("--preview-size", type=parse_resolution, default=(960, 540))
    p.add_argument("--fps", type=float, default=8.0)
    p.add_argument("--lens-position", type=float, default=9.515)
    p.add_argument("--exposure-us", type=int, default=1000)
    p.add_argument("--gain", type=float, default=1.0)
    p.add_argument("--jpeg-quality", type=int, default=85)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.exposure_us <= 0 or args.fps <= 0 or args.fps > 15:
        p.error("Exposure must be positive; FPS must be between 0 and 15")
    service = CameraService(args)
    server = ThreadServer((args.host, args.port), Handler)
    server.camera_service = service
    LOG.info("v14.7.1 FAST native ROI: http://%s:%d/snapshot_roi.png?x0=1200&width=64", args.host, args.port)
    LOG.info("v14.5 MONO preview (Y): http://%s:%d/stream.mjpg", args.host, args.port)
    LOG.info("Full-res 8-bit grayscale PNG: http://%s:%d/snapshot.png", args.host, args.port)
    LOG.info("Full-res snapshots: %sx%s; focus manual at %.3f", *args.main_size, args.lens_position)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()

if __name__ == "__main__":
    main()
