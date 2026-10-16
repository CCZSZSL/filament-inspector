"""v14.7.1 rotated sensor band coordinate adapter; no detector changes.

Sensor GRAY8 shape HxW (2592x4608); detector works on rotate90CW,
shape (4608x2592). Working X [x0,x0+width) maps to sensor rows
[H-x0-width,H-x0). Data always cropped at original pixel resolution.
"""
from __future__ import annotations
import cv2
import numpy as np

PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def choose_band(left_roi, right_roi, work_width=2592, band_width=64):
    if len(left_roi) != 4 or len(right_roi) != 4:
        raise ValueError('Both calibrated mirror search ROIs required')
    lx0, lx1 = int(left_roi[0]), int(left_roi[2]); rx0, rx1 = int(right_roi[0]), int(right_roi[2])
    if (lx0, lx1) != (rx0, rx1) or lx1-lx0+1 != 40:
        raise ValueError('Left/Right must share a 40-column working X band')
    if band_width < 40 or band_width > 256 or band_width > work_width:
        raise ValueError('Invalid ROI band width')
    start = max(0, min(work_width-band_width, (lx0+lx1+1)//2 - band_width//2))
    if start > lx0 or start + band_width <= lx1:
        raise ValueError('Chosen band does not contain all 40 original columns')
    return start, band_width


def sensor_row_range(working_x0, band_width, raw_height=2592):
    x0, width = int(working_x0), int(band_width)
    if width < 40 or width > 256 or x0 < 0 or x0+width > raw_height:
        raise ValueError('ROI out of sensor range')
    return raw_height-x0-width, raw_height-x0


def decode_validate_roi_png(blob, headers, expected_x0, expected_width, native_size_wh=(4608,2592), expected_epoch=None):
    if not blob.startswith(PNG_MAGIC) or b'IEND' not in blob[-32:]:
        raise ValueError('Incomplete or non-PNG ROI capture')
    data = cv2.imdecode(np.frombuffer(blob, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    raw_w, raw_h = native_size_wh
    if data is None or data.dtype != np.uint8 or data.ndim != 2 or data.shape != (expected_width, raw_w):
        raise ValueError(f'ROI must be genuine GRAY8 ({expected_width},{raw_w}); received {getattr(data,"shape",None)}')
    def header(name):
        return next((v for k,v in headers.items() if k.lower() == name.lower()), None)
    if header('X-ROI-Working-X0') is None or int(header('X-ROI-Working-X0')) != expected_x0:
        raise ValueError('ROI origin mismatch')
    if header('X-ROI-Width') is None or int(header('X-ROI-Width')) != expected_width:
        raise ValueError('ROI width mismatch')
    if (header('X-Full-Image-Width'), header('X-Full-Image-Height')) != (str(raw_w),str(raw_h)):
        raise ValueError('ROI native sensor size mismatch')
    if header('X-Pixel-Format') != 'GRAY8':
        raise ValueError('ROI sensor pixel format mismatch')
    epoch = (header('X-Session-ID'), header('X-Focus-Epoch'), header('X-Settings-Epoch'))
    if expected_epoch is not None and tuple(expected_epoch) != epoch:
        raise ValueError('Camera focus/exposure/session changed: old background invalid')
    return data, epoch


def install_band_in_place(full_working_bgr, full_sensor_bgr, decoded_sensor_band, working_x0):
    """Update ONLY the true measured pixels; preserve full-size geometry/calibration.

    No interpolation/resizing. Original detector continues to see correct
    (H=4608,W=2592) working array and original full-size sensor array.
    """
    if full_working_bgr.ndim != 3 or full_sensor_bgr.ndim != 3 or full_working_bgr.shape[2] != 3 or full_sensor_bgr.shape[2] != 3:
        raise ValueError('Expected cached 3-channel full-size images')
    raw_h, raw_w = full_sensor_bgr.shape[:2]
    if full_working_bgr.shape[:2] != (raw_w,raw_h):
        raise ValueError('Cached rotated image does not match sensor geometry')
    bw = decoded_sensor_band.shape[0]
    if decoded_sensor_band.dtype != np.uint8 or decoded_sensor_band.shape != (bw,raw_w):
        raise ValueError('Invalid decoded sensor band shape')
    y0,y1=sensor_row_range(working_x0,bw,raw_h)
    bgr=cv2.cvtColor(decoded_sensor_band,cv2.COLOR_GRAY2BGR)
    # Important: both caches are independent arrays; do not let processing use stale pixels.
    full_sensor_bgr[y0:y1,:,:]=bgr
    full_working_bgr[:,working_x0:working_x0+bw,:]=cv2.rotate(bgr,cv2.ROTATE_90_CLOCKWISE)
