'use strict';
// Offline, read-only charting: browser receives snapshots from Mac's Python HTTP server.
const $=id=>document.getElementById(id);
const PAGES={overview:['LIVE QUALITY MONITORING','Measurement Overview','Live diameter, mirror agreement and validity at a glance.'],diameter:['STATISTICAL MEASUREMENT','Diameter Analysis','Unfiltered diameter distribution, limits and moving range.'],mirrors:['POSITION & GEOMETRY','Mirror & Position','Independent left/right views, positional trends and calibration status.'],profile:['AXISYMMETRIC VISUALIZATION','3D Diameter Profile','Interactive nominal-circle model; not surface roughness or roundness.'],experiments:['STATIC / MOTION','Measurement Experiments','Local Mac controls experiment acquisition; browsers only monitor results.']};
let currentPage='overview', state={measurements:[],mode:'idle',experiment:{},limits:{}}, updating=false;
let latestRemoteState=null, displayPaused=false, sampleWindow='120', motionEnabled=!window.matchMedia('(prefers-reduced-motion: reduce)').matches;
let orbitEnabled=false, orbitLast=0, lastSamples=-1;const plotGeometry=new Map();
const chartRows=()=>sampleWindow==='all'?(state.measurements||[]):(state.measurements||[]).slice(-Number(sampleWindow));
let target=1.75,tolerance=.05, yaw=.63,pitch=.34,zoom=1;
const num=x=>{if(x===null||x===undefined||x==='')return null;let n=Number(x);return Number.isFinite(n)?n:null};
const valid=(r,key)=>num(r[key]);
const fmt=(x,d=4)=>x===null||!Number.isFinite(x)?'—':x.toFixed(d);
const mean=xs=>xs.length?xs.reduce((a,b)=>a+b,0)/xs.length:null;
const std=xs=>xs.length>1?Math.sqrt(xs.reduce((a,x)=>a+(x-mean(xs))**2,0)/(xs.length-1)):null;
const colors={left:'#69aaff',right:'#ffc277',f_direct:'#37e5ba',red:'#ff738d',purple:'#bd9cff'};
const isLight=()=>document.body.classList.contains('light-theme');
function applyPalette(){Object.assign(colors,isLight()?{left:'#337ac3',right:'#ba731d',f_direct:'#118d7b',red:'#d74b5a',purple:'#7858bf'}:{left:'#69aaff',right:'#ffc277',f_direct:'#37e5ba',red:'#ff738d',purple:'#bd9cff'});}
function setText(id,value){let x=$(id);if(x)x.textContent=value}
function getCanvas(id){let c=$(id);if(!c)return null;const ratio=Math.min(window.devicePixelRatio||1,2),w=Math.max(100,c.clientWidth),h=Math.max(100,c.clientHeight);c.width=Math.round(w*ratio);c.height=Math.round(h*ratio);const ctx=c.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);return {c,ctx,w,h}}
function clean(ctx,w,h,dark=false){const g=ctx.createLinearGradient(0,0,w,h);if(dark){g.addColorStop(0,'#081827');g.addColorStop(1,'#102b3d')}else if(isLight()){g.addColorStop(0,'#fff');g.addColorStop(1,'#f4faff')}else{g.addColorStop(0,'#102535');g.addColorStop(1,'#0b1c2a')}ctx.fillStyle=g;ctx.fillRect(0,0,w,h)}
function empty(ctx,w,h,text='Waiting for measurements'){ctx.fillStyle=isLight()?'#788b9c':'#85a8bd';ctx.font='13px system-ui';ctx.textAlign='center';ctx.fillText(text,w/2,h/2);ctx.textAlign='left'}
function bounds(xs,fallback=1){if(!xs.length)return [0,fallback];let lo=Math.min(...xs),hi=Math.max(...xs);const pad=hi===lo?Math.max(Math.abs(lo)*.005,.0005):(hi-lo)*.12;return [lo-pad,hi+pad]}
function axes(ctx,w,h,minX,maxX,minY,maxY,unitX,unitY){let l=57,t=15,r=w-16,b=h-34;
 ctx.lineWidth=1;ctx.font='10px system-ui';ctx.fillStyle=isLight()?'#6c8295':'#83a4b8';ctx.textAlign='right';
 for(let i=0;i<=4;i++){let yy=t+i*(b-t)/4,v=maxY-i*(maxY-minY)/4;ctx.strokeStyle=isLight()?'#e1ecf2':'#274154';ctx.beginPath();ctx.moveTo(l,yy);ctx.lineTo(r,yy);ctx.stroke();ctx.fillText(fmt(v,(Math.abs(v)<10&&Math.abs(maxY-minY)<1)?4:1),l-7,yy+3)}
 ctx.textAlign='left';ctx.fillText(fmt(minX,1),l,b+17);ctx.textAlign='right';ctx.fillText(fmt(maxX,1),r,b+17);ctx.textAlign='left';ctx.fillText(unitY||'',l,10);ctx.textAlign='center';ctx.fillText(unitX||'',(l+r)/2,h-3);
 return {l,t,r,b,x:x=>l+(x-minX)/(maxX-minX||1)*(r-l),y:y=>b-(y-minY)/(maxY-minY||1)*(b-t)};
}
function timePlot(id,series,{lines=[],unitY='mm'}={}){
 let q=getCanvas(id);if(!q)return;let {ctx,w,h}=q;clean(ctx,w,h);
 const rows=chartRows();let xx=rows.map((s,i)=>num(s.elapsed_s)??i);let yy=rows.flatMap(s=>series.map(k=>{let v=num(s[k.key]);return v===null?null:v*(k.factor||1)}).filter(v=>v!==null));
 yy.push(...lines.map(x=>x.value));if(!rows.length||!yy.length){empty(ctx,w,h);return}
 const [lo,hi]=bounds(yy);let a=axes(ctx,w,h,Math.min(...xx),Math.max(...xx)+1e-6,lo,hi,'elapsed s',unitY);plotGeometry.set(id,{rows,xx,a,series,unitY});
 for(const marker of lines){ctx.strokeStyle=marker.color||colors.red;ctx.setLineDash([6,5]);ctx.beginPath();ctx.moveTo(a.l,a.y(marker.value));ctx.lineTo(a.r,a.y(marker.value));ctx.stroke()}ctx.setLineDash([]);
 for(const k of series){ctx.strokeStyle=k.color;ctx.lineWidth=k.key==='f_direct_mm'?2.3:1.55;ctx.shadowColor=k.color;ctx.shadowBlur=isLight()?0:6;ctx.beginPath();let drawing=false,lastPoint=null;
 for(let i=0;i<rows.length;i++){const v=num(rows[i][k.key]);const x=xx[i];if(v===null){drawing=false;continue}const px=a.x(x),py=a.y(v*(k.factor||1));if(!drawing){ctx.moveTo(px,py);drawing=true}else ctx.lineTo(px,py);lastPoint=[px,py]}ctx.stroke();ctx.shadowBlur=0;if(lastPoint){ctx.fillStyle=k.color;ctx.beginPath();ctx.arc(lastPoint[0],lastPoint[1],2.7,0,2*Math.PI);ctx.fill()}}
}
function scatter(id,pairs,{unitX='mm',unitY='mm',identity=false,lines=[]}={}){
 let q=getCanvas(id);if(!q)return;let {ctx,w,h}=q;clean(ctx,w,h);pairs=pairs.filter(([x,y])=>num(x)!==null&&num(y)!==null);
 if(!pairs.length){empty(ctx,w,h);return}
 const [xmin,xmax]=bounds(pairs.map(p=>p[0]).concat(identity?pairs.map(p=>p[1]):[]));const [ymin,ymax]=bounds(pairs.map(p=>p[1]).concat(identity?pairs.map(p=>p[0]):[]));let a=axes(ctx,w,h,xmin,xmax,ymin,ymax,unitX,unitY);
 if(identity){ctx.strokeStyle=isLight()?'#96aab9':'#5e839c';ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(a.x(xmin),a.y(xmin));ctx.lineTo(a.x(xmax),a.y(xmax));ctx.stroke();ctx.setLineDash([])}
 for(const m of lines){ctx.strokeStyle=m.color||colors.red;ctx.setLineDash([5,3]);ctx.beginPath();ctx.moveTo(a.l,a.y(m.y));ctx.lineTo(a.r,a.y(m.y));ctx.stroke()}ctx.setLineDash([]);
 for(const [x,y,c] of pairs){ctx.fillStyle=c||colors.left;ctx.globalAlpha=.62;ctx.beginPath();ctx.arc(a.x(x),a.y(y),2.6,0,Math.PI*2);ctx.fill()}ctx.globalAlpha=1;
}
function histogram(id){let q=getCanvas(id);if(!q)return;let {ctx,w,h}=q;clean(ctx,w,h);
 const xs=(state.measurements||[]).map(s=>valid(s,'f_direct_mm')).filter(x=>x!==null);if(!xs.length){empty(ctx,w,h);return}
 let min=Math.min(...xs),max=Math.max(...xs),bins=Math.min(24,Math.max(8,Math.ceil(Math.sqrt(xs.length))));if(min===max){min-=.0005;max+=.0005}const widths=(max-min)/bins,counts=Array(bins).fill(0);for(const x of xs){counts[Math.min(bins-1,Math.floor((x-min)/widths))]++}
 let a=axes(ctx,w,h,min,max,0,Math.max(2,...counts)*1.1,'diameter mm','count');const bw=(a.r-a.l)/bins;
 counts.forEach((cnt,i)=>{ctx.fillStyle=colors.left;ctx.fillRect(a.l+i*bw+1,a.y(cnt),Math.max(1,bw-2),a.b-a.y(cnt))});
 let avg=mean(xs);ctx.strokeStyle=colors.red;ctx.setLineDash([4,3]);ctx.beginPath();ctx.moveTo(a.x(avg),a.t);ctx.lineTo(a.x(avg),a.b);ctx.stroke();ctx.setLineDash([]);
}
function movingRange(){let q=getCanvas('movingRange');if(!q)return;let {ctx,w,h}=q;clean(ctx,w,h);const rows=chartRows();let xs=[],ys=[];
 for(let i=1;i<rows.length;i++){let a=num(rows[i-1].f_direct_mm),b=num(rows[i].f_direct_mm);if(a!==null&&b!==null&&rows[i].source===rows[i-1].source){xs.push(i);ys.push(Math.abs(b-a)*1000)}}
 if(!ys.length){empty(ctx,w,h);return}let a=axes(ctx,w,h,0,rows.length,0,Math.max(1,...ys)*1.15,'sample index','μm');ctx.lineWidth=1.8;ctx.strokeStyle=colors.purple;ctx.beginPath();xs.forEach((x,i)=>i?ctx.lineTo(a.x(x),a.y(ys[i])):ctx.moveTo(a.x(x),a.y(ys[i])));ctx.stroke();
}
function displayStats(){let rows=state.measurements||[], vals=rows.map(s=>num(s.f_direct_mm)).filter(x=>x!==null);let avg=mean(vals),sd=std(vals);
 let last=[...rows].reverse().find(r=>num(r.f_direct_mm)!==null);let latest=last?num(last.f_direct_mm):null,dr=rows.length?num(rows[rows.length-1].delta_lr_mm):null;
 $('latestDiameter').innerHTML=fmt(latest,5)+'<span> mm</span>';$('meanDiameter').innerHTML=fmt(avg,5)+'<span> mm</span>';
 $('mirrorDifference').innerHTML=fmt(dr===null?null:dr*1000,2)+'<span> μm</span>';$('diameterStd').innerHTML=fmt(sd===null?null:sd*1000,2)+'<span> μm</span>';
 $('validFraction').innerHTML=vals.length+'<span> / '+rows.length+'</span>';
 let passed=latest===null?null:Math.abs(latest-target)<=tolerance;setText('toleranceStatus',passed===null?'WAITING':passed?'PASS':'OUT OF TOL');$('toleranceStatus').className='kpi-value status-value '+(passed===null?'':passed?'ok':'bad');
 let latestContinuous=rows.filter(r=>r.source==='continuous').slice(-20),fps=null;
 if(latestContinuous.length>1){let t0=num(latestContinuous[0].elapsed_s),t1=num(latestContinuous[latestContinuous.length-1].elapsed_s);if(t0!==null&&t1!==null&&t1>t0)fps=(latestContinuous.length-1)/(t1-t0)}
 setText('webFPS',fps===null?'—':fps.toFixed(2)+' FPS');
 setText('sampleCount',rows.length);setText('lastUpdate',state.updated_at?new Date(state.updated_at).toLocaleTimeString():'—');setText('calibrationStatus',state.calibration||'—');
 let stats=[['Valid mm',vals.length],['Mean (mm)',fmt(avg,5)],['Std (μm)',fmt(sd===null?null:sd*1000,2)],['Minimum (mm)',fmt(vals.length?Math.min(...vals):null,5)],['Maximum (mm)',fmt(vals.length?Math.max(...vals):null,5)],['Range (μm)',fmt(vals.length?(Math.max(...vals)-Math.min(...vals))*1000:null,2)]];
 $('diameterStats').innerHTML=stats.map(([k,v])=>'<div class="stat"><span>'+k+'</span><strong>'+v+'</strong></div>').join('');
 setText('ribbonFPS',fps===null?'— FPS':fps.toFixed(2)+' FPS');setText('ribbonFrames',rows.length+' FRAMES');setText('streamSidebar',rows.length?'STREAM / '+rows.length+' FRAMES':'AWAITING DATA');
 document.querySelector('.sidebar-telemetry')?.classList.toggle('idle',rows.length===0);
}
function mirrorStats(){let rows=state.measurements||[],both=rows.filter(s=>num(s.left_mm)!==null&&num(s.right_mm)!==null);
 scatter('mirrorScatter',both.map(s=>[num(s.left_mm),num(s.right_mm)]),{identity:true});
 const differences=both.map(s=>(s.left_mm-s.right_mm)*1000),bias=mean(differences),sd=std(differences);
 scatter('blandAltman',both.map(s=>[(s.left_mm+s.right_mm)/2,(s.left_mm-s.right_mm)*1000,colors.amber]),{unitY:'L - R μm',lines:bias===null?[]:[{y:bias,color:colors.blue},...(sd===null?[]:[{y:bias+1.96*sd,color:colors.red},{y:bias-1.96*sd,color:colors.red}])]});
 let pairs=[];for(let s of rows){for(let view of ['left','right']){let x=num(s[view+'_y_px']),y=num(s[view+'_mm']);if(x!==null&&y!==null)pairs.push([x,y,colors[view]])}}
 scatter('positionScatter',pairs,{unitX:'mirror Y (px)',unitY:'diameter mm'});
 let infos=['left','right'].map(view=>{let vals=rows.filter(s=>num(s[view+'_y_px'])!==null),pixel=rows.filter(s=>num(s[view+'_pixel_width'])!==null);
 let last=vals.length?vals[vals.length-1]:{},ncal=vals.filter(s=>num(s[view+'_mm'])===null).length;
 return '<div><strong>'+view.toUpperCase()+' MIRROR</strong><span>Y = '+fmt(num(last[view+'_y_px']),1)+' px · '+ncal+' non-mm / '+vals.length+' tracked</span><span>Pixel widths available: '+pixel.length+'</span><span>Valid columns: '+(num(last[view+'_valid_columns'])??'—')+' / 40</span><span>'+safeText(last[view+'_calibration_status']||'No calibration status')+'</span></div>'}).join('');
 $('calibrationDetails').innerHTML=infos;
}
function safeText(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function heatMap(){
 const q=getCanvas('edgeHeatmap');if(!q)return;const {ctx,w,h}=q;clean(ctx,w,h);
 const view=$('heatView').value, mode=$('heatMode').value, markRejected=$('heatRejected').checked;
 const rawRows=chartRows().filter(s=>Array.isArray(s['edge_columns_'+view])).slice(-150);
 if(!rawRows.length){empty(ctx,w,h,'Waiting for measured 40-column widths');return}
 const rows=rawRows.map(s=>(s['edge_columns_'+view]||[]).slice(0,40).map(num));
 const finite=rows.flat().filter(x=>x!==null);if(!finite.length){empty(ctx,w,h,'No valid widths');return}
 const sorted=arr=>[...arr].sort((a,b)=>a-b);
 const median=arr=>{if(!arr.length)return null;const a=sorted(arr),i=Math.floor(a.length/2);return a.length%2?a[i]:(a[i-1]+a[i])/2};
 const global=median(finite), rowMedians=rows.map(r=>median(r.filter(x=>x!==null)));
 const reference=Array.from({length:40},(_,col)=>median(rows.slice(0,5).map(r=>r[col]).filter(x=>x!==null)));
 let numbers=rows.map((r,i)=>r.map((v,j)=>v===null?null:(mode==='raw'?v-global:mode==='local'?v-(rowMedians[i]??v):reference[j]===null?null:v-reference[j])));
 const scalemode=$('heatScale').value;
 const absValues=numbers.flat().filter(v=>v!==null).map(Math.abs);
 const range=scalemode==='auto'?Math.max(.001,...absValues):Number(scalemode);
 const l=60,t=23,r=w-16,b=h-37,dx=(r-l)/40,dy=(b-t)/rows.length;
 window.heatHoverData={rawRows,rows,numbers,mode,view,range,l,t,r,b,dx,dy};
 numbers.forEach((row,i)=>row.forEach((v,j)=>{
   if(v===null)return;
   let z=Math.max(-1,Math.min(1,v/range));
   ctx.fillStyle=z<0?`hsl(205 73% ${83+z*47}%)`:`hsl(9 73% ${88-z*48}%)`;
   ctx.fillRect(l+j*dx,t+i*dy,Math.ceil(dx),Math.ceil(dy));
   if(markRejected){
     const accepted=rawRows[i]['edge_accepted_'+view];
     const colValid=Array.isArray(rawRows[i]['edge_columns_'+view])&&num(rawRows[i]['edge_columns_'+view][j])!==null;
     if(colValid && Array.isArray(accepted) && !accepted.includes(j)){
       ctx.strokeStyle=isLight()?'rgba(23,38,53,.85)':'rgba(240,249,255,.85)';ctx.lineWidth=Math.max(1,Math.min(2,dy/3));
       const x=l+(j+.1)*dx,y=t+(i+.15)*dy;
       ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x+dx*.75,y+dy*.65);ctx.stroke();
     }
   }
 }));
 // Legend and explicit zero-center; the colors never stand for measured roughness.
 const legendY=h-13,legendX=l+95,legendW=Math.min(230,r-legendX);
 for(let j=0;j<70;j++){
   const z=(j/69)*2-1;ctx.fillStyle=z<0?`hsl(205 73% ${83+z*47}%)`:`hsl(9 73% ${88-z*48}%)`;
   ctx.fillRect(legendX+j*legendW/70,legendY-8,Math.ceil(legendW/70),7);
 }
 ctx.font='11px system-ui';ctx.fillStyle=isLight()?'#647f8e':'#85a6b9';ctx.fillText('col 0',l,b+15);
 ctx.textAlign='right';ctx.fillText('col 39',r,b+15);ctx.textAlign='left';
 ctx.fillText('oldest',5,t+10);ctx.fillText('latest ↓',5,b-2);
 ctx.fillText(`−${range.toFixed(2)} px`,l,legendY-1);
 ctx.fillText(`+${range.toFixed(2)} px`,legendX+legendW+5,legendY-1);
 let label=mode==='raw'?'global median':mode==='local'?'per-frame median':'first 5 frames per column';
 setText('heatInfo',`Original 40-column pixel-width deviations relative to ${label}. ${rows.length} frames; 0 means reference width. Fixed ±${range.toFixed(2)} px color range. Diagonal marks indicate fusion-rejected columns. Blank = missing data. NOT surface roughness.`);
}
function experimentPage(){let exp=state.experiment||{},rows=state.measurements||[],name=exp.name||state.mode||'idle';setText('expMode',String(name).toUpperCase());
 setText('expProgress',exp.target?`${exp.attempts||0} / ${exp.target}`:'—');setText('expValid',rows.filter(s=>num(s.f_direct_mm)!==null).length+' / '+rows.length);
 setText('expText',exp.name?`${exp.status||''} · ${exp.progress||''}`:'Start Static 50-frame or Motion 100-frame test in the Mac calibration window. Existing CSV exports remain on the Mac.');
 $('expBar').style.width=exp.target?Math.min(100,(exp.attempts||0)*100/exp.target)+'%':'0%';
 $('sampleTable').innerHTML=(rows.slice(-24).reverse().map(s=>'<tr><td>'+safeText((s.timestamp||'').replace('T',' ').slice(11,23))+'</td><td>'+safeText(s.source||'')+'</td><td>'+fmt(num(s.f_direct_mm),5)+'</td><td>'+fmt(num(s.left_y_px),1)+'</td><td>'+fmt(num(s.right_y_px),1)+'</td><td>'+fmt(num(s.delta_lr_mm)===null?null:s.delta_lr_mm*1000,2)+'</td><td>'+safeText(s.status||'')+'</td></tr>').join('')||'<tr><td colspan="7">Waiting for measurements</td></tr>');
}
function mesh(){let q=getCanvas('meshCanvas');if(!q)return;let {ctx,w,h}=q;clean(ctx,w,h,true);let rows=(state.measurements||[]).filter(s=>num(s.axial_mm)!==null&&num(s.axial_run)!==null);
 if(rows.length<2){empty(ctx,w,h,'Enable actual axial feed on Mac and run continuous measurement.');setText('meshStats','No axial profile. Static tests, lateral-motion tests and CSV imports cannot become a physical length.');return}
 const run=rows[rows.length-1].axial_run;rows=(state.measurements||[]).filter(s=>s.axial_run===run);let good=rows.filter(s=>num(s.f_direct_mm)!==null&&s.f_direct_mm>0);
 if(good.length<2){empty(ctx,w,h,'Not enough valid fused-mm axial samples.');return}
 let avg=mean(good.map(s=>s.f_direct_mm)),dmin=Math.min(...good.map(s=>s.f_direct_mm)),dmax=Math.max(...good.map(s=>s.f_direct_mm)),zmin=Math.min(...good.map(s=>s.axial_mm)),zmax=Math.max(...good.map(s=>s.axial_mm)),factor=Number($('radialFactor').value)||20;
 let spans=[];let part=[];for(const r of rows){if(num(r.f_direct_mm)===null){if(part.length)spans.push(part);part=[]}else part.push(r)}if(part.length)spans.push(part);
 let polygons=[];const n=20,major=Math.max(.0001,dmax-dmin),c=Math.cos(yaw),s=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
 const transform=(z,rad,ang)=>{let X=(z-(zmin+zmax)/2)/Math.max(1,zmax-zmin)*4.8,Y=rad*Math.cos(ang),Z=rad*Math.sin(ang);
 // rotation around filament's axial X; then pitched in camera coords
 let yy=Y*c-Z*s,zz=Y*s+Z*c;let xx=X*cp-zz*sp,depth=X*sp+zz*cp;
 const perspective=zoom*Math.min(w/7.2,h/3.6)/(1+Math.max(-1.6,Math.min(1.6,depth))*.11);return [w/2+xx*perspective,h/2+yy*perspective,depth]};
 const radius=r=>Math.max(.10,.47+((r.f_direct_mm-avg)/2)*factor/.87);
 for(const part0 of spans){const stride=Math.max(1,Math.ceil(part0.length/240)),pts=part0.filter((_,i)=>i%stride===0);if(pts.length>1&&pts[pts.length-1]!==part0[part0.length-1])pts.push(part0[part0.length-1]);for(let i=0;i<pts.length-1;i++){
 let a=pts[i],b=pts[i+1];for(let j=0;j<n;j++){let p1=transform(a.axial_mm,radius(a),2*Math.PI*j/n),p2=transform(a.axial_mm,radius(a),2*Math.PI*(j+1)/n),p3=transform(b.axial_mm,radius(b),2*Math.PI*(j+1)/n),p4=transform(b.axial_mm,radius(b),2*Math.PI*j/n);
 polygons.push({p:[p1,p2,p3,p4],depth:(p1[2]+p2[2]+p3[2]+p4[2])/4,d:(a.f_direct_mm+b.f_direct_mm)/2});}}}
 polygons.sort((a,b)=>b.depth-a.depth);
 for(const p of polygons){const t=Math.max(-1,Math.min(1,(p.d-avg)/(major/2||1)));ctx.fillStyle=t>.15?`hsl(${22-t*19} 68% ${53-Math.abs(t)*13}%)`:t<-.15?`hsl(205 70% ${50+t*8}%)`:'#219e9b';ctx.globalAlpha=.91;ctx.beginPath();ctx.moveTo(p.p[0][0],p.p[0][1]);for(let i=1;i<4;i++)ctx.lineTo(p.p[i][0],p.p[i][1]);ctx.closePath();ctx.fill();ctx.strokeStyle='rgba(220,240,250,.13)';ctx.stroke()};ctx.globalAlpha=1;
 let intervals=good.slice(1).map((s,i)=>s.axial_mm-good[i].axial_mm).filter(x=>x>0).sort((a,b)=>a-b),med=intervals.length?intervals[Math.floor(intervals.length/2)]:null;
 setText('meshStats',`Estimated span ${fmt(zmax-zmin,1)} mm · nominal circular cross sections · mean diameter ${fmt(avg,5)} mm · radial deviation visually ×${factor} · median valid sample pitch ${fmt(med,2)} mm · ${good.length} valid samples. Unmeasured gaps not reconstructed.`);
}
function render(){applyPalette();displayStats();renderSparklines();if(currentPage==='overview'){timePlot('overviewTrend',[{key:'left_mm',color:colors.left},{key:'right_mm',color:colors.right},{key:'f_direct_mm',color:colors.f_direct}],{lines:[{value:target+tolerance,color:colors.red},{value:target-tolerance,color:colors.red}]});histogram('overviewHistogram');timePlot('overviewSigned',[{key:'signed_lr_mm',factor:1000,color:colors.red}],{unitY:'μm'})}
 else if(currentPage==='diameter'){timePlot('diameterTrend',[{key:'left_mm',color:colors.left},{key:'right_mm',color:colors.right},{key:'f_direct_mm',color:colors.f_direct}],{lines:[{value:target+tolerance,color:colors.red},{value:target-tolerance,color:colors.red}]});histogram('diameterHistogram');movingRange()}
 else if(currentPage==='mirrors'){mirrorStats();timePlot('positionTrend',[{key:'left_y_px',color:colors.left},{key:'right_y_px',color:colors.right}],{unitY:'px'});heatMap()}
 else if(currentPage==='profile')mesh();else if(currentPage==='experiments')experimentPage();
}
async function poll(){if(updating)return;updating=true;try{const response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);latestRemoteState=await response.json();setText('networkFlag','CONNECTED');$('networkFlag').className='pill online';setText('footerStatus','Mac read-only measurement service online');setText('modeFlag',(latestRemoteState.mode||'idle').toUpperCase());$('modeFlag').className='pill';setText('clockFlag',new Date().toLocaleTimeString());if(!displayPaused){const n=(latestRemoteState.measurements||[]).length;state=latestRemoteState;render();if(n!==lastSamples){lastSamples=n;samplePulse()}}}
 catch(e){setText('networkFlag','DISCONNECTED');$('networkFlag').className='pill offline';setText('footerStatus','Connection lost; data is not being refreshed');setText('clockFlag',String(e.message))}finally{updating=false}}
