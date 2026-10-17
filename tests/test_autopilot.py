import json
import pathlib
import sys
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "scripts"))

from publish import validate_entry, load_queue, eligible, uploaded_markers, uploads_playlist, download_video
from produce import load_stories, choose_story, estimated_upper_cost, extract_video_url, overlay_file


def test_queue_is_valid_and_unique():
    q=load_queue(ROOT/"content"/"queue.json")
    assert len(q)==3
    assert len({x['id'] for x in q}) == 3
    assert all(x['rights_confirmed'] for x in q)


def test_expected_dates():
    q=load_queue(ROOT/"content"/"queue.json")
    assert not eligible(q[0],datetime(2026,10,17,tzinfo=timezone.utc))
    assert eligible(q[0],datetime(2026,10,19,tzinfo=timezone.utc))


def test_bad_metadata_is_rejected():
    x=load_queue(ROOT/"content"/"queue.json")[0].copy()
    x['rights_confirmed']=False
    with pytest.raises(AssertionError):validate_entry(x)
    x['rights_confirmed']=True
    x['id']='../../bad'
    with pytest.raises(AssertionError):validate_entry(x)


def test_url_allowlist_blocks_other_domains(tmp_path):
    with pytest.raises(ValueError): download_video('http://example.com/video.mp4',tmp_path/'x.mp4')
    with pytest.raises(ValueError): download_video('https://127.0.0.1/private',tmp_path/'x.mp4')


def test_uploaded_markers_and_account_id():
    youtube=Mock()
    youtube.channels().list().execute.return_value={"items":[{"id":"UCPV98kjwLUta0vuiy3fx64A","contentDetails":{"relatedPlaylists":{"uploads":"UPzz"}}}]}
    assert uploads_playlist(youtube)=="UPzz"
    youtube.playlistItems().list().execute.return_value={"items":[{"snippet":{"resourceId":{"videoId":"v1"}}}]}
    youtube.videos().list().execute.return_value={"items":[{"snippet":{"description":"demo\nLRC-ID:lrc-test-123\nmore"}}]}
    assert 'lrc-test-123' in uploaded_markers(youtube,'UPzz')


def test_stories_sources_and_budget():
    stories=load_stories(ROOT/"content"/"stories.json")
    assert len(stories)==2
    assert all(estimated_upper_cost(s)<=5 for s in stories)
    assert choose_story(stories,set(),datetime(2026,10,20,tzinfo=timezone.utc)) is None
    assert choose_story(stories,set(),datetime(2026,10,26,tzinfo=timezone.utc))['id']=='lrc-bloop-noaa-004'
    assert choose_story(stories,{'lrc-bloop-noaa-004'},datetime(2026,11,2,tzinfo=timezone.utc))['id']=='lrc-voyager-disco-005'


def test_higgsfield_status_response_formats():
    assert extract_video_url({'status':'completed','video':{'url':'https://a.test/vid.mp4'}})=='https://a.test/vid.mp4'
    assert extract_video_url({'status':'completed','result':{'video':{'url':'https://a.test/vid2.mp4'}}})=='https://a.test/vid2.mp4'
    with pytest.raises(RuntimeError): extract_video_url({'status':'completed'})


def test_caption_rendering(tmp_path):
    p=tmp_path/'overlay.png'
    overlay_file('UNA SEÑAL EXTRAÑA DE 1997',p)
    from PIL import Image
    with Image.open(p) as im:
        assert im.size==(720,1280)
        assert im.mode=='RGBA'
