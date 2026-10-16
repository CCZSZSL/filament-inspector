"""v14.6.8 experiment logs and unfiltered statistical diagnostics.

No measurement algorithms live here. All records are one row per acquisition
attempt; Pixel Only is a valid pixel observation but not an mm observation.
"""
from __future__ import annotations
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import statistics

VIEWS = ('left', 'right')
FIELDS = ['timestamp','attempt','test','capture_status','frame_status','failure_reason',
          'snapshot_bytes','camera_session','focus_epoch','settings_epoch','exposure_us',
          'left_y_px','left_pixel_width','left_diameter_mm','left_valid_columns','left_quality',
          'left_edge_fit_residual_px','left_calibration_status','left_position_mode','left_failure_reason',
          'right_y_px','right_pixel_width','right_diameter_mm','right_valid_columns','right_quality',
          'right_edge_fit_residual_px','right_calibration_status','right_position_mode','right_failure_reason',
          'f_direct_mm','delta_lr_mm','signed_lr_mm','f_direct_status','outlier_flags']


def finite(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError, OverflowError):
        return None


def new_row(attempt, name, *, timestamp=None):
    row = {k: '' for k in FIELDS}
    row.update(timestamp=timestamp or datetime.now().astimezone().isoformat(timespec='milliseconds'),
               attempt=attempt, test=name, capture_status='FAILED',frame_status='INVALID')
    return row


def extract_result(row, results, f_direct, failures=None):
    """Pull existing v14.6.7 robust fusion result without recomputing edges.

    mm fused pixel width = fused mm / scale (same scale was applied to all
    fusion widths); when Pixel Only the fusion result itself is pixel width.
    """
    failures=failures or {}
    for name in VIEWS:
        m = (results or {}).get(name) or {}
        prefix = name + '_'
        f = m.get('fusion') or {}
        row[prefix+'calibration_status'] = m.get('calibration_status', 'NOT MEASURED')
        row[prefix+'position_mode'] = (m.get('position_compensation') or {}).get('mode','')
        row[prefix+'failure_reason'] = failures.get(name,'') if not f else ''
        for dest,val in (
            ('y_px',m.get('tracking_center_y_px')),
            ('valid_columns',f.get('valid_columns')),
            ('quality',m.get('confidence_heuristic')),
            ('edge_fit_residual_px',f.get('edge_fit_residual_median_px'))):
            z=finite(val)
            if z is not None: row[prefix+dest]=int(z) if dest=='valid_columns' else z
        value=finite(f.get('value'))
        if value is None:continue
        if f.get('unit')=='px':
            row[prefix+'pixel_width']=value
        elif f.get('unit')=='mm':
            row[prefix+'diameter_mm']=value
            scale=finite(m.get('scale_mm_per_px'))
            if scale is not None and scale>0:
                row[prefix+'pixel_width']=value/scale
            else:
                row[prefix+'failure_reason']='missing positive scale to recover raw fused pixel width'
        else:
            row[prefix+'failure_reason']='unknown fusion unit: '+str(f.get('unit'))
    if f_direct:
        d=finite(f_direct.get('value'))
        if d is not None:
            row['f_direct_mm']=d
            delta=finite(f_direct.get('absolute_difference_mm'))
            if delta is not None:row['delta_lr_mm']=delta
    if finite(row['left_diameter_mm']) is not None and finite(row['right_diameter_mm']) is not None:
        row['signed_lr_mm']=float(row['left_diameter_mm'])-float(row['right_diameter_mm'])
    row['f_direct_status']='FUSED mm' if row['f_direct_mm']!='' else 'NOT AVAILABLE'
    both_px=all(row[n+'_pixel_width']!='' for n in VIEWS)
    both_mm=all(row[n+'_diameter_mm']!='' for n in VIEWS)
    row['frame_status']=('VALID_MM' if both_mm and row['f_direct_mm']!=''
                         else 'PIXEL_ONLY' if both_px else 'PARTIAL_OR_FAILED')
    if row['frame_status']!='VALID_MM':
        reasons=[f'{n}: {row[n+"_failure_reason"] or row[n+"_calibration_status"]}'
                 for n in VIEWS if row[n+'_diameter_mm']=='']
        row['failure_reason']=' | '.join(reasons) or 'F-Direct fusion unavailable'
    return row


