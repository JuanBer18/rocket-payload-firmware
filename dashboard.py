"""Compact offline Wi-Fi dashboard, file manager, and staged updater."""

import json
import os
import socket
import time

import config
from core import ota
from mission import MISSION_ACTIVE, MISSION_COMPLETE, PAD_IDLE


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Payload Control</title><style>
:root{--desk:#3a6b6b;--face:#c6c6c6;--light:#fff;--mid:#888;--dark:#202020;
--navy:#00006d;--text:#101010;--green:#087b24;--red:#a00000;--amber:#9a5600}
*{box-sizing:border-box}body{margin:0;background:var(--desk);color:var(--text);
font:13px "Courier New",monospace}.app{max-width:1160px;margin:8px auto;background:var(--face);
border:2px solid;border-color:var(--light) var(--dark) var(--dark) var(--light);
box-shadow:2px 2px 0 #111}.titlebar{display:flex;justify-content:space-between;align-items:center;
background:var(--navy);color:#fff;padding:5px 7px;font-weight:bold;letter-spacing:.02em}
.titlebar small{font-weight:normal}.tabs{display:flex;gap:2px;padding:7px 7px 0;border-bottom:1px solid var(--mid)}
.tab{min-width:135px;padding:7px 12px;border:2px solid;border-color:var(--light) var(--dark) var(--face) var(--light);
background:var(--face);font:inherit;font-weight:bold}.tab.active{position:relative;top:1px;background:#ddd}
.view{display:none;padding:8px}.view.active{display:block}.panel{background:#d8d8d8;border:2px solid;
border-color:var(--mid) var(--light) var(--light) var(--mid);padding:8px}.summary{display:grid;
grid-template-columns:1.5fr 1fr 1fr;gap:7px}.summary .panel{min-height:66px}.label{font-size:11px;
text-transform:uppercase;color:#414141}.big{font:bold 22px "Courier New",monospace;margin-top:7px;
font-variant-numeric:tabular-nums}.state-active{color:var(--green)}.state-idle{color:var(--navy)}
.state-warn{color:var(--amber)}.state-bad{color:var(--red)}.sensor-grid{display:grid;
grid-template-columns:repeat(3,1fr);gap:7px;margin-top:7px}.sensor{padding:0}.sensor-head{display:flex;
justify-content:space-between;background:#b5b5b5;border-bottom:1px solid var(--mid);padding:6px 8px;
font-weight:bold}.lamp{display:inline-block;width:9px;height:9px;border:1px solid #333;background:#777;
margin-right:5px}.lamp.on{background:#00a62c}.readings{display:grid;grid-template-columns:1fr 1fr;
gap:0}.reading{padding:7px 8px;border-bottom:1px dotted #8c8c8c;min-width:0}.reading:nth-child(odd){border-right:1px dotted #8c8c8c}
.reading b{display:block;font-size:15px;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:7px}.message{flex:1;min-width:240px;
padding:7px;background:#eee;border:1px inset #fff;min-height:31px}.btn,select,input{font:13px "Courier New",monospace}
.btn{padding:7px 11px;background:var(--face);border:2px solid;border-color:var(--light) var(--dark) var(--dark) var(--light);
color:#111;font-weight:bold;cursor:pointer}.btn:active{border-color:var(--dark) var(--light) var(--light) var(--dark)}
.btn:disabled{color:#777;cursor:not-allowed}.btn.start{background:#b8d9b8}.btn.stop{background:#e8b4aa}
.btn.primary{background:#aec4e8}.btn.danger{color:#7a0000}.group-grid{display:grid;grid-template-columns:1fr 1fr;
gap:8px}.group{border:1px solid #777;padding:9px;margin:0;min-width:0}.group legend{font-weight:bold;padding:0 5px}
.group .btn{margin:3px 2px}.field{display:flex;align-items:center;gap:7px;margin:5px 0;flex-wrap:wrap}
select,input[type=text]{padding:6px;background:#fff;border:2px inset #eee;min-width:180px}input[type=file]{max-width:100%}
.statusline{margin-top:8px;border:2px inset #eee;background:#eee;padding:5px}.locked{padding:18px;text-align:center;
border:1px dashed #666;background:#cacaca}.hidden{display:none!important}table{width:100%;border-collapse:collapse;background:#eee}
th,td{border:1px solid #888;padding:5px;text-align:left}th{background:#aaa}td.num{text-align:right}
a{color:#00007a;font-weight:bold}.file-actions{white-space:nowrap}.debug-grid{display:grid;grid-template-columns:1.15fr .85fr;
gap:8px}.diag{display:grid;grid-template-columns:repeat(4,1fr);gap:5px}.diag div{background:#eee;
border:1px inset #fff;padding:6px;overflow-wrap:anywhere}.plot-grid{display:grid;grid-template-columns:repeat(3,1fr);
gap:6px;margin-top:7px}.plot-card{border:2px inset #eee;background:#ddd;padding:5px}.plot-title{font-size:11px;
margin-bottom:3px}.plot-card canvas{display:block;width:100%;height:125px;background:#fff}.fine{font-size:11px;color:#444}
@media(max-width:760px){.app{margin:0}.summary,.sensor-grid,.group-grid,.debug-grid{grid-template-columns:1fr}
.plot-grid{grid-template-columns:1fr}.tabs{overflow-x:auto}.tab{min-width:115px}.diag{grid-template-columns:1fr 1fr}}
</style></head><body><main class="app">
<header class="titlebar"><span>ATMOSPHERIC PAYLOAD CONTROL</span><small>HYPOWER PAYLOAD // LOCAL</small></header>
<nav class="tabs" aria-label="Dashboard sections">
<button class="tab active" data-tab="status">STATUS</button><button class="tab" data-tab="controls">MISSION CONTROLS</button>
<button class="tab" data-tab="debug">DEBUG / FILES</button></nav>

<section id="status" class="view active">
 <div class="summary">
  <div class="panel"><div class="label">STATE</div><div id="state" class="big state-warn">CONNECTING</div></div>
  <div class="panel"><div class="label">TIME SINCE POWER-UP</div><div id="uptime" class="big">--:--:--</div></div>
  <div class="panel"><div class="label">CURRENT BIN</div><div id="binNumber" class="big">--</div></div>
 </div>
 <div class="sensor-grid">
  <article class="panel sensor"><div class="sensor-head"><span>SEN65</span><span><i id="senLamp" class="lamp"></i><span id="senState">OFF</span></span></div>
   <div class="readings">
    <div class="reading"><span class="label">VOC INDEX</span><b id="voc">--</b></div><div class="reading"><span class="label">NOX INDEX</span><b id="nox">--</b></div>
    <div class="reading"><span class="label">PM1.0</span><b id="pm1">--</b></div><div class="reading"><span class="label">PM2.5</span><b id="pm25">--</b></div>
    <div class="reading"><span class="label">PM4.0</span><b id="pm4">--</b></div><div class="reading"><span class="label">PM10</span><b id="pm10">--</b></div>
   </div>
  </article>
  <article class="panel sensor"><div class="sensor-head"><span>IMU</span><span><i id="imuLamp" class="lamp"></i><span id="imuState">OFF</span></span></div>
   <div class="readings">
    <div class="reading"><span class="label">MAGNITUDE</span><b id="gmag">--</b></div><div class="reading"><span class="label">RATE</span><b id="imuRate">--</b></div>
    <div class="reading"><span class="label">ACCEL X</span><b id="ax">--</b></div><div class="reading"><span class="label">ACCEL Y</span><b id="ay">--</b></div>
    <div class="reading"><span class="label">ACCEL Z</span><b id="az">--</b></div><div class="reading"><span class="label">FIFO OVERRUNS</span><b id="overruns">--</b></div>
   </div>
  </article>
  <article class="panel sensor"><div class="sensor-head"><span>BAROMETER</span><span><i id="baroLamp" class="lamp"></i><span id="baroState">OFF</span></span></div>
   <div class="readings">
    <div class="reading"><span class="label">PRESSURE</span><b id="pressure">--</b></div><div class="reading"><span class="label">ALTITUDE AGL</span><b id="altitude">--</b></div>
    <div class="reading"><span class="label">TEMPERATURE</span><b id="baroTemp">--</b></div><div class="reading"><span class="label">HUMIDITY</span><b id="baroRh">--</b></div>
   </div>
  </article>
 </div>
 <div class="bar"><button id="startButton" class="btn start" onclick="startMission()">START NEW MISSION</button>
 <button id="stopButton" class="btn stop" onclick="stopMission()">STOP + CLOSE SAFELY</button><div id="message" class="message">Connecting to payload...</div></div>
</section>

<section id="controls" class="view">
 <div class="group-grid">
  <fieldset class="group"><legend>MISSION DEFAULTS</legend>
   <div class="field"><label for="mode">Mode after next power cycle</label><select id="mode"><option value="launch_triggered">Launch triggered</option><option value="continuous">Continuous</option></select>
   <button class="btn primary" data-idle onclick="saveMode()">SAVE DEFAULT</button></div>
   <div class="fine">Controls unlock only after STOP + CLOSE SAFELY reaches IDLE.</div>
  </fieldset>
  <fieldset class="group"><legend>BAROMETER</legend>
   <button class="btn" data-idle onclick="act('zero_altitude')">ZERO ALTITUDE HERE</button>
   <div class="fine">Sets the present pressure as 0 m AGL. Raw pressure is unchanged.</div>
  </fieldset>
  <fieldset class="group"><legend>IMU TESTS</legend>
   <button class="btn" data-idle onclick="act('gyro_cal','Keep the payload completely still. Calibrate gyro?')">CALIBRATE GYRO</button>
   <button class="btn" data-idle onclick="act('imu_test','Keep the payload still. Run self-test?')">RUN SELF-TEST</button>
  </fieldset>
  <fieldset class="group"><legend>SEN65 SERVICE</legend>
   <button class="btn" data-idle onclick="act('sen_start')">START SENSOR</button><button class="btn" data-idle onclick="act('sen_stop')">STOP SENSOR</button>
   <button class="btn" data-idle onclick="act('fan_clean','SEN65 must be stopped. Begin fan clean?')">CLEAN FAN</button>
  </fieldset>
 </div>
 <fieldset class="group" style="margin-top:8px"><legend>LIVE PLOTS</legend>
  <button id="plotButton" class="btn" data-idle onclick="togglePlots()">TURN LIVE PLOTS ON</button>
  <span id="plotTimer" class="fine">OFF // automatically stops after 10 minutes</span>
  <div id="plots" class="plot-grid hidden"></div>
 </fieldset>
 <div id="controlMessage" class="statusline">IDLE controls are locked while recording.</div>
</section>

<section id="debug" class="view">
 <div class="bar" style="margin-top:0"><button id="debugButton" class="btn danger" onclick="toggleDebug()">ENTER DEBUG MODE</button>
 <div class="message">Debug is allowed only while no BIN is open. Entering Debug stops live plots.</div></div>
 <div id="debugLocked" class="locked">DEBUG MODE IS OFF<br><span class="fine">Enable it to inspect SD files or stage firmware.</span></div>
 <div id="debugBody" class="hidden">
  <div class="debug-grid" style="margin-top:8px">
   <fieldset class="group"><legend>SD CARD FILES</legend><table><thead><tr><th>FILE</th><th>SIZE</th><th>ACTIONS</th></tr></thead><tbody id="files"><tr><td colspan="3">Loading...</td></tr></tbody></table></fieldset>
   <fieldset class="group"><legend>WIRELESS SOURCE UPDATE</legend>
    <div class="field"><input id="updateFile" type="file" accept=".py,text/x-python"><input id="updateTarget" type="text" placeholder="destination e.g. dashboard.py"></div>
    <button class="btn primary" onclick="uploadUpdate()">STAGE + CHECK FILE</button><button class="btn" onclick="discardUpdate()">DISCARD STAGED</button>
    <table style="margin-top:7px"><thead><tr><th>STAGED DESTINATION</th><th>SIZE</th></tr></thead><tbody id="staged"><tr><td colspan="2">None</td></tr></tbody></table>
    <button id="applyButton" class="btn danger" onclick="applyUpdate()" style="margin-top:7px">INSTALL STAGED UPDATE + REBOOT</button>
    <div class="fine" style="margin-top:6px">Python files are syntax-checked and kept in staging first. The previous firmware is restored if the updated dashboard does not boot and confirm itself.</div>
   </fieldset>
  </div>
  <fieldset class="group" style="margin-top:8px"><legend>SYSTEM DIAGNOSTICS</legend><div id="diagnostics" class="diag"></div></fieldset>
 </div>
 <div id="debugMessage" class="statusline">No debug action pending.</div>
</section>
</main><script>
const $=id=>document.getElementById(id);let current=null,plotsEnabled=false,plotData={},plotLimit=600;
const plotDefs=[
 {title:'Altitude AGL',unit:'m',series:[['altitude','agl_m','ALT','#00006d']]},
 {title:'Acceleration magnitude',unit:'g',series:[['imu','accel_mag_g','MAG G','#8b0000']]},
 {title:'Pressure',unit:'Pa',series:[['bme280','pressure_pa','PRESSURE','#006060']]},
 {title:'VOC index',unit:'index',series:[['sen65','voc','VOC','#6d006d']]},
 {title:'Particulate matter',unit:'ug/m3',series:[['sen65','pm1p0','PM1.0','#00006d'],['sen65','pm2p5','PM2.5','#a00000'],['sen65','pm4p0','PM4.0','#087b24'],['sen65','pm10','PM10','#9a5600']]}
];
document.querySelectorAll('.tab').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x===button));document.querySelectorAll('.view').forEach(x=>x.classList.toggle('active',x.id===button.dataset.tab))}));
function value(v,unit='',digits){const n=Number(v);if(v===null||v===undefined||!Number.isFinite(n))return '--';return (digits===undefined?n:n.toFixed(digits))+(unit?' '+unit:'')}
function esc(v){return String(v===null||v===undefined?'--':v).replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]))}
function elapsed(v){let n=Math.max(0,Math.floor(Number(v)||0)),h=Math.floor(n/3600),m=Math.floor(n%3600/60),s=n%60;return [h,m,s].map(x=>String(x).padStart(2,'0')).join(':')}
async function jsonCall(url,options){const r=await fetch(url,Object.assign({cache:'no-store'},options||{}));const text=await r.text();let obj;try{obj=JSON.parse(text)}catch(e){throw new Error(text||('HTTP '+r.status))}if(!r.ok)throw new Error(obj.error||('HTTP '+r.status));return obj}
function recording(s){return !!(s&&(s.state==='MISSION_ACTIVE'||s.state==='PAD_IDLE'||(s.system&&s.system.logging_active)))}
function idle(s){return !!(s&&s.state==='MISSION_COMPLETE'&&s.system&&!s.system.logging_active)}
function stateName(s){if(s.dashboard&&s.dashboard.debug_enabled)return ['DEBUG','state-warn'];if(s.state==='MISSION_ACTIVE')return [s.system.logging_active?'ACTIVE / RECORDING':'ACTIVE / LOG ERROR',s.system.logging_active?'state-active':'state-bad'];if(s.state==='PAD_IDLE')return ['ARMED / RECORDING','state-warn'];if(s.state==='MISSION_COMPLETE')return ['IDLE','state-idle'];if(s.state==='BOOTING')return ['STARTING','state-warn'];return [s.state||'UNKNOWN','state-bad']}
function setSensor(prefix,on,label){$(prefix+'Lamp').classList.toggle('on',!!on);$(prefix+'State').textContent=on?(label||'ACTIVE'):'OFF'}
function show(id,v,unit,digits){$(id).textContent=value(v,unit,digits)}
function updateStatus(s){current=s;const st=stateName(s);$('state').textContent=st[0];$('state').className='big '+st[1];$('uptime').textContent=elapsed(s.system.uptime_s);let path=s.system.log_path||'',m=path.match(/flight_(\d+)\.bin$/);$('binNumber').textContent=m?'#'+m[1]:'--';
 const rec=recording(s),health=s.sensor_health||{},sen=s.sen65||{},imu=s.imu||{},baro=s.bme280||{};
 setSensor('sen',!!sen.measuring);setSensor('imu',rec&&!!(health.imu&&health.imu.available));setSensor('baro',rec&&!!(health.bme280&&health.bme280.available));
 show('voc',sen.voc,'',1);show('nox',sen.nox,'',1);show('pm1',sen.pm1p0,'ug/m3',1);show('pm25',sen.pm2p5,'ug/m3',1);show('pm4',sen.pm4p0,'ug/m3',1);show('pm10',sen.pm10,'ug/m3',1);
 show('gmag',imu.accel_mag_g,'g',3);show('imuRate',imu.odr_hz,'Hz',0);show('ax',imu.ax_g,'g',3);show('ay',imu.ay_g,'g',3);show('az',imu.az_g,'g',3);show('overruns',s.system.fifo_overruns,'',0);
 show('pressure',baro.pressure_pa===null?null:baro.pressure_pa/100,'hPa',2);show('altitude',(s.altitude||{}).agl_m,'m',2);show('baroTemp',baro.temperature_c,'C',2);show('baroRh',baro.humidity_pct,'%',1);
 $('message').textContent=s.message||'';if(document.activeElement!==$('mode'))$('mode').value=s.mission.next_boot_mode;
 document.querySelectorAll('[data-idle]').forEach(x=>x.disabled=!idle(s));$('startButton').disabled=!(idle(s)||s.state==='PAD_IDLE');$('startButton').textContent=s.state==='PAD_IDLE'?'START MISSION NOW':'START NEW MISSION';$('stopButton').disabled=!rec;
 const debug=!!(s.dashboard&&s.dashboard.debug_enabled);$('debugButton').disabled=rec;$('debugButton').textContent=debug?'EXIT DEBUG MODE':'ENTER DEBUG MODE';$('debugLocked').classList.toggle('hidden',debug);$('debugBody').classList.toggle('hidden',!debug);
 const diag={log:s.system.log_path,bytes:s.system.log_bytes,sd_free:s.system.sd_free_bytes,heap_free:s.system.heap_free_bytes,errors:s.system.errors,logging_errors:s.system.logging_errors,last_error:s.system.last_error,last_test:s.system.last_test};$('diagnostics').innerHTML=Object.entries(diag).map(([k,v])=>`<div><span class="label">${esc(k.replaceAll('_',' '))}</span><br><b>${esc(v)}</b></div>`).join('');
 syncPlots(s.dashboard||{});recordPlots(s);renderStaged((s.dashboard&&s.dashboard.update&&s.dashboard.update.staged)||[]);if(debug)loadFiles()}
async function act(name,question,target='controlMessage'){if(question&&!confirm(question))return;try{const r=await jsonCall('/api/action?action='+encodeURIComponent(name));$(target).textContent=JSON.stringify(r);await refresh()}catch(e){$(target).textContent='ERROR: '+e.message}}
async function startMission(){await act('start','Start a new mission and open the next numbered BIN?','message')}
async function stopMission(){await act('stop','Stop sensors, drain the IMU FIFO, flush and safely close the BIN?','message')}
async function saveMode(){try{const r=await jsonCall('/api/action?action=set_mode&mode='+encodeURIComponent($('mode').value));$('controlMessage').textContent=JSON.stringify(r)}catch(e){$('controlMessage').textContent='ERROR: '+e.message}}
async function toggleDebug(){await act(current&&current.dashboard.debug_enabled?'debug_off':'debug_on',null,'debugMessage')}
async function loadFiles(){if(!current||!current.dashboard.debug_enabled)return;try{const r=await jsonCall('/api/logs');$('files').innerHTML=r.logs.map(x=>{const encoded=encodeURIComponent(x[0]);return `<tr><td>${esc(x[0])}</td><td class="num">${(x[1]/1024).toFixed(1)} KiB</td><td class="file-actions"><a href="/download?name=${encoded}">DOWNLOAD</a> <button class="btn danger" onclick="deleteFile(decodeURIComponent('${encoded}'))">DELETE</button></td></tr>`}).join('')||'<tr><td colspan="3">No mission files found</td></tr>'}catch(e){$('files').innerHTML='<tr><td colspan="3">'+esc(e.message)+'</td></tr>'}}
async function deleteFile(name){if(!confirm('Permanently delete '+name+' from the SD card?'))return;try{const r=await jsonCall('/api/action?action=delete_file&name='+encodeURIComponent(name));$('debugMessage').textContent=JSON.stringify(r);loadFiles()}catch(e){$('debugMessage').textContent='ERROR: '+e.message}}
$('updateFile').addEventListener('change',()=>{const f=$('updateFile').files[0];if(f&&!$('updateTarget').value)$('updateTarget').value=f.name});
async function uploadUpdate(){const f=$('updateFile').files[0],target=$('updateTarget').value.trim();if(!f||!target){$('debugMessage').textContent='Choose a .py file and destination first.';return}try{$('debugMessage').textContent='Uploading and checking '+target+'...';const r=await jsonCall('/api/update?name='+encodeURIComponent(target),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:f});$('debugMessage').textContent='STAGED: '+r.target+' // SHA256 '+r.sha256;await refresh()}catch(e){$('debugMessage').textContent='ERROR: '+e.message}}
function renderStaged(entries){$('staged').innerHTML=entries.map(x=>`<tr><td>${esc(x.target)}</td><td class="num">${Number(x.size)||0} B</td></tr>`).join('')||'<tr><td colspan="2">None</td></tr>';$('applyButton').disabled=!entries.length}
async function discardUpdate(){await act('update_discard','Discard all staged update files?','debugMessage')}
async function applyUpdate(){if(!confirm('Install every staged file and reboot? Keep power connected. Automatic rollback protects a failed dashboard boot.'))return;await act('update_apply',null,'debugMessage')}
function plotKey(i,j){return i+'.'+j}
function makePlots(){plotData={};$('plots').innerHTML=plotDefs.map((d,i)=>{d.series.forEach((series,j)=>plotData[plotKey(i,j)]=[]);const legend=d.series.map(series=>`<span style="color:${series[3]}">■ ${series[2]}</span>`).join(' &nbsp; ');return `<div class="plot-card"><div class="plot-title"><b>${d.title}</b> (${d.unit}) &nbsp; ${legend}</div><canvas id="plot${i}"></canvas></div>`}).join('')}
function drawPlot(canvas,definition,index){const groups=definition.series.map((series,j)=>({series:series,points:plotData[plotKey(index,j)]||[]})),all=[];groups.forEach(group=>group.points.forEach(point=>all.push(point)));const dpr=window.devicePixelRatio||1,w=Math.max(220,canvas.clientWidth),h=125;canvas.width=w*dpr;canvas.height=h*dpr;const c=canvas.getContext('2d');c.scale(dpr,dpr);c.fillStyle='#fff';c.fillRect(0,0,w,h);c.strokeStyle='#777';c.strokeRect(35,6,w-40,h-24);if(!all.length){c.fillStyle='#555';c.fillText('WAITING',45,62);return}let lo=all[0][1],hi=lo,t0=all[0][0],t1=t0;all.forEach(p=>{lo=Math.min(lo,p[1]);hi=Math.max(hi,p[1]);t0=Math.min(t0,p[0]);t1=Math.max(t1,p[0])});let pad=(hi-lo)*.08||Math.max(Math.abs(hi)*.02,.01);lo-=pad;hi+=pad;const span=Math.max(1,t1-t0);c.fillStyle='#333';c.font='9px monospace';c.fillText(hi.toPrecision(4),2,12);c.fillText(lo.toPrecision(4),2,h-18);groups.forEach(group=>{c.strokeStyle=group.series[3];c.lineWidth=1.25;c.beginPath();group.points.forEach((p,j)=>{const x=35+(p[0]-t0)/span*(w-40),y=6+(hi-p[1])/(hi-lo)*(h-24);j?c.lineTo(x,y):c.moveTo(x,y)});c.stroke()})}
function recordPlots(s){if(!plotsEnabled)return;const t=Number(s.system.uptime_s);plotDefs.forEach((d,i)=>{d.series.forEach((series,j)=>{const v=s[series[0]]&&s[series[0]][series[1]],n=Number(v),key=plotKey(i,j),a=plotData[key]||[];if(v!==null&&v!==undefined&&Number.isFinite(n)){a.push([t,n]);if(a.length>plotLimit)a.shift()}plotData[key]=a});drawPlot($('plot'+i),d,i)})}
function syncPlots(info){const on=!!info.graphs_enabled;if(info.graphs_points)plotLimit=info.graphs_points;if(on&&!plotsEnabled){makePlots();$('plots').classList.remove('hidden')}if(!on&&plotsEnabled){plotData={};$('plots').innerHTML='';$('plots').classList.add('hidden')}plotsEnabled=on;$('plotButton').textContent=on?'TURN LIVE PLOTS OFF':'TURN LIVE PLOTS ON';$('plotTimer').textContent=on?'ON // auto-off in '+Math.ceil(info.graphs_remaining_s/60)+' min':'OFF // automatically stops after 10 minutes'}
async function togglePlots(){await act(plotsEnabled?'graphs_off':'graphs_on',null,'controlMessage')}
async function refresh(){try{updateStatus(await jsonCall('/api/status'))}catch(e){$('state').textContent='CONNECTION LOST';$('state').className='big state-bad';$('message').textContent=e.message}}
refresh();setInterval(refresh,1000);setInterval(()=>{if(current&&current.dashboard.debug_enabled)loadFiles()},5000);
</script></body></html>"""


class Dashboard:
    def __init__(self, mission, log=print):
        self.mission = mission
        self.log = log
        self.ap = None
        self.sock = None
        self.graphs_enabled = False
        self.debug_enabled = False
        self._graphs_deadline_ms = None
        self._reboot_pending = False

    def _recording(self):
        return bool(self.mission.state in (MISSION_ACTIVE, PAD_IDLE) or
                    getattr(self.mission, "logger", None))

    def _idle(self):
        return bool(self.mission.state == MISSION_COMPLETE and
                    not getattr(self.mission, "logger", None))

    def _expire_graphs(self):
        if (self.graphs_enabled and self._graphs_deadline_ms is not None and
                time.ticks_diff(time.ticks_ms(), self._graphs_deadline_ms) >= 0):
            self._set_graphs(False)

    def _graph_status(self):
        self._expire_graphs()
        remaining_s = 0
        if self.graphs_enabled:
            remaining_ms = time.ticks_diff(self._graphs_deadline_ms,
                                           time.ticks_ms())
            remaining_s = max(0, remaining_ms // 1000)
        return {"ok": True, "graphs_enabled": self.graphs_enabled,
                "graphs_remaining_s": remaining_s,
                "graphs_timeout_s": config.DASHBOARD_GRAPH_TIMEOUT_MS // 1000,
                "graphs_points": config.DASHBOARD_GRAPH_POINTS}

    def _set_graphs(self, enabled):
        if enabled and not self._idle():
            return {"ok": False, "error": "live plots require IDLE"}
        self.graphs_enabled = bool(enabled)
        self._graphs_deadline_ms = (time.ticks_add(
            time.ticks_ms(), config.DASHBOARD_GRAPH_TIMEOUT_MS)
            if self.graphs_enabled else None)
        if hasattr(self.mission, "set_maintenance_sampling"):
            result = self.mission.set_maintenance_sampling(self.graphs_enabled)
            if self.graphs_enabled and not result.get("ok"):
                self.graphs_enabled = False
                self._graphs_deadline_ms = None
                return result
        return self._graph_status()

    def _status(self):
        status = self.mission.status_snapshot()
        dashboard = self._graph_status()
        dashboard.update({
            "debug_enabled": self.debug_enabled,
            "controls_enabled": self._idle(),
            "recording": self._recording(),
            "update": ota.status() if self.debug_enabled else {"staged": []},
        })
        status["dashboard"] = dashboard
        return status

    def start(self):
        import network
        self.ap = network.WLAN(network.AP_IF)
        self.ap.active(True)
        self.ap.config(essid=config.AP_SSID, password=config.AP_PASSWORD,
                       authmode=network.AUTH_WPA_WPA2_PSK,
                       channel=config.AP_CHANNEL)
        deadline = time.ticks_add(time.ticks_ms(), 5000)
        while not self.ap.active():
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                raise OSError("Wi-Fi access point did not start")
            time.sleep_ms(50)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", config.HTTP_PORT))
        self.sock.listen(2)
        self.sock.settimeout(0)
        ip = self.ap.ifconfig()[0]
        self.log("Wi-Fi '%s'; dashboard http://%s/" % (config.AP_SSID, ip))
        return ip

    def poll(self):
        self._expire_graphs()
        if not self.sock:
            return False
        try:
            client, _address = self.sock.accept()
        except OSError:
            return False
        try:
            client.settimeout(config.HTTP_CLIENT_TIMEOUT_S)
            request = client.readline()
            if not request:
                return False
            parts = request.decode("utf-8", "replace").split(" ")
            method = parts[0] if parts else "GET"
            path = parts[1] if len(parts) > 1 else "/"
            headers = {}
            while True:
                line = client.readline()
                if not line or line in (b"\r\n", b"\n"):
                    break
                pair = line.decode("utf-8", "replace").split(":", 1)
                if len(pair) == 2:
                    headers[pair[0].strip().lower()] = pair[1].strip()
            self._route(client, method, path, headers)
        except Exception as exc:
            self.log("HTTP error: %r" % (exc,))
        finally:
            try:
                client.close()
            except Exception:
                pass
        if self._reboot_pending:
            self._reboot_pending = False
            time.sleep_ms(300)
            import machine
            machine.reset()
        return True

    def _route(self, client, method, path, headers):
        base = path.split("?", 1)[0]
        if method == "GET" and base in ("/", "/index.html"):
            self._send(client, "200 OK", "text/html", PAGE)
        elif method == "GET" and base == "/api/status":
            self._json(client, self._status())
        elif method == "GET" and base == "/api/logs":
            if not self.debug_enabled:
                self._json(client, {"ok": False, "error": "enter DEBUG mode first"},
                           "403 Forbidden")
            else:
                self._json(client, {"ok": True, "logs": self.mission.list_logs()})
        elif method == "GET" and base == "/api/action":
            result = self._action(path)
            self._json(client, result,
                       "200 OK" if result.get("ok") else "409 Conflict")
        elif method == "GET" and base == "/download":
            self._download(client, self._param(path, "name"))
        elif method == "POST" and base == "/api/update":
            try:
                length = int(headers.get("content-length", "0"))
            except ValueError:
                length = 0
            self._upload_update(client, self._param(path, "name"), length)
        else:
            self._send(client, "404 Not Found", "text/plain", "not found")

    def _action(self, path):
        action = self._param(path, "action")
        if action == "start":
            if self.mission.state not in (MISSION_COMPLETE, PAD_IDLE):
                return {"ok": False, "error": "start requires IDLE or ARMED"}
            return self.mission.start_mission("dashboard start")
        if action == "stop":
            return self.mission.stop_mission()
        if action == "graphs_off":
            return self._set_graphs(False)
        if action == "debug_on":
            if self._recording():
                return {"ok": False, "error": "stop and close the BIN before DEBUG"}
            self._set_graphs(False)
            self.debug_enabled = True
            return {"ok": True, "debug_enabled": True}
        if action == "debug_off":
            self.debug_enabled = False
            return {"ok": True, "debug_enabled": False}
        if action in ("delete_file", "update_discard", "update_apply"):
            if not self.debug_enabled or self._recording():
                return {"ok": False, "error": "action requires non-recording DEBUG mode"}
            if action == "delete_file":
                return self._delete_file(self._param(path, "name"))
            if action == "update_discard":
                return ota.discard_staged()
            try:
                result = ota.activate()
                self._reboot_pending = bool(result.get("ok"))
                return result
            except Exception as exc:
                return {"ok": False, "error": repr(exc)}
        if not self._idle():
            return {"ok": False, "error": "control requires IDLE with no open BIN"}
        if action == "graphs_on":
            return self._set_graphs(True)
        if action == "set_mode":
            return self.mission.set_boot_mode(self._param(path, "mode"))
        if action == "sen_start":
            return self.mission.start_sen65()
        if action == "sen_stop":
            return self.mission.stop_sen65()
        if action == "zero_altitude":
            return self.mission.zero_altitude()
        if action == "gyro_cal":
            return self.mission.calibrate_gyro()
        if action == "imu_test":
            return self.mission.imu_self_test()
        if action == "fan_clean":
            return self.mission.clean_sen65()
        return {"ok": False, "error": "unknown action"}

    def _delete_file(self, name):
        if (not name or "/" in name or ".." in name or
                not name.startswith(config.LOG_PREFIX) or
                not (name.endswith(config.LOG_EXT) or name.endswith(".csv"))):
            return {"ok": False, "error": "invalid mission filename"}
        try:
            os.remove("%s/%s" % (config.SD_MOUNT, name))
            return {"ok": True, "deleted": name}
        except OSError as exc:
            return {"ok": False, "error": repr(exc)}

    @staticmethod
    def _url_decode(value):
        if value is None:
            return None
        value = value.replace("+", " ")
        output = bytearray()
        index = 0
        while index < len(value):
            if value[index] == "%" and index + 2 < len(value):
                try:
                    output.append(int(value[index + 1:index + 3], 16))
                    index += 3
                    continue
                except ValueError:
                    pass
            output.extend(value[index].encode("utf-8"))
            index += 1
        return output.decode("utf-8", "replace")

    @classmethod
    def _param(cls, path, key):
        if "?" not in path:
            return None
        for field in path.split("?", 1)[1].split("&"):
            pair = field.split("=", 1)
            if len(pair) == 2 and pair[0] == key:
                return cls._url_decode(pair[1])
        return None

    @staticmethod
    def _send(client, status, content_type, body):
        if isinstance(body, str):
            body = body.encode("utf-8")
        header = ("HTTP/1.0 %s\r\nContent-Type: %s\r\n"
                  "Cache-Control: no-store\r\nContent-Length: %d\r\n\r\n"
                  % (status, content_type, len(body))).encode("utf-8")
        client.write(header)
        client.write(body)

    def _json(self, client, obj, status="200 OK"):
        self._send(client, status, "application/json", json.dumps(obj))

    def _upload_update(self, client, name, length):
        if not self.debug_enabled or self._recording():
            self._json(client, {"ok": False, "error": "upload requires non-recording DEBUG mode"},
                       "403 Forbidden")
            return
        try:
            client.settimeout(config.OTA_UPLOAD_TIMEOUT_S)
            entry = ota.stage_stream(name, client, length)
            result = dict(entry)
            result["ok"] = True
            self._json(client, result)
        except Exception as exc:
            self._json(client, {"ok": False, "error": repr(exc)}, "400 Bad Request")

    def _download(self, client, name):
        if not self.debug_enabled:
            self._send(client, "403 Forbidden", "text/plain", "enter DEBUG mode first")
            return
        if self._recording():
            self._send(client, "409 Conflict", "text/plain",
                       "downloads are disabled while recording")
            return
        if not name or "/" in name or ".." in name:
            self._send(client, "400 Bad Request", "text/plain", "bad filename")
            return
        full_path = "%s/%s" % (config.SD_MOUNT, name)
        try:
            size = os.stat(full_path)[6]
            source = open(full_path, "rb")
        except OSError:
            self._send(client, "404 Not Found", "text/plain", "no such log")
            return
        try:
            header = ("HTTP/1.0 200 OK\r\nContent-Type: application/octet-stream\r\n"
                      "Content-Length: %d\r\nContent-Disposition: attachment; filename=\"%s\"\r\n\r\n"
                      % (size, name)).encode("utf-8")
            client.write(header)
            buf = bytearray(config.HTTP_DOWNLOAD_CHUNK)
            view = memoryview(buf)
            while True:
                count = source.readinto(buf)
                if not count:
                    break
                client.write(view[:count])
        finally:
            source.close()