function changePage(page){currentPage=page;document.querySelectorAll('.page').forEach(e=>e.classList.toggle('active',e.id===page));document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('active',e.dataset.page===page));const [category,title,sub]=PAGES[page];setText('pageCategory',category);setText('pageTitle',title);setText('pageSubtitle',sub);render()}
document.querySelectorAll('.nav').forEach(b=>b.addEventListener('click',()=>changePage(b.dataset.page)));
function setLimits(){let a=num($('targetInput').value),b=num($('toleranceInput').value);if(a!==null&&a>0&&b!==null&&b>0){target=a;tolerance=b;render()}}
$('targetInput').addEventListener('input',setLimits);$('toleranceInput').addEventListener('input',setLimits);
$('radialFactor').addEventListener('change',mesh);for(const id of ['heatView','heatMode','heatScale','heatRejected'])$(id).addEventListener('change',heatMap);
$('resetCamera').addEventListener('click',()=>{yaw=.63;pitch=.34;zoom=1;mesh()});
const meshEl=$('meshCanvas');let dragging=false,lastX=0,lastY=0;meshEl.addEventListener('pointerdown',e=>{dragging=true;lastX=e.clientX;lastY=e.clientY;meshEl.setPointerCapture(e.pointerId)});meshEl.addEventListener('pointerup',()=>dragging=false);meshEl.addEventListener('pointercancel',()=>dragging=false);
meshEl.addEventListener('pointermove',e=>{if(!dragging)return;yaw+=(e.clientX-lastX)*.012;pitch=Math.max(-1.5,Math.min(1.5,pitch+(e.clientY-lastY)*.008));lastX=e.clientX;lastY=e.clientY;mesh()});
meshEl.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(.45,Math.min(3,zoom*Math.exp(-e.deltaY*.001)));mesh()},{passive:false});
window.addEventListener('resize',()=>render());setInterval(poll,1200);poll();


