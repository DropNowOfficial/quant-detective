"""Small local workbench. No account, order, notification or feed endpoints."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re

from .artifacts import render_html, save_run
from .engine import Policy, screen


def make_server(raw_dir, universe, output_dir, port=8765):
    output_dir = Path(output_dir)
    current = screen(raw_dir,universe,Policy(),datetime.now(timezone.utc))
    save_run(current,output_dir)
    state = {'result':current}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass

        def send(self, status, body, content_type='application/json; charset=utf-8'):
            if not isinstance(body, str): body=json.dumps(body,ensure_ascii=False,allow_nan=False)
            content=body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(content)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(content)

        def valid_host(self):
            host=self.headers.get('Host','')
            if host not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
                self.send(403,{'error':'仅允许本机地址访问'});return False
            origin=self.headers.get('Origin')
            if origin is not None and origin != f'http://{host}':
                self.send(403,{'error':'拒绝跨站请求'});return False
            return True

        def do_GET(self):
            if not self.valid_host(): return
            if self.path=='/':
                self.send(200,render_html(state['result'],live=True),'text/html; charset=utf-8')
            elif self.path=='/api/runs':
                records=[]
                for p in output_dir.glob('*/result.json'):
                    try:
                        d=json.loads(p.read_text())
                        records.append({k:d[k] for k in ['run_id','session','as_of','counts']})
                    except (OSError,ValueError,KeyError): continue
                self.send(200,sorted(records,key=lambda x:x['as_of'],reverse=True)[:100])
            elif re.fullmatch(r'/api/runs/[a-f0-9]{24}',self.path):
                path=output_dir/self.path.rsplit('/',1)[1]/'result.json'
                if path.is_file(): self.send(200,path.read_text())
                else: self.send(404,{'error':'未找到留档'})
            else: self.send(404,{'error':'未找到此页面'})

        def do_POST(self):
            if not self.valid_host(): return
            if self.path!='/api/screen': self.send(404,{'error':'未找到操作'});return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=8192: raise ValueError('请求体必须为 1–8192 字节')
                if self.headers.get('Content-Type','').split(';')[0]!='application/json': raise ValueError('请求必须使用 JSON')
                request=json.loads(self.rfile.read(length))
                if not isinstance(request,dict) or set(request)-{'session','as_of','policy'}: raise ValueError('不支持的请求字段')
                raw_policy=request.get('policy',{})
                if not isinstance(raw_policy,dict): raise ValueError('policy 必须为对象')
                as_of=request.get('as_of')
                if as_of is None:
                    as_of=datetime.now(timezone.utc)
                elif not isinstance(as_of,str) or not as_of.strip():
                    raise ValueError('as_of 必须为带时区的时间字符串；省略或 null 才使用当前时间')
                result=screen(raw_dir,universe,Policy(**raw_policy),as_of,request.get('session'))
                save_run(result,output_dir);state['result']=result
                self.send(200,result)
            except (ValueError,TypeError,KeyError) as exc:
                self.send(400,{'error':str(exc)})
            except OSError:
                self.send(500,{'error':'读取行情或保存留档失败，请检查本地文件权限和剩余空间'})

    return HTTPServer(('127.0.0.1',port),Handler)
