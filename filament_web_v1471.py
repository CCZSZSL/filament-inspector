"""v14.7.0 read-only, offline-capable LAN dashboard HTTP service.

ThreadingHTTPServer handles browsers; only the Tk thread publishes immutable
copies of detector data. HTTP worker threads never read Tk state, call the
Pi Camera Server, or invoke any measurement function.
"""
from __future__ import annotations
import csv
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import math
from pathlib import Path
import socket
import threading

WEB_DIR = Path(__file__).resolve().parent / 'web_v1471'
FILES = {'/': ('index.html','text/html; charset=utf-8'),
         '/index.html':('index.html','text/html; charset=utf-8'),
         '/dashboard.css':('dashboard.css','text/css; charset=utf-8'),
         '/dashboard.js':('dashboard.js','application/javascript; charset=utf-8')}


def clean_number(n):
    try:
        n=float(n)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def network_addresses():
    """Best-effort interface detection; no HTTP/DNS requests to public sites."""
    addresses=set()
    try:
        for entry in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET):
            ip=entry[4][0]
            if not ip.startswith('127.') and not ip.startswith('169.254.'):
                addresses.add(ip)
    except OSError:
        pass
    try:
        probe=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        try:
            probe.connect(('192.0.2.1',80))  # TEST-NET; no packet is sent by UDP connect
            ip=probe.getsockname()[0]
            if not ip.startswith('127.') and not ip.startswith('169.254.'):
                addresses.add(ip)
        finally:
            probe.close()
    except OSError:
        pass
    return sorted(addresses)


class DashboardStore:
    """Atomic copy-on-publish snapshot. Hard limit prevents unbounded HTTP output."""
    def __init__(self, max_rows=1800):
        self.lock=threading.Lock()
        self.max_rows=max(10,int(max_rows))
        self._state={'version':'14.7.1','updated_at':None,'mode':'idle',
                     'calibration':'Local Tk calibration required',
                     'experiment':{}, 'measurements':[], 'limits':{'target':1.75,'tolerance':0.05}}

    def publish(self, *, measurements=(), mode='idle', calibration='', experiment=None, limits=None):
        """All args must be plain JSON serializable values; no Tk objects."""
        state={'version':'14.7.1',
               'updated_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
               'mode':str(mode), 'calibration':str(calibration),
               'experiment':experiment or {},
               'measurements':list(measurements)[-self.max_rows:],
               'limits':limits or {'target':1.75,'tolerance':0.05}}
        # Strict snapshot serialization here keeps readers safe if producer mutates lists.
        encoded=json.dumps(state,allow_nan=False,separators=(',',':')).encode('utf-8')
        with self.lock:
            self._state=json.loads(encoded)
        return len(state['measurements'])

    def snapshot(self):
        with self.lock:
            return self._state


def csv_from_rows(rows):
    fields=['timestamp','elapsed_s','source','left_mm','right_mm','f_direct_mm',
            'delta_lr_mm','signed_lr_mm','left_y_px','right_y_px',
            'left_pixel_width','right_pixel_width','left_valid_columns','right_valid_columns',
            'left_quality','right_quality','left_calibration_status','right_calibration_status',
            'left_position_mode','right_position_mode','failure_reason','status','axial_mm','axial_run']
    fields += [f'{view}_edge_col_{i:02d}_px' for view in ('left','right') for i in range(40)]
    buf=StringIO()
    writer=csv.DictWriter(buf,fieldnames=fields,extrasaction='ignore')
    writer.writeheader()
    for r in rows:
        row=dict(r)
        for view in ('left','right'):
            widths=row.get('edge_columns_'+view) or []
            for i,v in enumerate(widths[:40]):
                row[f'{view}_edge_col_{i:02d}_px']=v
        writer.writerow(row)
    return buf.getvalue().encode('utf-8-sig')


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        server_version='FilamentLAN/14.7.0'
        def _send(self, code, blob, content_type, *, attachment=False):
            self.send_response(code)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(blob)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('X-Frame-Options','DENY')
            self.send_header('Content-Security-Policy',
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            if attachment:self.send_header('Content-Disposition','attachment; filename="filament_dashboard_v1471.csv"')
            self.end_headers()
            try:self.wfile.write(blob)
            except (BrokenPipeError, ConnectionResetError):pass
        def do_GET(self):
            url=self.path.split('?',1)[0]
            if url=='/api/state':
                data=json.dumps(store.snapshot(),allow_nan=False,separators=(',',':')).encode('utf-8')
                return self._send(200,data,'application/json; charset=utf-8')
            if url=='/api/export.csv':
                return self._send(200,csv_from_rows(store.snapshot()['measurements']),
                                  'text/csv; charset=utf-8',attachment=True)
            if url=='/api/health':
                return self._send(200,b'{"status":"ok","version":"14.7.1"}',
                                  'application/json; charset=utf-8')
            info=FILES.get(url)
            if info:
                try:blob=(WEB_DIR/info[0]).read_bytes()
                except OSError:return self._send(500,b'Asset unavailable','text/plain')
                return self._send(200,blob,info[1])
            return self._send(404,b'Not Found','text/plain; charset=utf-8')
        def do_POST(self):return self._send(405,b'Read-only dashboard','text/plain')
        do_PUT=do_POST
        do_DELETE=do_POST
        def log_message(self, fmt, *args):
            # HTTP requests are never logged to Tk and cannot block GUI.
            return
    return Handler


class LanDashboardServer:
    def __init__(self, store, *, host='0.0.0.0', port=8765):
        if not isinstance(port,int) or not (1024<=port<=65535 or (port==0 and host=='127.0.0.1')):
            raise ValueError('Port must be 1024–65535')
        self.host=host;self.port=port;self.store=store
        self._httpd=None;self._thread=None

    @property
    def running(self):return bool(self._thread and self._thread.is_alive())

    def start(self):
        if self.running:return
        # bind immediately: report port-in-use errors to Tk instead of a silent worker failure
        class Server(ThreadingHTTPServer):
            daemon_threads=True
            allow_reuse_address=True
        httpd=Server((self.host,self.port),make_handler(self.store))
        self.port=httpd.server_address[1]
        self._httpd=httpd
        self._thread=threading.Thread(target=httpd.serve_forever,name='filament-lan-web',daemon=True)
        self._thread.start()

    def stop(self):
        httpd,self._httpd=self._httpd,None
        thread,self._thread=self._thread,None
        if httpd:
            httpd.shutdown();httpd.server_close()
        if thread and thread is not threading.current_thread():thread.join(timeout=2)
