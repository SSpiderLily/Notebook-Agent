import json
import pytest
from src.data.loader import NoteLoader
from src.data.parser import NoteParser
from src.data.collection import Collector
from src.infra.config import Settings

def settings(tmp):
    return Settings(_env_file=None, vault_dir=tmp, data_dir=tmp/'data', logs_dir=tmp/'logs', ignore_paths=['private/**'])

def test_recursive_hash_exclude_and_missing_vault(tmp_path):
    (tmp_path/'a').mkdir(); (tmp_path/'a'/'one.md').write_text('# One')
    (tmp_path/'.obsidian').mkdir(); (tmp_path/'.obsidian'/'x.md').write_text('x')
    loader=NoteLoader(tmp_path, settings(tmp_path))
    rows=loader.load_all(); assert len(rows)==1
    assert rows[0]['relative_path']=='a/one.md'; assert len(rows[0]['note_id'])==64
    with pytest.raises(FileNotFoundError): NoteLoader(tmp_path/'none').scan_directory()

def test_parser_frontmatter_tags_links_and_code(tmp_path):
    content='''---\ntags: [项目, nested/test]\nactive: true\n---\n# 标题\n正文 #中文 #nested/test [[目标|别名]] https://x/#url\n```\n#代码 #bad\n```\n'''
    parsed=NoteParser().parse(content)
    assert parsed['metadata']['active'] is True
    assert parsed['title']=='标题'; assert parsed['keywords']==['中文','nested/test']
    assert parsed['links']==[{'target':'目标','alias':'别名'}]

def test_changes_ignore_and_estimate(tmp_path):
    (tmp_path/'private').mkdir(); (tmp_path/'private'/'x.md').write_text('# x')
    (tmp_path/'ok.md').write_text('# ok')
    collector=Collector(tmp_path, settings(tmp_path)); rows=collector.collect()
    assert rows[0]['vault_status']=='active'; assert any(r['vault_status']=='ignored' for r in rows)
    old={'ok.md':{'content_hash':'old'},'gone.md':{'content_hash':'x'}}
    change=collector.diff(rows,old); assert 'ok.md' in change.modified; assert change.missing==['gone.md']
    estimate=collector.estimate(rows,settings(tmp_path)); assert estimate.notes==1; assert estimate.calls==1
