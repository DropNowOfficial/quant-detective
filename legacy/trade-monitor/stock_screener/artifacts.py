"""Portable reports and atomic, immutable screening evidence."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile

from .engine import canonical


def safe_cell(value):
    if value is None: return ''
    if isinstance(value, str) and value.lstrip().startswith(('=','+','-','@','\t','\r')):
        return "'"+value
    return value


def csv_text(result):
    output = io.StringIO(newline='')
    fields = ['session','symbol','name','state','rank','close','ma5','atr5','d5','gap','adv20',
              'rs5_qqq','rs20_qqq','rs5_soxx','rs20_soxx','return60','volume_ratio','atr_fraction',
              'candidate_low','candidate_high','block_codes','validation','run_id']
    writer = csv.DictWriter(output, fields, extrasaction='ignore')
    writer.writeheader()
    for row in result['rows']:
        values = {**row, **row['factors'], 'session':result['session'], 'run_id':result['run_id'],
                  'block_codes':';'.join(row['block_codes'])}
        writer.writerow({k:safe_cell(values.get(k)) for k in fields})
    return output.getvalue()


def render_html(result, live=False):
    template = Path(__file__).with_name('workbench.html').read_text()
    payload = json.dumps(dict(result=result,live=live),ensure_ascii=False,allow_nan=False)
    payload = payload.replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    return template.replace('__SCREEN_DATA__',payload)


def save_run(result, directory):
    data = {k:v for k,v in result.items() if k!='run_id'}
    expected_id = hashlib.sha256(canonical(data).encode()).hexdigest()[:24]
    if result.get('run_id') != expected_id: raise ValueError('结果已改变，run_id 与内容不匹配')
    directory = Path(directory); directory.mkdir(parents=True,exist_ok=True)
    target = directory/expected_id
    outputs = {'result.json':json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',
               'screen.csv':csv_text(result), 'workbench.html':render_html(result)}
    def verify_existing():
        if not all((target/name).is_file() and (target/name).read_bytes()==text.encode('utf-8') for name,text in outputs.items()):
            raise ValueError('已有留档不完整或内容冲突；保留现场，请使用新的输出目录')
    if target.exists():
        verify_existing(); return target
    temporary = Path(tempfile.mkdtemp(prefix='.writing-',dir=directory))
    try:
        for name,text in outputs.items():
            with (temporary/name).open('w',encoding='utf-8',newline='') as handle:
                handle.write(text); handle.flush(); os.fsync(handle.fileno())
        try:
            temporary.rename(target)
        except OSError:
            if not target.exists(): raise
            verify_existing()
        return target
    finally:
        if temporary.exists(): shutil.rmtree(temporary)