def describe(values):
    nums=[finite(v) for v in values]
    x=[v for v in nums if v is not None]
    if not x:return dict(count=0,mean=None,std=None,min=None,max=None,range=None)
    return dict(count=len(x),mean=statistics.mean(x),
                std=statistics.stdev(x) if len(x)>=2 else None,
                min=min(x),max=max(x),range=max(x)-min(x))


def paired_correlation(rows,x_key,y_key):
    pts=[(finite(r.get(x_key)),finite(r.get(y_key))) for r in rows]
    pts=[(x,y) for x,y in pts if x is not None and y is not None]
    if len(pts)<3:return dict(count=len(pts),pearson_r=None,slope=None,intercept=None,r_squared=None,
                              note='At least three paired frames needed')
    x=[p[0] for p in pts];y=[p[1] for p in pts]
    mx=statistics.mean(x);my=statistics.mean(y)
    xx=sum((v-mx)**2 for v in x);yy=sum((v-my)**2 for v in y)
    if xx<=1e-16 or yy<=1e-16:
        return dict(count=len(pts),pearson_r=None,slope=None,intercept=None,r_squared=None,
                    note='Near-zero position or measurement variance; correlation undefined')
    xy=sum((u-mx)*(v-my) for u,v in pts)
    r=xy/math.sqrt(xx*yy)
    return dict(count=len(pts),pearson_r=r,slope=xy/xx,
                intercept=my-(xy/xx)*mx,r_squared=r*r,
                note='Exploratory Pearson; no significance claim; time/order confounding possible')


def outlier_flags(rows):
    """Robust median±3.5*MAD*1.4826 diagnostics; never remove rows.

    If MAD is exactly zero, nonzero differences are flagged explicitly.
    """
    signals=('left_pixel_width','right_pixel_width','left_diameter_mm',
             'right_diameter_mm','f_direct_mm','delta_lr_mm','signed_lr_mm')
    flags={int(r['attempt']):[] for r in rows}
    for name in signals:
        samples=[(int(r['attempt']),finite(r.get(name))) for r in rows]
        samples=[(k,v) for k,v in samples if v is not None]
        if len(samples)<5:continue
        med=statistics.median(v for _,v in samples)
        mad=statistics.median(abs(v-med) for _,v in samples)
        threshold=3.5*1.4826*mad
        for i,v in samples:
            if abs(v-med)>max(threshold,1e-12): flags[i].append(name)
    return flags


def analyse(rows,name,target,stopped=False):
    flags=outlier_flags(rows)
    stats={c:describe(r.get(c) for r in rows) for c in (
        'left_y_px','right_y_px','left_pixel_width','right_pixel_width',
        'left_diameter_mm','right_diameter_mm','f_direct_mm','delta_lr_mm','signed_lr_mm',
        'left_quality','right_quality','left_valid_columns','right_valid_columns')}
    correlations={}
    for n in VIEWS:
        for measure in (n+'_pixel_width',n+'_diameter_mm','f_direct_mm'):
            correlations[n+'_y_px__vs__'+measure]=paired_correlation(rows,n+'_y_px',measure)
    invalid=[{'attempt':int(r['attempt']),'status':r['frame_status'],
              'reason':r['failure_reason'] or r['capture_status']} for r in rows
             if r.get('frame_status')!='VALID_MM']
    flagged=[{'attempt':i,'signals':values} for i,values in flags.items() if values]
    mean_motion=stats['f_direct_mm']
    return {
       'schema':'filament_position_experiment_report_v1468', 'test':name,
       'target_frames':target,'attempted_frames':len(rows),
       'successful_snapshots':sum(r.get('capture_status')=='OK' for r in rows),
       'valid_mm_frames':sum(r.get('frame_status')=='VALID_MM' for r in rows),
       'valid_both_pixel_frames':sum(all(finite(r.get(n+'_pixel_width')) is not None for n in VIEWS) for r in rows),
       'stopped_early':bool(stopped),'stats':stats,'correlations':correlations,
       'invalid_frames':invalid,'outliers':flagged,
       'analysis_method':'All captured frames kept. Sample std (ddof=1); Pearson on finite paired observations. '
                         'MAD flags only, not removed. No physical reference traceability implied.',
       'note':'F-Direct is a VIRTUAL (Left+Right)/2, not a third camera view. '
              'Mirrored Y values should be examined separately; Y cannot uniquely determine depth.',
       'diameter_variation_mm':mean_motion['range'],
    }


