"""Read the independent ETF performance workbook and retain last verified results."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from urllib.request import Request, urlopen

from performance.publication import OFFICIAL_RESEARCH_ID,api_base,api_get,decode


PERFORMANCE_BASE = 'https://raw.githubusercontent.com/monstervincentw-glitch/growth-observatory-bank/performance-data/'


def fetch_published(path):
    request = Request(PERFORMANCE_BASE + path, headers={'Cache-Control':'no-cache'})
    with urlopen(request, timeout=5) as response:
        raw = response.read(2_000_001)
    if len(raw)>2_000_000:
        raise ValueError('Published performance response too large')
    return json.loads(raw)

class Reader:
    def __init__(self,path):
        self.path=Path(path)
        self.last=None
        self.lock=Lock()

    def refresh(self,fetch):
        with self.lock:
            if self.last is None and self.path.is_file():
                try:
                    self.last=decode(json.loads(self.path.read_text()))
                except (OSError,ValueError,KeyError,TypeError):
                    pass
            try:
                rows=fetch()
                value=decode(rows)
                if self.last and (value['through']<self.last['through'] or
                                  (value['through']==self.last['through'] and value['batch']!=self.last['batch'])):
                    raise ValueError('Performance update out of order')
                if not self.last or self.last['batch']!=value['batch']:
                    self.path.parent.mkdir(parents=True,exist_ok=True)
                    with NamedTemporaryFile(mode='w',dir=self.path.parent,delete=False) as handle:
                        temp=Path(handle.name)
                        try:
                            json.dump(rows,handle)
                            handle.close()
                            temp.replace(self.path)
                        finally:
                            temp.unlink(missing_ok=True)
                self.last=value
                return self.last,None
            except Exception as exc:
                return self.last,f'績效讀取失敗（{type(exc).__name__}），顯示上次已驗證資料或原封存。'

def fetch(sheet_id,account):
    if sheet_id==OFFICIAL_RESEARCH_ID:
        raise ValueError('Performance requires its own workbook')
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials
    credentials=Credentials.from_service_account_info(dict(account),
        scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
    with AuthorizedSession(credentials) as client:
        return api_get(client,api_base(sheet_id)+'/values/performance_release!A1:E120',
                       params={'valueRenderOption':'UNFORMATTED_VALUE'})['values']

def overlay(archive,performance):
    if not performance:
        return archive
    keys={(r['symbol'],r['start_year'],r['variant']):r for r in performance['summary']}
    return {**archive,'advisor':[r|keys.get((r['symbol'],r['start_year'],r['variant']),{}) for r in archive['advisor']]}
