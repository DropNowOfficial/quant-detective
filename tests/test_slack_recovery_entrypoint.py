import importlib
from copy import deepcopy
import pytest
from market_data import slack_alerts as sa
from test_slack_alerts import initialized, make_entry, NOW


def recovery_module():
    return importlib.import_module('market_data.slack_recovery')


def test_entrypoint_requires_manual_main_and_explicitly_disabled_sender():
    m=recovery_module()
    env={'GITHUB_REPOSITORY':sa.REPO,'GITHUB_REPOSITORY_ID':str(sa.REPO_ID),
         'GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'workflow_dispatch',
         'QD_SLACK_ENABLED':'false'}
    m.check_context(env)
    for key,value in [('QD_SLACK_ENABLED','true'),('QD_SLACK_ENABLED',''),
                      ('GITHUB_EVENT_NAME','schedule'),('GITHUB_REF','refs/heads/other'),
                      ('GITHUB_REPOSITORY_ID','1')]:
        with pytest.raises(ValueError):m.check_context(env|{key:value})


def test_snapshot_preview_and_apply_are_separate_and_changes_refuse_apply():
    m=recovery_module();http,store=initialized();store.put(make_entry())
    before=deepcopy(http.server);http.requests.clear()
    backup,preview=m.preview(store)
    assert http.server==before and all(r['method']=='GET' for r in http.requests)
    http.server['issues'][store.config.ledger_issue]['title']='external modification'
    with pytest.raises(sa.LedgerUnavailable):m.apply_backup(store,backup)
    assert store.load().manifest.fresh_after is None


def test_apply_verifies_persistent_cutoff_and_preserves_all_sources():
    m=recovery_module();http,store=initialized();store.put(make_entry())
    backup,preview=m.preview(store)
    result=m.apply_backup(store,backup)
    assert result['verified'] is True
    assert store.load().manifest.fresh_after==NOW
    assert len(store.load().entries)==1


def test_workflow_uses_same_serial_group_and_does_not_receive_webhook():
    from pathlib import Path
    text=Path('.github/workflows/slack-recovery.yml').read_text()
    assert 'group: hosted-core-watch-fallback' in text
    assert 'cancel-in-progress: false' in text
    assert "vars.QD_SLACK_ENABLED == 'false'" in text
    assert 'QD_SLACK_WEBHOOK_URL' not in text
    assert text.index('actions/upload-artifact@')<text.index('slack_recovery apply')