class ExperimentLog:
    """Create CSV early; append+flush every attempt for crash-safe partial runs."""
    def __init__(self,output_dir,name,target,metadata):
        self.output_dir=Path(output_dir)
        self.output_dir.mkdir(parents=True,exist_ok=True)
        self.name=name;self.target=target;self.rows=[];self.closed=False
        self.csv_path=self.output_dir/'frames.csv'
        self.json_path=self.output_dir/'report.json'
        self.text_path=self.output_dir/'report.txt'
        self.metadata_path=self.output_dir/'metadata.json'
        self.metadata_path.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
        self.fp=self.csv_path.open('w',newline='',encoding='utf-8')
        self.writer=csv.DictWriter(self.fp,fieldnames=FIELDS,extrasaction='ignore')
        self.writer.writeheader();self.fp.flush()
    def append(self,row):
        if self.closed:raise RuntimeError('experiment log already closed')
        self.writer.writerow(row);self.fp.flush()
        self.rows.append(dict(row))
    def finish(self,stopped=False):
        if not self.closed:
            self.fp.flush();self.fp.close();self.closed=True
        result=analyse(self.rows,self.name,self.target,stopped)
        flags=outlier_flags(self.rows)
        # Export flags to a separate traceable file without changing source CSV.
        with (self.output_dir/'outlier_flags.csv').open('w',newline='',encoding='utf-8') as fp:
            writer=csv.writer(fp);writer.writerow(['attempt','flagged_signals'])
            for r in self.rows:
                writer.writerow([r['attempt'],'; '.join(flags[int(r['attempt'])])])
        self.json_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        self.text_path.write_text(format_report(result),encoding='utf-8')
        return result


def format_report(r):
    lines=[f'Position-Induced Error Analysis v14.6.8 — {r["test"].upper()}',
           f'Attempts: {r["attempted_frames"]}/{r["target_frames"]}; stopped early: {r["stopped_early"]}',
           f'Snapshots OK: {r["successful_snapshots"]}; valid fused mm frames: {r["valid_mm_frames"]}; '
           f'valid both-mirror pixel frames: {r["valid_both_pixel_frames"]}',
           '', 'REPEATABILITY / VARIATION — unfiltered, sample standard deviation',
           'Metric                       N          Mean           Std         Range']
    for n,s in r['stats'].items():
        def f(x):return f'{x:.6f}' if x is not None else 'n/a'
        lines.append(f'{n:26s} {s["count"]:4d} {f(s["mean"]):>13} {f(s["std"]):>13} {f(s["range"]):>13}')
    lines+=['','Y-Position Correlations (r, slope per Y pixel; exploratory, no filtering)']
    for k,v in r['correlations'].items():
        lines.append(f'{k}: N={v["count"]}, r={v["pearson_r"]}, slope={v["slope"]}')
    lines += ['',f'Invalid / Pixel Only frames: {len(r["invalid_frames"])}']
    for v in r['invalid_frames']:lines.append(f'  #{v["attempt"]}: {v["status"]}: {v["reason"]}')
    lines += ['',f'Flagged outlier frames: {len(r["outliers"])} (NOT REMOVED)']
    for v in r['outliers']:lines.append(f'  #{v["attempt"]}: {", ".join(v["signals"])}')
    lines+=['',r['analysis_method'],r['note'],'']
    return '\n'.join(lines)
