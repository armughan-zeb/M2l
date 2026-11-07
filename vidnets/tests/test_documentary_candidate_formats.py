import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from services.youtube_pipeline_v2 import _score_candidate


def test_real_finance_story_rejects_anime_before_download():
    candidate={'title':'Reborn 3 Years Earlier, the Former Berserker','description':'anime recap','channel':'Story recap'}
    assert _score_candidate(candidate,'monthly investment','Investment contributions compound over thirty years.','reborn years',{'domain_category':'finance'}) == -999
    assert candidate['v2_reject_reason']=='fictional_footage_for_real_subject'


def test_finance_story_does_not_accept_spreadsheet_tutorial_from_drifting_query():
    candidate={'title':'Summarize Sales Data by Year or Month or Quarter','description':'Excel pivot table tutorial','channel':'Spreadsheet school'}
    assert _score_candidate(candidate,'monthly investment','Returns change over thirty years.','sales data tutorial',{'domain_category':'finance'}) == -999
    assert candidate['v2_reject_reason']=='software_tutorial_for_financial_story'


def test_genuine_spreadsheet_story_is_not_blocked_by_new_veto():
    candidate={'title':'Build an Excel pivot table','description':'Excel spreadsheet walkthrough','channel':'Spreadsheet school'}
    _score_candidate(candidate,'Excel spreadsheet','Build an Excel pivot table.','Excel pivot table',{'domain_category':'finance'})
    assert candidate.get('v2_reject_reason')!='software_tutorial_for_financial_story'
