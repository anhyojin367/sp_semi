from types import SimpleNamespace

import pytest

import sp_app as app


@pytest.mark.parametrize('change', ['none', 'modified', 'deleted', 'missing_signature', 'missing_summary', 'summary_is_directory'])
def test_summary_shortcut_requires_the_same_pdf_and_real_summary_files(monkeypatch, tmp_path, change):
    pdf = tmp_path / 'sp.pdf'
    pdf.write_bytes(b'original')
    before, after = tmp_path / 'before.csv', tmp_path / 'after.csv'
    before.write_text('before', encoding='utf-8')
    after.write_text('after', encoding='utf-8')
    stat = pdf.stat()
    record = {'status':'completed', 'detail_fingerprint':'d', 'artifact_key':'test',
              'pdf_sig':{'size':stat.st_size, 'mtime_ns':stat.st_mtime_ns},
              'summary_before_path':str(before), 'summary_after_path':str(after)}
    if change == 'modified':
        pdf.write_bytes(b'changed after the last judgement')
    elif change == 'deleted':
        pdf.unlink()
    elif change == 'missing_signature':
        record.pop('pdf_sig')
    elif change == 'missing_summary':
        record['summary_after_path'] = ''
    elif change == 'summary_is_directory':
        record['summary_after_path'] = str(tmp_path)
    monkeypatch.setattr(app, '_load_judgement_status_index', lambda: {})
    monkeypatch.setattr(app, '_status_record_for_doc', lambda *a: record)
    monkeypatch.setattr(app, 'resolve_domain_detail_profile', lambda *a: SimpleNamespace(fingerprint='d'))
    monkeypatch.setattr(app, 'JUDGEMENT_STATUS_DIR', tmp_path / 'missing-status-fallback')
    value = app._cached_simulation_artifacts_for_doc(SimpleNamespace(company='x',product='y'),
        pdf_path=pdf, permit_paths=[], csv_dir=tmp_path)
    assert (value is not None) == (change == 'none')
