"""Explicit local research commands; importing the package never contacts a broker."""
import argparse,json
from pathlib import Path
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from functools import partial
from .experiment import run_experiment,save_experiment,public_summary
from .render import render


class LocalResearchHandler(SimpleHTTPRequestHandler):
    """Private snapshots are served only to an explicit local Host."""
    def local_host(self):
        hosts=self.headers.get_all('Host') or []
        allowed={f'{host}:{self.server.server_port}' for host in ['localhost','127.0.0.1']}
        if self.server.server_port==80:allowed.update({'localhost','127.0.0.1'})
        if len(hosts)!=1 or hosts[0].lower() not in allowed:
            self.send_error(403,'Local Host required');return False
        return True

    def do_GET(self):
        if self.local_host():super().do_GET()

    def do_HEAD(self):
        if self.local_host():super().do_HEAD()


def main():
    p=argparse.ArgumentParser(description='Quant Detective · read-only strategy research laboratory')
    sub=p.add_subparsers(dest='command',required=True)
    b=sub.add_parser('build');b.add_argument('--ibkr',type=Path,required=True);b.add_argument('--yahoo',type=Path,required=True)
    b.add_argument('--expected-manifest',type=Path)
    b.add_argument('--sources',type=Path,default=Path('research_outputs/sources.json'));b.add_argument('--review',type=Path)
    b.add_argument('--output',type=Path,default=Path('research_lab/runs'));b.add_argument('--public-summary',type=Path)
    s=sub.add_parser('serve');s.add_argument('directory',type=Path);s.add_argument('--port',type=int,default=8766)
    args=p.parse_args()
    if args.command=='serve':
        if not args.directory.is_dir():p.error('directory does not exist')
        server=ThreadingHTTPServer(('127.0.0.1',args.port),partial(LocalResearchHandler,directory=str(args.directory)))
        print(f'Research lab: http://127.0.0.1:{server.server_port}/lab.html',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close()
        return
    try:
        sources=json.loads(args.sources.read_text());review=json.loads(args.review.read_text()) if args.review else None
        expected=json.loads(args.expected_manifest.read_text()) if args.expected_manifest else None
        result=run_experiment(args.ibkr,args.yahoo,sources,review,expected);folder=save_experiment(result,args.output)
        html=folder/'lab.html';rendered=render(result)
        if html.exists() and html.read_text()!=rendered:raise ValueError('HTML evidence already exists; use a new output directory for another renderer')
        html.write_text(rendered)
        if args.public_summary:
            args.public_summary.parent.mkdir(parents=True,exist_ok=True)
            args.public_summary.write_text(json.dumps(public_summary(result),ensure_ascii=False,indent=2,allow_nan=False)+'\n')
        print(json.dumps({'run_id':result['run_id'],'output':str(folder),'source_rows':result['meta']['source_rows'],'scenarios':result['meta']['visible_scenario_count']},ensure_ascii=False))
    except (ValueError,OSError,KeyError,TypeError) as e:p.exit(2,f'Research failed: {e}\n')

if __name__=='__main__':main()
