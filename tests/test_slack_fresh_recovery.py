from copy import deepcopy
from dataclasses import replace, asdict
from datetime import timedelta
import pytest
from market_data import slack_alerts as sa
from test_slack_alerts import (NOW, initialized, make_entry, make_source, make_store,
    delivery_fixture, forward_run, restored)


def test_recovery_preview_is_read_only_then_apply_preserves_unknown_and_snapshots():
    http, store = initialized()
    old = make_entry(make_source(generated_at_et=(NOW-timedelta(days=6)).isoformat()))
    unknown = make_entry(make_source(102), status='unknown', attempts=1,
                         attempt_id='old', attempted_at=NOW, reason='unconfirmed')
    store.put(old); store.put(unknown)
    before = deepcopy(http.server)
    preview = sa.recover_fresh(store, cutoff=NOW, now=NOW)
    assert preview['quarantined'] == 1 and http.server == before
    sa.recover_fresh(store, cutoff=NOW, now=NOW, apply=True)
    after = make_store(http).load()
    assert after.manifest.fresh_after == NOW
    assert after.entries[101].status == 'needs_attention'
    assert after.entries[101].source == old.source
    assert after.entries[102] == unknown
    again = deepcopy(http.server)
    sa.recover_fresh(store, cutoff=NOW, now=NOW, apply=True)
    assert http.server == again


def test_recovery_failure_does_not_advance_cursor_or_enable_sends(monkeypatch):
    http, store = initialized()
    store.put(make_entry())
    def fail(*a): raise sa.LedgerUnavailable('test interruption')
    monkeypatch.setattr(store, 'put_many', fail)
    with pytest.raises(sa.LedgerUnavailable):
        sa.recover_fresh(store, cutoff=NOW+timedelta(seconds=2), now=NOW+timedelta(seconds=2), apply=True)
    assert make_store(http).load().manifest.cursor == NOW


def test_cutoff_cannot_go_backwards_or_into_future():
    _, store = initialized()
    with pytest.raises(ValueError): sa.recover_fresh(store,cutoff=NOW-timedelta(seconds=1),now=NOW,apply=True)
    with pytest.raises(ValueError): sa.recover_fresh(store,cutoff=NOW+timedelta(seconds=1),now=NOW,apply=True)


def test_old_event_with_new_comment_timestamp_is_not_sent():
    prior = delivery_fixture()
    store = make_store(prior)
    sa.recover_fresh(store,cutoff=NOW+timedelta(seconds=1),now=NOW+timedelta(seconds=1),apply=True)
    summary, http = forward_run(prior,now=NOW+timedelta(seconds=2))
    assert summary.delivered == 0 and not http.server['slack_posts']


def test_fresh_event_uses_scan_time_not_comment_creation_time():
    entry=make_entry(make_source(created_at=NOW-timedelta(days=6),generated_at_et=NOW.isoformat()))
    assert sa._fresh_after_allowed(entry,NOW,NOW+timedelta(seconds=2))
    assert not sa._fresh_after_allowed(entry,NOW,NOW+timedelta(seconds=601))
    assert not sa._fresh_after_allowed(replace(entry,source=replace(entry.source,generated_at_et=None)),NOW,NOW)


def test_fresh_delivery_and_restart_keep_idempotence():
    prior=delivery_fixture()
    sa.recover_fresh(make_store(prior),cutoff=NOW,now=NOW,apply=True)
    first,http=forward_run(prior,now=NOW+timedelta(seconds=2))
    assert first.delivered==1
    second,http=forward_run(http,now=NOW+timedelta(seconds=4))
    assert second.delivered==0 and len(http.server['slack_posts'])==1


def test_manifest_reader_accepts_legacy_and_cutoff_cannot_be_removed():
    http,store=initialized()
    raw=sa._encode(asdict(store.load().manifest));raw.pop('fresh_after',None)
    assert sa._manifest(raw,store.config).fresh_after is None
    sa.recover_fresh(store,cutoff=NOW,now=NOW,apply=True)
    with pytest.raises(sa.LedgerUnavailable):
        store.update_manifest(replace(store.load().manifest,fresh_after=None))


def test_orphan_preview_refuses_repair_without_any_write():
    import json
    from test_slack_alerts import LEDGER_ISSUE
    http,store=initialized()
    store.put(make_entry())
    issue=http.server['issues'][LEDGER_ISSUE]
    marker,raw=issue['body'].split('\n',1)
    doc=json.loads(raw);doc['manifest']['shard_count']=0
    issue['body']=marker+'\n'+json.dumps(doc)
    before=deepcopy(http.server);http.requests.clear()
    with pytest.raises(sa.LedgerUnavailable):
        sa.recover_fresh(make_store(http),cutoff=NOW,now=NOW)
    assert http.server==before
    assert all(r['method']=='GET' for r in http.requests)


def test_signal_expiring_during_verified_writes_is_not_posted():
    prior=delivery_fixture()
    sa.recover_fresh(make_store(prior),cutoff=NOW,now=NOW,apply=True)
    def setup(http,store):
        attempted=False
        def boundary(stage,when,url):
            nonlocal attempted
            if stage=='attempting' and when=='after': attempted=True
            if attempted and stage=='manifest' and when=='after': http.clock.sleep(2)
        http.boundary=boundary
    summary,http=forward_run(prior,now=NOW+timedelta(seconds=599),setup=setup)
    assert not http.server['slack_posts']
    assert restored(http).entries[101].reason=='fresh_only_expired_before_post'


def test_old_comment_with_fresh_event_passes_full_sender_after_recovery():
    from test_slack_alerts import make_config
    prior=delivery_fixture(queued=False)
    comment=prior.server['comments'][101]
    comment['created_at']=(NOW-timedelta(days=6)).isoformat()
    source=sa.parse_source(comment,prior.server['issues'][7],make_config())
    store=make_store(prior);store.put(make_entry(source))
    assert sa.recover_fresh(store,cutoff=NOW,now=NOW,apply=True)['quarantined']==0
    summary,http=forward_run(prior,now=NOW+timedelta(seconds=2))
    assert summary.delivered==1 and len(http.server['slack_posts'])==1
