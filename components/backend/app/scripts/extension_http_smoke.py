"""Exercise real HTTP/auth/DB with isolated synthetic records, then remove them.

No CEAC requests, no payment provider, and no existing customer rows are touched.
Run with the target backend environment; prints assertions only, never tokens.
"""
import json
import secrets
import threading
from datetime import datetime,timedelta,timezone
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from backend import application as app, extension_service as service

def main():
    now=datetime.now(timezone.utc);future=(now+timedelta(hours=1)).isoformat();stamp=now.isoformat()
    prefix='extension-smoke-'+secrets.token_hex(8);org=prefix+'-org';uid=prefix+'-user';case=prefix+'-case'
    ext='pjkehgbmklndljbaefgldodnpfgakggl'
    class QuietHandler(app.ApiHandler):
        def log_message(self,*args):pass
    http=ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
    worker=threading.Thread(target=http.serve_forever,daemon=True);worker.start()
    base=f'http://127.0.0.1:{http.server_port}'
    cookie=''
    def call(path,method='GET',data=None,token=None,origin=service.ORIGIN):
        headers={'Origin':origin,'Content-Type':'application/json'}
        if token:headers.update(Authorization='Bearer '+token,**{'X-DocFlow-Extension-Id':ext})
        elif cookie:headers['Cookie']=cookie
        req=Request(base+path,method=method,headers=headers,data=json.dumps(data).encode() if data is not None else None)
        try:
            with urlopen(req,timeout=30) as r:return r.status,json.load(r)
        except HTTPError as e:return e.code,json.load(e)
    try:
        with app.connect() as c:
            c.executescript(service.SCHEMA)
            c.execute('INSERT INTO organizations(id,name,service_country,created_at,updated_at) VALUES (?,?,?,?,?)',(org,'Extension integration test','CN',stamp,stamp))
            c.execute('INSERT INTO users(id,organization_id,name,email,role,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                      (uid,org,'Extension integration test',prefix+'@example.invalid','manager',stamp,stamp))
            payload={'id':case,'clientName':'TEST ONLY','visaType':'B1/B2','serviceCountry':'CN',
                     'extractedFields':[{'id':'personal.surname','value':'TEST','status':'confirmed'}],'branchQuestionnaire':[]}
            c.execute('INSERT INTO ds160_cases(id,organization_id,owner_user_id,visa_type,status,payload_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                      (case,org,uid,'B1/B2','draft',json.dumps(payload),stamp,stamp))
        assert call('/api/extension/jobs','POST',{'caseId':case,'extensionId':ext})[0]==401
        session=app.create_auth_session(uid);cookie=app.AUTH_COOKIE+'='+session
        assert call('/api/extension/jobs','POST',{'caseId':case,'extensionId':ext})[0]==402
        with app.connect() as c:c.execute('INSERT INTO billing_subscriptions(id,organization_id,status,starts_at,current_period_end,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                  (prefix+'-member',org,'active',stamp,future,stamp,stamp))
        code,j=call('/api/extension/jobs','POST',{'caseId':case,'extensionId':ext});assert code==201,(code,j)
        assert call('/api/extension/jobs','POST',{'caseId':case,'extensionId':ext})[0]==409
        path='/api/extension/jobs/'+j['jobId'];token=j['accessToken'];origin='chrome-extension://'+ext
        code,plan=call(path,token=token,origin=origin);assert code==200,(code,plan)
        actions=[a for p in plan['pages'] for a in p['actions']];assert any(a['id']=='personal.surname' and a['value']=='TEST' for a in actions)
        assert plan['statusUrl']==service.ORIGIN+path+'/status'
        assert call(path,token=token,origin='https://evil.example')[0]==403
        assert call(path,token='wrong',origin=origin)[0]==401
        code,_=call(path+'/status','POST',{'state':'running','eventSequence':1,'completedFields':1,'lastActionId':'personal.surname'},token,origin);assert code==200
        assert call(path)[1]['completedFields']==1
        with app.connect() as c:c.execute("UPDATE billing_subscriptions SET status='revoked' WHERE organization_id=?",(org,))
        assert call(path+'/status','POST',{'state':'running','eventSequence':2},token,origin)[0]==402
        assert call(path,'DELETE')[0]==200
        assert call(path,token=token,origin=origin)[0]==401
        print(json.dumps({'ok':True,'checks':['login_required','paid_only','task_creation','concurrency','plan_delivery','https_status_url','origin_denial','token_denial','progress','membership_revocation','stop_scrub'],'syntheticRecordsRemoved':True}))
    finally:
        http.shutdown();http.server_close();worker.join()
        with app.connect() as c:
            c.execute('DELETE FROM extension_job_events WHERE job_id IN (SELECT id FROM extension_jobs WHERE organization_id=?)',(org,))
            c.execute('DELETE FROM extension_jobs WHERE organization_id=?',(org,))
            c.execute('DELETE FROM billing_subscriptions WHERE organization_id=?',(org,))
            c.execute('DELETE FROM ds160_cases WHERE organization_id=?',(org,))
            c.execute('DELETE FROM auth_sessions WHERE user_id=?',(uid,))
            c.execute('DELETE FROM users WHERE id=?',(uid,))
            c.execute('DELETE FROM organizations WHERE id=?',(org,))

if __name__=='__main__':main()