// -------- v14.7.2 interactive display-only enhancements (no camera controls) -----
function renderSparklines(){
 const source=chartRows();
 document.querySelectorAll('.kpi[data-spark]').forEach((card,i)=>{
   let svg=card.querySelector('.kpi-spark');if(!svg){svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 140 24');svg.setAttribute('preserveAspectRatio','none');svg.setAttribute('aria-hidden','true');svg.classList.add('kpi-spark');card.appendChild(svg)}
   const key=card.dataset.spark;const vals=source.slice(-35).map(s=>num(s[key]));const validVals=vals.filter(v=>v!==null);
   if(validVals.length<2){svg.replaceChildren();return}
   const low=Math.min(...validVals),high=Math.max(...validVals),padding=Math.max((high-low)*.1,.000001);
   let path='',active=false;vals.forEach((value,index)=>{if(value===null){active=false;return}const x=index/Math.max(1,vals.length-1)*140,y=21-(value-low+padding)/(high-low+2*padding)*18;path+=(active?'L':'M')+x.toFixed(1)+' '+y.toFixed(1)+' ';active=true});
   const color=i===2?colors.amber:i===3?colors.purple:colors.f_direct;
   // SVG markup is authored from numbers only; no server/user markup enters innerHTML.
   svg.innerHTML='<path d="'+path+'" fill="none" stroke="'+color+'" stroke-width="1.8" vector-effect="non-scaling-stroke" stroke-linecap="round"/>';
 });
}
function samplePulse(){if(!motionEnabled)return;for(const el of document.querySelectorAll('#overview .kpi')){el.classList.remove('new-sample');void el.offsetWidth;el.classList.add('new-sample')}}
function tooltipForCanvas(el){let tip=el.closest('.panel').querySelector('.chart-tip');if(!tip){tip=document.createElement('div');tip.className='chart-tip';tip.setAttribute('role','tooltip');tip.hidden=true;el.closest('.panel').appendChild(tip)}return tip}
for(const id of ['overviewTrend','diameterTrend','overviewSigned','positionTrend']){
 const el=$(id);const tip=tooltipForCanvas(el);
 el.addEventListener('pointermove',event=>{
   const g=plotGeometry.get(id);if(!g||!g.rows.length){tip.hidden=true;return}
   const box=el.getBoundingClientRect(),x=event.clientX-box.left,y=event.clientY-box.top;
   if(x<g.a.l||x>g.a.r||y<g.a.t||y>g.a.b){tip.hidden=true;return}
   let best=0,dist=Infinity;for(let i=0;i<g.xx.length;i++){const d=Math.abs(g.a.x(g.xx[i])-x);if(d<dist){dist=d;best=i}}
   const row=g.rows[best];if(!row){tip.hidden=true;return}
   const content=g.series.map(k=>{const v=num(row[k.key]);return '<div><span>'+safeText(k.key.replace(/_/g,' '))+': </span>'+fmt(v===null?null:v*(k.factor||1),k.factor===1000?2:5)+' '+safeText(g.unitY)+'</div>'}).join('');
   tip.innerHTML='<strong>FRAME '+(best+1)+' · '+fmt(g.xx[best],2)+' s</strong>'+content;
   tip.style.left=Math.min(x+14,box.width-190)+'px';tip.style.top=Math.max(12,y-25)+'px';tip.hidden=false;
 });
 el.addEventListener('pointerleave',()=>tip.hidden=true);
}
const heatEl=$('edgeHeatmap'),heatTip=$('heatTooltip');
heatEl.addEventListener('pointermove',event=>{
 const q=window.heatHoverData;if(!q){heatTip.hidden=true;return}
 const box=heatEl.getBoundingClientRect(),x=event.clientX-box.left,y=event.clientY-box.top;
 if(x<q.l||x>=q.r||y<q.t||y>=q.b){heatTip.hidden=true;return}
 const col=Math.floor((x-q.l)/q.dx),row=Math.floor((y-q.t)/q.dy);
 if(row<0||row>=q.rows.length||col<0||col>=40){heatTip.hidden=true;return}
 const width=q.rows[row][col],dev=q.numbers[row][col],raw=q.rawRows[row],accepted=raw['edge_accepted_'+q.view];
 const rejected=Array.isArray(accepted)&&width!==null&&!accepted.includes(col);
 heatTip.innerHTML='<strong>'+safeText(q.view.toUpperCase())+' · FRAME '+(row+1)+' / COL '+col+'</strong><div><span>Width: </span>'+fmt(width,4)+' px</div><div><span>Deviation: </span>'+fmt(dev,4)+' px</div><div><span>Column: </span>'+(width===null?'MISSING':rejected?'REJECTED':'AVAILABLE')+'</div>';
 heatTip.style.left=Math.max(8,Math.min(x+12,box.width-215))+'px';heatTip.style.top=Math.max(4,Math.min(y+12,box.height-106))+'px';heatTip.hidden=false;
});
heatEl.addEventListener('pointerleave',()=>heatTip.hidden=true);
$('sampleWindow').addEventListener('change',e=>{sampleWindow=e.target.value;render()});
$('pauseLive').addEventListener('click',()=>{
 displayPaused=!displayPaused;
 const button=$('pauseLive');button.textContent=displayPaused?'▶ Resume display':'Ⅱ Pause display';button.setAttribute('aria-pressed',String(displayPaused));$('pauseNotice').hidden=!displayPaused;
 if(!displayPaused&&latestRemoteState){state=latestRemoteState;render()}
});
$('motionToggle').addEventListener('click',()=>{
 motionEnabled=!motionEnabled;document.body.classList.toggle('no-motion',!motionEnabled);
 const btn=$('motionToggle');btn.setAttribute('aria-pressed',String(motionEnabled));btn.textContent=motionEnabled?'◌ MOTION':'○ STATIC';
 if(!motionEnabled){orbitEnabled=false;updateOrbitButton()}
});
$('themeToggle').addEventListener('click',()=>{document.body.classList.toggle('light-theme');const b=$('themeToggle');b.setAttribute('aria-pressed',String(document.body.classList.contains('light-theme')));b.textContent=isLight()?'◐ DARK':'◐ LIGHT';render()});
function updateOrbitButton(){$('orbitToggle').textContent=orbitEnabled?'⟳ Auto orbit: ON':'⟳ Auto orbit: OFF';$('orbitToggle').setAttribute('aria-pressed',String(orbitEnabled))}
$('orbitToggle').addEventListener('click',()=>{orbitEnabled=!orbitEnabled;if(orbitEnabled&&!motionEnabled){motionEnabled=true;document.body.classList.remove('no-motion');$('motionToggle').textContent='◌ MOTION';$('motionToggle').setAttribute('aria-pressed','true')}updateOrbitButton()});
setInterval(()=>{if(orbitEnabled&&motionEnabled&&currentPage==='profile'&&!displayPaused&&!document.hidden&&!document.querySelector('.panel.focused:not(:has(#meshCanvas))')&&!dragging){yaw+=.014;mesh()}},150);
function configureExpand(){document.querySelectorAll('.panel').forEach(panel=>{
 const canvas=panel.querySelector('canvas'),header=panel.querySelector('.panel-header');if(!canvas||!header)return;
 panel.classList.add('chart-panel');const button=document.createElement('button');button.type='button';button.className='expand-control';button.textContent='⤢';button.title='Expand this chart';button.setAttribute('aria-label','Expand chart');button.setAttribute('aria-pressed','false');
 button.addEventListener('click',()=>{const expanded=panel.classList.contains('focused');document.querySelectorAll('.panel.focused').forEach(x=>{x.classList.remove('focused');x.querySelector('.expand-control').setAttribute('aria-pressed','false')});if(!expanded){panel.classList.add('focused');button.setAttribute('aria-pressed','true')}else button.setAttribute('aria-pressed','false');render()});
 header.appendChild(button);
 })}
configureExpand();
document.addEventListener('keydown',e=>{if(e.key==='Escape'){document.querySelectorAll('.panel.focused').forEach(x=>{x.classList.remove('focused');x.querySelector('.expand-control').setAttribute('aria-pressed','false')});render()}});
window.addEventListener('keydown',e=>{if(e.code==='Space'&&currentPage==='profile'&&e.target.tagName!=='BUTTON'&&e.target.tagName!=='INPUT'){e.preventDefault();orbitEnabled=!orbitEnabled;updateOrbitButton()}});
if(!motionEnabled){document.body.classList.add('no-motion');$('motionToggle').textContent='○ STATIC';$('motionToggle').setAttribute('aria-pressed','false')}
$('themeToggle').textContent='◐ LIGHT';
