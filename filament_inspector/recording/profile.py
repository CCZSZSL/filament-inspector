"""Independent v14.6.9 chart and axisymmetric-profile data model.

A reconstruction is intentionally NOT a true roughness or roundness estimate.
Motion/static experiment CSV rows never get axial positions from timestamps.
There are no changes here to detector, mirror fusion, camera, calibration, or ROI.
"""
from __future__ import annotations
import csv
from dataclasses import asdict, dataclass
from datetime import datetime
import math
from pathlib import Path
from typing import Optional


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass
class ProfileSample:
    timestamp: str
    elapsed_s: float
    source: str
    left_mm: Optional[float]
    right_mm: Optional[float]
    f_direct_mm: Optional[float]
    delta_lr_mm: Optional[float]
    status: str
    axial_mm: Optional[float] = None
    feed_speed_mm_s: Optional[float] = None
    # Run ID prevents accidental joining independent starts / sessions.
    axial_run: Optional[int] = None


def row_measurements(row):
    """Handle either v14.6.8 experiment rows or v14.6.7 continuous rows."""
    left = number(row.get('left_diameter_mm', row.get('left_mm')))
    right = number(row.get('right_diameter_mm', row.get('right_mm')))
    fused = number(row.get('f_direct_mm'))
    delta = number(row.get('delta_lr_mm'))
    if fused is None and left is not None and right is not None:
        # For legacy continuous CSV only; mirrors original virtual definition.
        fused = (left + right) / 2
    if delta is None and left is not None and right is not None:
        delta = abs(left - right)
    return left, right, fused, delta


class ProfileRecorder:
    def __init__(self, max_samples=5000):
        self.max_samples = max_samples
        self.samples: list[ProfileSample] = []
        self._initial_time = None
        self._last_feed_time = None
        self._last_feed_position = 0.0
        self._feed_was_on = False
        self._run = 0
        self._last_source = None

    def reset(self):
        self.samples.clear()
        self._initial_time = None
        self._last_feed_time = None
        self._last_feed_position = 0.0
        self._feed_was_on = False
        self._run += 1
        self._last_source = None

    def append(self, *, at_s, timestamp, source, left_mm, right_mm, f_direct_mm,
               delta_lr_mm, status='', feed_enabled=False, speed_mm_s=None):
        """Only explicit axial-feed continuous runs qualify for model length.

        Uses actual monotonic sample times, not UI interval. Missing fused mm
        is an explicit gap. If feed is off, all records have axial_mm=None.
        """
        t = number(at_s)
        if t is None:raise ValueError('nonfinite monotonic time')
        if self._initial_time is None:self._initial_time = t
        fused = number(f_direct_mm)
        left = number(left_mm)
        right = number(right_mm)
        delta = number(delta_lr_mm)
        feed = bool(feed_enabled and source == 'continuous')
        speed = number(speed_mm_s) if feed else None
        if feed and (speed is None or speed <= 0):
            raise ValueError('Axial feed speed must be positive and finite')
        # Discontinuity if user toggles feed, switches source, or restarts run.
        if not feed:
            self._last_feed_time = None
            self._feed_was_on = False
            axial = None
            run = None
        else:
            if not self._feed_was_on or self._last_source != 'continuous':
                self._run += 1
                self._last_feed_time = t
                self._last_feed_position = 0.0
            else:
                dt = t - self._last_feed_time
                if dt < 0:raise ValueError('nonmonotonic axial feed timestamps')
                self._last_feed_position += dt * speed
                self._last_feed_time = t
            axial = self._last_feed_position
            run = self._run
            self._feed_was_on = True
        sample = ProfileSample(timestamp=timestamp, elapsed_s=t-self._initial_time,
                    source=source, left_mm=left, right_mm=right,
                    f_direct_mm=fused, delta_lr_mm=delta, status=status,
                    axial_mm=axial, feed_speed_mm_s=speed, axial_run=run)
        self.samples.append(sample)
        if len(self.samples)>self.max_samples:
            self.samples = self.samples[-self.max_samples:]
        self._last_source = source
        return sample

    def from_csv(self, path):
        """Import CSV for *time* plots only; never infer axial feed from it."""
        path = Path(path)
        with path.open(newline='',encoding='utf-8-sig') as f:
            rows = list(csv.DictReader(f))
        if not rows:raise ValueError('No data rows in CSV')
        self.reset()
        first_time = None
        for i, row in enumerate(rows):
            stamp = row.get('timestamp') or ''
            try:
                t = datetime.fromisoformat(stamp).timestamp()
            except (ValueError, OverflowError):
                t = float(i)
            if first_time is None:first_time=t
            # Guard timestamp disorder: display captured sequence without fabricating feed distance.
            if self.samples and t < first_time + self.samples[-1].elapsed_s:
                t=first_time + self.samples[-1].elapsed_s
            left,right,fused,delta = row_measurements(row)
            self.append(at_s=t, timestamp=stamp or str(i+1),
                        source='imported '+str(row.get('test') or 'CSV'),
                        left_mm=left, right_mm=right, f_direct_mm=fused,
                        delta_lr_mm=delta, status=row.get('frame_status') or
                        ('VALID_MM' if fused is not None else 'NO_FUSION'))
        return len(rows)

    def save_csv(self, path):
        with Path(path).open('w',newline='',encoding='utf-8') as out:
            fields=list(ProfileSample.__dataclass_fields__)
            w=csv.DictWriter(out,fieldnames=fields)
            w.writeheader()
            for s in self.samples:w.writerow(asdict(s))


def radius_mesh_sections(samples, *, exaggeration=20, max_sections=300):
    """Return connected 2D (axial_mm, exaggerated *radius_mm) surface profile.

    Works only on explicitly marked axial-feed continuous samples. Does not
    bridge invalid measurements or separate runs. Exaggeration is VISUAL ONLY.
    """
    factor=number(exaggeration)
    if factor is None or not (1 <= factor <= 100):
        raise ValueError('Exaggeration must be 1–100')
    if max_sections < 2:raise ValueError('max_sections must be >= 2')
    usable=[s for s in samples if s.axial_mm is not None and s.axial_run is not None]
    if not usable:return [],None
    # Most recent continuous feed segment only, do not silently join sessions.
    run=usable[-1].axial_run
    seq=[s for s in samples if s.axial_run==run]
    good=[s.f_direct_mm for s in seq if s.f_direct_mm is not None and s.f_direct_mm>0]
    if not good:return [],None
    baseline=sum(good)/len(good)
    segments=[];current=[]
    for s in seq:
        if s.f_direct_mm is None or s.f_direct_mm<=0 or s.axial_mm is None:
            if current:segments.append(current);current=[]
            continue
        displayed_radius=max(baseline*.2,baseline/2 + factor*(s.f_direct_mm-baseline)/2)
        current.append((s.axial_mm,displayed_radius,s.f_direct_mm))
    if current:segments.append(current)
    count=sum(map(len,segments))
    if count>max_sections:
        # De-emphasize detail to preserve render time but preserve gaps by segment.
        k=math.ceil(count/max_sections)
        segments=[v[::k]+([v[-1]] if len(v)>1 and v[-1] != v[::k][-1] else []) for v in segments]
    return segments,baseline
