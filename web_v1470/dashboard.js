'use strict';
// Offline, read-only charting: browser receives snapshots from Mac's Python HTTP server.
const $=id=>document.getElementById(id);
const PAGES={overview:['LIVE QUALITY MONITORING','Measurement Overview','Live diameter, mirror agreement and validity at a glance.'],diameter:['STATISTICAL MEASUREMENT','Diameter Analysis','Unfiltered diameter distribution, limits and moving range.'],mirrors:['POSITION & GEOMETRY','Mirror & Position','Independent left/right views, positional trends and calibration status.'],profile:['AXISYMMETRIC VISUALIZATION','3D Diameter Profile','Interactive nominal-circle model; not surface roughness or roundness.'],experiments:['STATIC / MOTION','Measurement Experiments','Local Mac controls experiment acquisition; browsers only monitor results.']};
let currentPage='overview', state={measurements:[],mode:'idle',experiment:{},limits:{}}, updating=false;
let target=1.75,tolerance=.05, yaw=.63,pitch=.34,zoom=1;
const num=x=>{if(x===null||x===undefined||x==='')return null;let n=Number(x);return Number.isFinite(n)?n:null};
const valid=(r,key)=>num(r[key]);
const fmt=(x,d=4)=>x===null||!Number.isFinite(x)?'—':x.toFixed(d);
const mean=xs=>xs.length?xs.reduce((a,b)=>a+b,0)/xs.length:null;
const std=xs=>xs.length>1?Math.sqrt(xs.reduce((a,x)=>a+(x-mean(xs))**2,0)/(xs.length-1)):null;
const colors={left:'#4b91d5',right:'#d89c3d',f_direct:'#1c9d95',red:'#d9666b',purple:'#9e81d7'};
function setText(id,value){let x=$(id);if(x)x.textContent=value}
function getCanvas(id){let c=$(id);if(!c)return null;const ratio=Math.min(window.devicePixelRatio||1,2),w=Math.max(100,c.clientWidth),h=Math.max(100,c.clientHeight);c.width=Math.round(w*ratio);c.height=Math.round(h*ratio);const ctx=c.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);return {c,ctx,w,h}}
function clean(ctx,w,h,dark=false){ctx.fillStyle=dark?'#142435':'#fff';ctx.fillRect(0,0,w,h)}
function empty(ctx,w,h,text='Waiting for measurements'){ctx.fillStyle='#93a3b1';ctx.font='13px system-ui';ctx.textAlign='center';ctx.fillText(text,w/2,h/2);ctx.textAlign='left'}
function bounds(xs,fallback=1){if(!xs.length)return [0,fallback];let lo=Math.min(...xs),hi=Math.max(...xs);const pad=hi===lo?Math.max(Math.abs(lo)*.005,.0005):(hi-lo)*.12;return [lo-pad,hi+pad]}
function axes(ctx,w,h,minX,maxX,minY,maxY,unitX,unitY){let l=57,t=15,r=w-16,b=h-34;
 ctx.lineWidth=1;ctx.font='10px system-ui';ctx.fillStyle='#8a9aab';ctx.textAlign='right';
 for(let i=0;i<=4;i++){let yy=t+i*(b-t)/4,v=maxY-i*(maxY-minY)/4;ctx.strokeStyle='#e8eef3';ctx.beginPath();ctx.moveTo(l,yy);ctx.lineTo(r,yy);ctx.stroke();ctx.fillText(fmt(v,(Math.abs(v)<10&&Math.abs(maxY-minY)<1)?4:1),l-7,yy+3)}
 ctx.textAlign='left';ctx.fillText(fmt(minX,1),l,b+17);ctx.textAlign='right';ctx.fillText(fmt(maxX,1),r,b+17);ctx.textAlign='left';ctx.fillText(unitY||'',l,10);ctx.textAlign='center';ctx.fillText(unitX||'',(l+r)/2,h-3);
 return {l,t,r,b,x:x=>l+(x-minX)/(maxX-minX||1)*(r-l),y:y=>b-(y-minY)/(maxY-minY||1)*(b-t)};
}
function timePlot(id,series,{lines=[],unitY='mm'}={}){
 let q=getCanvas(id);if(!q)return;let {ctx,w,h}=q;clean(ctx,w,h);
 const rows=state.measurements||[];let xx=rows.map((s,i)=>num(s.elapsed_s)??i);let yy=rows.flatMap(s=>series.map(k=>{let v=num(s[k.key]);return v===null?null:v*(k.factor||1)}).filter(v=>v!==null));
 yy.push(...lines.map(x=>x.value));if(!rows.length||!yy.length){empty(ctx,w,h);return}
 const [lo,hi]=bounds(yy);let a=axes(ctx,w,h,Math.min(...xx),Math.max(...xx)+1e-6,lo,hi,'elapsed s',unitY);
 for(const marker of lines){ctx.strokeStyle=marker.color||colors.red;ctx.setLineDash([6,5]);ctx.beginPath();ctx.moveTo(a.l,a.y(marker.value));ctx.lineTo(a.r,a.y(marker.value));ctx.stroke()}ctx.setLineDash([]);
 for(const k of series){ctx.strokeStyle=k.color;ctx.lineWidth=1.8;ctx.beginPath();let drawing=false;
 for(let i=0;i<rows.length;i++){const v=num(rows[i][k.key]);const x=xx[i];if(v===null){drawing=false;continue}const px=a.x(x),py=a.y(v*(k.factor||1));if(!drawing){ctx.moveTo(px,py);drawing=true}else ctx.lineTo(px,py)}ctx.stroke()}
}
function scatter(id,pairs,{unitX='mm',unitY='mm',identity=false,lines=[]}={}){
 let q=getCanvas(id);if(!q)return;let {ctx,w,h}=q;clean(ctx,w,h);pairs=pairs.filter(([x,y])=>num(x)!==null&&num(y)!==null);
 if(!pairs.length){empty(ctx,w,h);return}
 const [xmin,xmax]=bounds(pairs.map(p=>p[0]).concat(identity?pairs.map(p=>p[1]):[]));const [ymin,ymax]=bounds(pairs.map(p=>p[1]).concat(identity?pairs.map(p=>p[0]):[]));let a=axes(ctx,w,h,xmin,xmax,ymin,ymax,unitX,unitY);
 if(identity){ctx.strokeStyle='#9da9b6';ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(a.x(xmin),a.y(xmin));ctx.lineTo(a.x(xmax),a.y(xmax));ctx.stroke();ctx.setLineDash([])}
 for(const m of lines){ctx.strokeStyle=m.color||colors.red;ctx.setLineDash([5,3]);ctx.beginPath();ctx.moveTo(a.l,a.y(m.y));ctx.lineTo(a.r,a.y(m.y));ctx.stroke()}ctx.setLineDash([]);
 for(const [x,y,c] of pairs){ctx.fillStyle=c||colors.left;ctx.globalAlpha=.62;ctx.beginPath();ctx.arc(a.x(x),a.y(y),2.6,0,Math.PI*2);ctx.fill()}ctx.globalAlpha=1;
}
function histogram(id){let q=getCanvas(id);if(!q)return;let {ctx,w,h}=q;clean(ctx,w,h);
 const xs=(state.measurements||[]).map(s=>valid(s,'f_direct_mm')).filter(x=>x!==null);if(!xs.length){empty(ctx,w,h);return}
 let min=Math.min(...xs),max=Math.max(...xs),bins=Math.min(24,Math.max(8,Math.ceil(Math.sqrt(xs.length))));if(min===max){min-=.0005;max+=.0005}const widths=(max-min)/bins,counts=Array(bins).fill(0);for(const x of xs){counts[Math.min(bins-1,Math.floor((x-min)/widths))]++}
 let a=axes(ctx,w,h,min,max,0,Math.max(2,...counts)*1.1,'diameter mm','count');const bw=(a.r-a.l)/bins;
 counts.forEach((cnt,i)=>{ctx.fillStyle='#81b6e0';ctx.fillRect(a.l+i*bw+1,a.y(cnt),Math.max(1,bw-2),a.b-a.y(cnt))});
 let avg=mean(xs);ctx.strokeStyle=colors.red;ctx.setLineDash([4,3]);ctx.beginPath();ctx.moveTo(a.x(avg),a.t);ctx.lineTo(a.x(avg),a.b);ctx.stroke();ctx.setLineDash([]);
}
function movingRange(){let q=getCanvas('movingRange');if(!q)return;let {ctx,w,h}=q;clean(ctx,w,h);const rows=state.measurements||[];let xs=[],ys=[];
 for(let i=1;i<rows.length;i++){let a=num(rows[i-1].f_direct_mm),b=num(rows[i].f_direct_mm);if(a!==null&&b!==null&&rows[i].source===rows[i-1].source){xs.push(i);ys.push(Math.abs(b-a)*1000)}}
 if(!ys.length){empty(ctx,w,h);return}let a=axes(ctx,w,h,0,rows.length,0,Math.max(1,...ys)*1.15,'sample index','μm');ctx.lineWidth=1.8;ctx.strokeStyle=colors.purple;ctx.beginPath();xs.forEach((x,i)=>i?ctx.lineTo(a.x(x),a.y(ys[i])):ctx.moveTo(a.x(x),a.y(ys[i])));ctx.stroke();
}
function displayStats(){let rows=state.measurements||[], vals=rows.map(s=>num(s.f_direct_mm)).filter(x=>x!==null);let avg=mean(vals),sd=std(vals);
 let last=[...rows].reverse().find(r=>num(r.f_direct_mm)!==null);let latest=last?num(last.f_direct_mm):null,dr=rows.length?num(rows[rows.length-1].delta_lr_mm):null;
 $('latestDiameter').innerHTML=fmt(latest,5)+'<span> mm</span>';$('meanDiameter').innerHTML=fmt(avg,5)+'<span> mm</span>';
 $('mirrorDifference').innerHTML=fmt(dr===null?null:dr*1000,2)+'<span> μm</span>';$('diameterStd').innerHTML=fmt(sd===null?null:sd*1000,2)+'<span> μm</span>';
 $('validFraction').innerHTML=vals.length+'<span> / '+rows.length+'</span>';
 let passed=latest===null?null:Math.abs(latest-target)<=tolerance;setText('toleranceStatus',passed===null?'WAITING':passed?'PASS':'OUT OF TOL');$('toleranceStatus').className='kpi-value status-value '+(passed===null?'':passed?'ok':'bad');
 setText('sampleCount',rows.length);setText('lastUpdate',state.updated_at?new Date(state.updated_at).toLocaleTimeString():'—');setText('calibrationStatus',state.calibration||'—');
 let stats=[['Valid mm',vals.length],['Mean (mm)',fmt(avg,5)],['Std (μm)',fmt(sd===null?null:sd*1000,2)],['Minimum (mm)',fmt(vals.length?Math.min(...vals):null,5)],['Maximum (mm)',fmt(vals.length?Math.max(...vals):null,5)],['Range (μm)',fmt(vals.length?(Math.max(...vals)-Math.min(...vals))*1000:null,2)]];
 $('diameterStats').innerHTML=stats.map(([k,v])=>'<div class="stat"><span>'+k+'</span><strong>'+v+'</strong></div>').join('');
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
function heatMap(){let q=getCanvas('edgeHeatmap');if(!q)return;let {ctx,w,h}=q;clean(ctx,w,h);const view=$('heatView').value;
 let rows=(state.measurements||[]).filter(s=>Array.isArray(s['edge_columns_'+view])).slice(-100);
 if(!rows.length){empty(ctx,w,h,'Raw 40-column widths available on new measurements');return}
 let all=rows.flatMap(s=>s['edge_columns_'+view].map(num).filter(x=>x!==null));if(!all.length){empty(ctx,w,h);return}let avg=mean(all);let range=Math.max(.2,...all.map(x=>Math.abs(x-avg)));const l=45,t=12,r=w-12,b=h-28;
 rows.forEach((row,i)=>{const arr=row['edge_columns_'+view];arr.forEach((v,j)=>{v=num(v);if(v===null)return;const tt=Math.max(-1,Math.min(1,(v-avg)/range));ctx.fillStyle=tt<0?`hsl(203 70% ${73+tt*24}%)`:`hsl(10 76% ${80-tt*37}%)`;ctx.fillRect(l+j*(r-l)/40,t+i*(b-t)/rows.length,Math.ceil((r-l)/40),Math.ceil((b-t)/rows.length))})});
 ctx.fillStyle='#7e90a0';ctx.font='11px system-ui';ctx.fillText('col 0',l,b+16);ctx.fillText('col 39',r-38,b+16);ctx.fillText('latest ↓',0,h/2);ctx.fillText('40 pixel columns · each row is a separate frame',l,9);
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
function render(){displayStats();if(currentPage==='overview'){timePlot('overviewTrend',[{key:'left_mm',color:colors.left},{key:'right_mm',color:colors.right},{key:'f_direct_mm',color:colors.f_direct}],{lines:[{value:target+tolerance,color:colors.red},{value:target-tolerance,color:colors.red}]});histogram('overviewHistogram');timePlot('overviewSigned',[{key:'signed_lr_mm',factor:1000,color:colors.red}],{unitY:'μm'})}
 else if(currentPage==='diameter'){timePlot('diameterTrend',[{key:'left_mm',color:colors.left},{key:'right_mm',color:colors.right},{key:'f_direct_mm',color:colors.f_direct}],{lines:[{value:target+tolerance,color:colors.red},{value:target-tolerance,color:colors.red}]});histogram('diameterHistogram');movingRange()}
 else if(currentPage==='mirrors'){mirrorStats();timePlot('positionTrend',[{key:'left_y_px',color:colors.left},{key:'right_y_px',color:colors.right}],{unitY:'px'});heatMap()}
 else if(currentPage==='profile')mesh();else if(currentPage==='experiments')experimentPage();
}
async function poll(){if(updating)return;updating=true;try{const response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);state=await response.json();setText('networkFlag','● CONNECTED');$('networkFlag').className='pill online';setText('footerStatus','Mac read-only measurement service online');setText('modeFlag',(state.mode||'idle').toUpperCase());$('modeFlag').className='pill';setText('clockFlag',new Date().toLocaleTimeString());render()}
 catch(e){setText('networkFlag','● DISCONNECTED');$('networkFlag').className='pill offline';setText('footerStatus','Connection lost; data is not being refreshed');setText('clockFlag',String(e.message))}finally{updating=false}}
function changePage(page){currentPage=page;document.querySelectorAll('.page').forEach(e=>e.classList.toggle('active',e.id===page));document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('active',e.dataset.page===page));const [category,title,sub]=PAGES[page];setText('pageCategory',category);setText('pageTitle',title);setText('pageSubtitle',sub);render()}
document.querySelectorAll('.nav').forEach(b=>b.addEventListener('click',()=>changePage(b.dataset.page)));
function setLimits(){let a=num($('targetInput').value),b=num($('toleranceInput').value);if(a!==null&&a>0&&b!==null&&b>0){target=a;tolerance=b;render()}}
$('targetInput').addEventListener('input',setLimits);$('toleranceInput').addEventListener('input',setLimits);
$('radialFactor').addEventListener('change',mesh);$('heatView').addEventListener('change',heatMap);
$('resetCamera').addEventListener('click',()=>{yaw=.63;pitch=.34;zoom=1;mesh()});
const meshEl=$('meshCanvas');let dragging=false,lastX=0,lastY=0;meshEl.addEventListener('pointerdown',e=>{dragging=true;lastX=e.clientX;lastY=e.clientY;meshEl.setPointerCapture(e.pointerId)});meshEl.addEventListener('pointerup',()=>dragging=false);meshEl.addEventListener('pointercancel',()=>dragging=false);
meshEl.addEventListener('pointermove',e=>{if(!dragging)return;yaw+=(e.clientX-lastX)*.012;pitch=Math.max(-1.5,Math.min(1.5,pitch+(e.clientY-lastY)*.008));lastX=e.clientX;lastY=e.clientY;mesh()});
meshEl.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(.45,Math.min(3,zoom*Math.exp(-e.deltaY*.001)));mesh()},{passive:false});
window.addEventListener('resize',()=>render());setInterval(poll,1200);poll();
