import xml.etree.ElementTree as ET, re, collections, json, sys
root=ET.parse('old.xml').getroot()
rows=[]
for tc in root.iter('testcase'):
    f=tc.find('failure')
    name=tc.get('name')
    if f is None: rows.append((name,'PASS','',''));continue
    msg=(f.get('message') or '')
    txt=f.text or ''
    # last E line
    e=[l for l in txt.splitlines() if l.startswith('E ')]
    last=e[-1][1:].strip() if e else msg
    if 'DID NOT RAISE' in msg: cat='a_no_raise'
    elif 'must be a declared field' in last or re.search(r"assert '\w+' in \{",last): cat='c_missing_field_name'
    elif 'unexpected keyword' in last or 'missing' in last and 'argument' in last: cat='c_new_kwarg'
    elif last.startswith('TypeError') or 'TypeError' in msg: cat='c_type_api_mismatch(date vs tz-aware datetime)'
    elif 'AssertionError' in msg or last.startswith('assert') or 'assert' in last: cat='a_wrong_value'
    else: cat='other'
    rows.append((tc.get('classname').split('.')[-1]+'::'+name,cat,last[:100],''))
json.dump(rows,open('old_rows.json','w'))
c=collections.Counter(r[1] for r in rows); print(c)
for r in rows:
    if r[1] in('a_wrong_value','other','a_no_raise') or r[1].startswith('c_new'): print(r[:3])
