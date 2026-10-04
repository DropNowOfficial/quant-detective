"""Offline deterministic replay; supplied session labels are not a market calendar."""
from collections import Counter
from dataclasses import asdict
from decimal import Decimal,InvalidOperation
import json
import math
import hashlib
import fcntl
from pathlib import Path
from .contracts import RuleSpec,RuleState,parse_bar,canonical,canonical_bar
from .quality import QualityState,check_bar
from .rules import evaluate
from .store import ReplayStore
from .notifications import LocalLogSink


def load_rules(path):
    try:
        raw=json.loads(Path(path).read_text())
        if not isinstance(raw,dict): raise ValueError('invalid rule configuration: expected object')
        if type(raw.get('schema_version')) is not int or raw.get('schema_version')!=1 or raw.get('synthetic') is not True:
            raise ValueError('only explicitly labelled synthetic configuration is supported')
        age=raw['stale_after_ms']
        if type(age) is not int or age<0: raise ValueError('invalid stale_after_ms')
        specs=[]; seen=set()
        for item in raw['rules']:
            if set(item)!=set(RuleSpec.__dataclass_fields__): raise ValueError('invalid rule fields')
            data=dict(item)
            for field in ('id','version','purpose'):
                if not isinstance(data[field],str) or not data[field].strip(): raise ValueError('missing rule identity/purpose')
            for field in ('lower','upper','protection_fraction'):
                if data[field] is not None:
                    if not isinstance(data[field],str): raise ValueError('rule numbers must be decimal strings')
                    data[field]=Decimal(data[field])
                    if not data[field].is_finite(): raise ValueError('nonfinite rule value')
            key=(data['id'],data['version'])
            if key in seen: raise ValueError('duplicate rule identity')
            seen.add(key); specs.append(RuleSpec(**data))
        if not specs: raise ValueError('no rules')
        return specs,age
    except (KeyError,TypeError,InvalidOperation,json.JSONDecodeError) as e:
        raise ValueError('invalid rule configuration') from e


def percentiles(values):
    if not values: return None
    values=sorted(values)
    return {name:values[max(0,math.ceil(p*len(values))-1)] for name,p in [('p50',.5),('p95',.95)]}


def run_replay(input_path,rules_path,database_path,output_dir):
    # Serialize observation read/evaluate/commit for this local database.
    with Path(str(database_path)+'.replay.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _run_replay(input_path,rules_path,database_path,output_dir)


def _run_replay(input_path,rules_path,database_path,output_dir):
    specs,age=load_rules(rules_path)
    store=ReplayStore(database_path)
    store.bind_rules(specs,age)
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    sink=LocalLogSink(out/'notifications.jsonl')
    new_signals=0; latencies=[]; observed=set()
    # Rule IDs/versions are already part of each cursor key and sealed above.
    # Whitespace or adding an independent rule must not hide existing cursors.
    stream=hashlib.sha256(str(Path(input_path).resolve()).encode()).hexdigest()
    cursors={ (s.id,s.version):store.cursor(stream,s.id,s.version) for s in specs }
    lines=Path(input_path).read_text().splitlines()
    # Validate every committed prefix before any new observation can be written.
    for cursor in cursors.values():
        if not cursor: continue
        if len(lines)<cursor[0]: raise ValueError('previously committed input was truncated')
        digest=hashlib.sha256(''.join(line+'\n' for line in lines[:cursor[0]]).encode()).hexdigest()
        if digest!=cursor[1]: raise ValueError('previously committed input prefix changed')
    prefix=hashlib.sha256()
    for number,line in enumerate(lines,1):
        try:
            prefix.update((line+'\n').encode())
            row=json.loads(line)
            if not isinstance(row,dict): raise ValueError('input envelope must be an object')
            if row.get('synthetic') is not True: raise ValueError('synthetic input label required')
            bar=parse_bar(row['bar'])
            session=row.get('session_open')
            now=row.get('now_ms',bar.received_ms)
            observed.add(bar.instrument.key())
            if bar.complete: latencies.append(bar.received_ms-bar.end_ms)
            for spec in specs:
                cursor=cursors[(spec.id,spec.version)]
                if cursor and number<=cursor[0]:
                    if number==cursor[0] and prefix.hexdigest()!=cursor[1]:raise ValueError('previously committed input prefix changed')
                    continue
                checkpoint=store.checkpoint(bar.instrument.key(),spec.id,spec.version)
                qs,rs=checkpoint or (QualityState(bar.instrument.key()),RuleState())
                quality=check_bar(bar,qs,now_ms=now,stale_after_ms=age,session_open=session)
                decision=evaluate(bar,spec,rs,quality)
                inserted=store.commit(bar,decision,quality.state,cursor=(stream,number,prefix.hexdigest()))
                new_signals+=int(inserted and decision.kind=='signal')
        except (ValueError,KeyError,TypeError) as e:
            raise ValueError(f'line {number}: {e}') from e
    for event in store.pending():
        try:
            sink.send(event); store.mark_attempt(event['event_id'],'accepted')
        except (OSError,ValueError) as e:
            store.mark_attempt(event['event_id'],'failed',str(e))
    events=store.events()
    (out/'events.jsonl').write_text(''.join(canonical(e)+'\n' for e in events))
    report=dict(mode='offline_replay',synthetic_inputs=True,production_ready=False,delivered=None,read=None,
        new_signals=new_signals,event_ids=[e['event_id'] for e in events],reason_counts=dict(Counter(e['code'] for e in events)),
        state=[{'instrument':key,'rule_id':s.id,'version':s.version,'checkpoint':[asdict(x) for x in store.checkpoint(key,s.id,s.version)]} for key in sorted(observed) for s in specs],
        pending_local=len(store.pending()),latency_ms={'bar_end_to_received':percentiles(latencies),'calculation_to_channel':None,'delivered':None},
        latency_method='nearest-rank: sorted[ceil(p*n)-1], supplied timestamps of complete bars only',
        limitations=['Synthetic entry-range demonstration only','No live market feed or order API','Local acceptance is not user delivery','No production calendar, price entitlement or uptime verification'])
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report
