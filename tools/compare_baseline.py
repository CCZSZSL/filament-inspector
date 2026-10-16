"""Compare deterministic measurements against a Git baseline in separate processes.

Run from the repository root: python tools/compare_baseline.py
Requires the original commit in local Git history, but no camera or GUI window.
Synthetic equivalence does not establish physical measurement accuracy.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

PROBE = r'''
import json, numpy as np, cv2
from IMPORT_VIEWS import new_view, run_metrology
from IMPORT_SEED import sample_record, build_model
from IMPORT_DYNAMIC import measure_dynamic

def plain(value):
    if isinstance(value, np.ndarray): return plain(value.tolist())
    if isinstance(value, np.generic): return plain(value.item())
    if isinstance(value, dict): return {k:plain(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [plain(v) for v in value]
    if isinstance(value, float) and not np.isfinite(value): return str(value)
    return value

h,w=225,95
bg=np.full((h,w),114,np.uint8)
background=cv2.cvtColor(bg,cv2.COLOR_GRAY2BGR)
box=[20,12,59,208]
def frame(offset=0,angle=0):
    raw=bg.copy()
    for x in range(w):
        y=int(round(72+offset+angle*(x-38)))
        raw[y:y+62,x]=58
        raw[y+18:y+28,x]=156
        raw[y+45:y+49,x]=139
    return cv2.cvtColor(cv2.GaussianBlur(raw,(5,5),0),cv2.COLOR_GRAY2BGR)

results=[]
for name in ['direct','left','right']:
 for calibrated in [False,True]:
  for offset in [-32,0,32]:
   for angle in [0,.12]:
    v=new_view(name)
    v.update(search_roi_xyxy=box,approx_expected_width_px=62.,foreground_threshold=11.)
    v['seed_gray_model']=build_model([sample_record(frame(),[26,84,40,92],box)],image_shape=(h,w))
    if calibrated:
     if name=='direct':
      v['profile']={'local_planar_calibration':{'original_image_width_px':h,'original_image_height_px':w,'homography_calibration_view_px_to_mm':[[.028,0,0],[0,.028,0],[0,0,1]]},'target_plane_reference_correction':{'enabled':True,'correction_factor_K_ref':1.02}}
     else:
      v['local_scale_calibration']={'original_image_size_wh':[h,w],'search_roi_center_xy':[39.5,103.],'mm_per_px':.029}
    try:
     found,m,e=measure_dynamic(frame(offset,angle),background,(w,h),v)
     result={'measurement':m,'center_y':found['center_y'],'widths':e.widths_px,'valid':e.valid}
    except Exception as exc: result={'error':type(exc).__name__,'message':str(exc)}
    results.append(plain(result))

# Exercise the extracted original gradient/50% engine, not just seeded fusion.
for polarity,inside in [('bright',190),('dark',40)]:
 for width in [48,62,75]:
  raw=bg.copy();raw[72:72+width]=inside
  current=cv2.cvtColor(cv2.GaussianBlur(raw,(5,5),0),cv2.COLOR_GRAY2BGR)
  m,e=run_metrology(current,(w,h),[20,40,59,180],None,polarity)
  results.append(plain({'measurement':m,'widths':e.widths_px,'valid':e.valid}))
try:
 measure_dynamic(background,background,(w,h),v)
except Exception as exc:
 results.append({'error':type(exc).__name__,'message':str(exc)})
else: raise AssertionError('Blank frame unexpectedly accepted')
print(json.dumps(results,sort_keys=True,allow_nan=False))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',default='d7a953decb0363b63acaccee0b1978af9b328a44')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    paths=subprocess.check_output(['git','ls-tree','--name-only',args.baseline],cwd=root,text=True).splitlines()
    def run(directory,modern):
        modules=(['filament_inspector.algorithms.views','filament_inspector.algorithms.seed','filament_inspector.algorithms.dynamic'] if modern else ['filament_three_view_core_v13','filament_seed_guided_v1462','filament_dynamic_fusion_v1463'])
        script=PROBE
        for token,module in zip(['IMPORT_VIEWS','IMPORT_SEED','IMPORT_DYNAMIC'],modules):script=script.replace(token,module)
        env={**os.environ,'PYTHONPATH':str(directory)}
        output=subprocess.check_output([sys.executable,'-X','utf8','-c',script],cwd=directory,env=env,text=True,encoding='utf-8')
        return json.loads(output)
    with tempfile.TemporaryDirectory(prefix='filament-baseline-') as tmp:
        old_root=Path(tmp)
        for name in paths:
            if name.endswith('.py'):
                (old_root/name).write_bytes(subprocess.check_output(['git','show',args.baseline+':'+name],cwd=root))
        old=run(old_root,False)
        new=run(root,True)
    if old!=new:
        bad=[i for i,(a,b) in enumerate(zip(old,new)) if a!=b]
        raise AssertionError(f'Baseline mismatch at cases {bad}; lengths {len(old)}/{len(new)}')
    print(f'{len(new)} deterministic cases match the baseline exactly (values, validity and errors).')


if __name__=='__main__':
    main()
