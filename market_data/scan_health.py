"""Nontrade scanner-health events using the existing durable Slack path.

The serialized hosted workflow is the sole writer. Trusted immutable comments
and accepted ledger snapshots preserve incident deduplication across restarts.
Stdlib-only: dependency synchronization failures can still be diagnosed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

from . import slack_alerts as sa

COOLDOWN_SECONDS = 1800
SEVERITY = {'SCAN_RECOVERED': 0, 'SCAN_DEGRADED': 1, 'SCAN_UNAVAILABLE': 2, 'SCAN_FAILED': 3}


def _failed(action='Inspect the linked workflow: scan evidence is missing or invalid.'):
    return {'state': 'SCAN_FAILED', 'usable': None, 'requested': None, 'missing': [], 'action': action}


def assess(report, outcome):
    """Describe coverage without forwarding raw errors, credentials or prices."""
    report = report if isinstance(report, dict) else {}
    status = report.get('scan_status')
    if isinstance(status, str) and status in {'SKIPPED', 'SESSION_ENDED'} and outcome == 'success' and not report.get('real_acquisition_failure'):
        return None
    requested, rows = report.get('symbols_requested'), report.get('rows')
    if (not isinstance(requested, list) or not requested or len(requested) > 60
            or any(not isinstance(s, str) or not re.fullmatch(r'[A-Z0-9][A-Z0-9._-]{0,15}', s) for s in requested)
            or len(set(requested)) != len(requested) or not isinstance(rows, list)):
        return _failed()
    if (len(rows) != len(requested)
            or any(not isinstance(r, dict) or not isinstance(r.get('symbol'), str)
                   or r['symbol'] not in requested or not isinstance(r.get('status'), str)
                   or r['status'] not in {'OK', 'ERROR', 'INVALID_DATA'} for r in rows)
            or len({r['symbol'] for r in rows}) != len(requested)):
        return _failed('Inspect the linked workflow: scan row evidence is malformed or incomplete.')
    usable = {r['symbol'] for r in rows if r['status'] == 'OK'}
    missing = sorted(set(requested) - usable)
    if not missing and status == 'COMPLETE' and outcome == 'success':
        state, action = 'SCAN_RECOVERED', 'Full requested-symbol coverage is restored.'
    elif missing:
        state = 'SCAN_DEGRADED' if usable else 'SCAN_UNAVAILABLE'
        action = ('Inspect failed-symbol evidence and provider freshness in the linked run. '
                  'Invalid or stale quotes remain blocked; usable observations may still be published.')
    else:
        state, action = 'SCAN_FAILED', 'Inspect the linked workflow for scan or GitHub publication failure.'
    if report.get('ending_session') and report.get('real_acquisition_failure'):
        action += ' The session ended during this scan; this notice concerns actual acquisition errors, not the expected close.'
    return {'state': state, 'usable': len(usable), 'requested': len(requested), 'missing': missing, 'action': action}


def _event_body(assessment, *, symbol, now, run_id):
    day, state = now.astimezone(sa.ET).date().isoformat(), assessment['state']
    metadata = {'schema': 1, 'generated_at_et': now.astimezone(sa.ET).isoformat(), 'run_id': run_id}
    lines = [f'<!-- qd-event:{day}|{symbol}|{state} -->',
             '<!-- qd-source:' + json.dumps(metadata, separators=(',', ':')) + ' -->',
             f'### {symbol} — {state}', '', 'SCANNER HEALTH / 非交易信号',
             f'- Health checked at: {now.isoformat()}',
             '- Scope: GitHub hosted fallback only. This does not establish VPS health.']
    if state == 'SCAN_DELIVERY_TEST':
        lines += ['- HEALTH / DELIVERY TEST: authorized transport verification.',
                  '- No market data or signal is being asserted; incident state is unchanged.']
    else:
        total, usable = assessment['requested'], assessment['usable']
        lines += [f'- Usable / requested symbols: {usable} / {total}' if total is not None
                  else '- Usable / requested symbols: unknown (no trusted scan artifact).',
                  '- Missing / rejected symbols: ' + (', '.join(assessment['missing']) or 'none / see workflow'),
                  '- Next step: ' + assessment['action']]
    if run_id:
        lines.append(f'- Workflow evidence: https://github.com/{sa.REPO}/actions/runs/{run_id}')
    return '\n'.join(lines + ['', 'System diagnostics only. No orders or trading actions.'])


def _health_sources(comments, issue, config):
    sources = []
    for comment in comments:
        source = sa.parse_source(comment, issue, config)
        if source is not None and source.state in sa.HEALTH_STATES:
            sources.append(source)
    return sorted(sources, key=lambda source: (source.created_at, source.comment_id))


def publish_health(report, *, outcome, github, store, clock, delivery_test=False, run_id=None):
    """One verified source POST at most, never direct Slack or an uncertain retry."""
    assessment = {'state': 'SCAN_DELIVERY_TEST'} if delivery_test else assess(report, outcome)
    if assessment is None:
        return {'published': 0, 'reason': 'session_skipped'}
    if store.config != github.config:
        raise sa.LedgerUnavailable('Health configuration mismatch')
    ledger = store.load()  # Already-authorized initialized ledger, fail closed.
    issue, _ = github.get_issue(store.config.ledger_issue)
    sources = _health_sources(github.list_issue_comments(store.config.ledger_issue), issue, store.config)
    known = {source.comment_id: source for source in sources}
    for entry in ledger.entries.values():
        if entry.source.state in sa.HEALTH_STATES:
            known[entry.source.comment_id] = entry.source  # Immutable accepted evidence beats later edits.
    sources = sorted(known.values(), key=lambda source: (source.created_at, source.comment_id))
    now, state = clock.now(), assessment['state']
    run_id = run_id if isinstance(run_id, str) and re.fullmatch(r'[1-9][0-9]{0,18}', run_id) else None
    if delivery_test:
        if not run_id:
            raise ValueError('Delivery test requires workflow run identity')
        symbol = 'SCANNER.TEST.' + run_id
        if any(source.symbol == symbol and source.state == state for source in sources):
            return {'published': 0, 'reason': 'test_already_persisted'}
    else:
        incidents = [source for source in sources if source.state != 'SCAN_DELIVERY_TEST']
        previous = incidents[-1] if incidents else None
        if previous is None and state == 'SCAN_RECOVERED':
            return {'published': 0, 'reason': 'healthy_no_incident'}
        if previous and state == previous.state:
            return {'published': 0, 'reason': 'unchanged'}
        if (previous and (now - previous.created_at).total_seconds() < COOLDOWN_SECONDS
                and SEVERITY[state] <= SEVERITY[previous.state]):
            return {'published': 0, 'reason': 'cooldown'}
        symbol = 'SCANNER.' + str(previous.comment_id if previous else 0)
    body = _event_body(assessment, symbol=symbol, now=now, run_id=run_id)
    key = body.splitlines()[0]
    path = f'/repos/{store.config.repo}/issues/{store.config.ledger_issue}/comments'
    try:
        github._request('POST', path, {'body': body})
    except sa.LedgerUnavailable:
        pass  # Readback can establish a lost response; never issue another POST.
    confirmed = _health_sources(github.list_issue_comments(store.config.ledger_issue), issue, store.config)
    matches = [source for source in confirmed if source.body and source.body.splitlines()[0] == key]
    if len(matches) != 1 or matches[0].body != body:
        raise sa.LedgerUncertain('Health event not uniquely confirmed')
    return {'published': 1, 'state': state, 'source_comment_id': matches[0].comment_id}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Persist nontrade scanner health using the existing Slack path')
    parser.add_argument('--report', default='market-watch.json')
    parser.add_argument('--outcome', choices=['success', 'failure', 'skipped', 'cancelled'], required=True)
    parser.add_argument('--delivery-test', action='store_true')
    args = parser.parse_args(argv)
    result, code = {'published': 0}, 0
    if os.environ.get('QD_SLACK_ENABLED') != 'true':
        result['reason'] = 'disabled'
    elif (os.environ.get('GITHUB_REPOSITORY') != sa.REPO
          or os.environ.get('GITHUB_REPOSITORY_ID') != '1369484548' or os.environ.get('GITHUB_REF') != 'refs/heads/main'
          or os.environ.get('GITHUB_EVENT_NAME') not in {'push', 'schedule', 'workflow_dispatch'}):
        result['reason'] = 'untrusted_context'
    elif args.outcome == 'cancelled':
        result['reason'] = 'cancelled'
    else:
        try:
            if args.delivery_test and os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch':
                raise ValueError('Test requires explicit dispatch')
            issue, producers = os.environ.get('QD_SLACK_LEDGER_ISSUE', ''), os.environ.get('QD_SLACK_PRODUCER_IDS', '')
            if not re.fullmatch(r'[1-9][0-9]*', issue) or producers != '41898282':
                raise ValueError('Invalid health configuration')
            config = sa.Config(sa.REPO, int(issue), frozenset(map(int, producers.split(','))), sa.CHANNEL_ID)
            token = os.environ.get('GITHUB_TOKEN')
            if not token:
                raise ValueError('Missing runtime token')
            try:
                report = json.loads(Path(args.report).read_text(encoding='utf-8'))
            except (OSError, ValueError, UnicodeError):
                report = None
            clock = sa.SystemClock()
            budget = sa.MonotonicBudget(clock, sa.BUDGET_SECONDS)
            github = sa.GitHubClient(config, token, sa.StdlibHttpClient(), budget)
            with sa._hard_deadline(budget):
                result = publish_health(report, outcome=args.outcome, github=github,
                                        store=sa.LedgerStore(github, config), clock=clock,
                                        delivery_test=args.delivery_test, run_id=os.environ.get('GITHUB_RUN_ID'))
        except (ValueError, TypeError):
            result['reason'], code = 'invalid_configuration', 1
        except (sa.LedgerUnavailable, sa.BudgetExpired):
            result['reason'], code = 'health_persistence_unavailable', 1
        except Exception:
            result['reason'], code = 'health_internal_error', 1
    print(json.dumps(result, sort_keys=True), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
