import json
from pathlib import Path


def render(payload):
    data=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    return Path(__file__).with_name('template.html').read_text().replace('__LAB_DATA__',data)
