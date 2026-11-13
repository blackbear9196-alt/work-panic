import os,sqlite3,requests,threading,time,statistics,datetime,json,math
from flask import Flask,request,session,redirect,render_template_string,jsonify
from functools import wraps

app=Flask(__name__)
app.secret_key=os.environ["SESSION_SECRET"]
ADMIN=os.environ["ADMIN_PASSWORD"]
DB=os.environ.get("SPORTS_DB","/tmp/sports-ai.sqlite")
SPORTS={"NBA":"basketball_nba","NFL":"americanfootball_nfl","MLB":"baseball_mlb","NHL":"icehockey_nhl","UFC / MMA":"mma_mixed_martial_arts","EPL":"soccer_epl","MLS":"soccer_usa_mls","La Liga":"soccer_spain_la_liga"}
MUTEX=threading.Lock()
def db():
 c=sqlite3.connect(DB,timeout=20)
 c.row_factory=sqlite3.Row
 return c
def init():
 with db() as c:
  c.execute("create table if not exists settings(k text primary key,v text)")
  c.execute("create table if not exists events(id text primary key,sport text,home text,away text,start text,raw text,updated text)")
  c.execute("create table if not exists picks(id integer primary key,event_id text,sport text,selection text,decimal real,stake real,status text default 'pending',payout real default 0,created text)")
  c.execute("create table if not exists logs(sport text primary key,events integer,err text,at text)")
init()
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def kv(k,default=""):
 with db() as c:r=c.execute("select v from settings where k=?",(k,)).fetchone()
 return r["v"] if r else default
def put(k,v):
 with db() as c:c.execute("insert or replace into settings(k,v) values(?,?)",(k,str(v)))
def key():return os.getenv("ODDS_API_KEY","").strip() or kv("odds_key")
def login_required(f):
 @wraps(f)
 def wrap(*a,**kw):
  if not session.get("authenticated"):return redirect("/login") if request.method=="GET" else (jsonify(error="먼저 로그인하세요"),401)
  return f(*a,**kw)
 return wrap
LOGIN='''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font-family:system-ui;background:#091122;color:white;display:grid;place-items:center;height:90vh}.card{background:#17253c;border-radius:18px;padding:35px;max-width:400px;width:85%}input,button{padding:14px;border-radius:8px;width:100%;box-sizing:border-box;margin:8px 0}button{background:#2779e3;color:white;border:0}</style><div class="card"><h1>SPORTS AI</h1><p>Private analysis dashboard · Paper only</p><form method=post><input type=password name=password placeholder="관리자 비밀번호" required><button>로그인</button></form><p>{{error}}</p></div>'''
@app.route("/login",methods=["GET","POST"])
def login():
 error=""
 if request.method=="POST":
  import hmac
  if hmac.compare_digest(request.form.get("password",""),ADMIN):
   session.clear();session["authenticated"]=True;return redirect("/")
  error="비밀번호가 다릅니다."
 return render_template_string(LOGIN,error=error)
@app.get("/logout")
def logout():session.clear();return redirect("/login")
def fetch(path,params):
 r=requests.get("https://api.the-odds-api.com/v4/"+path,params={**params,"apiKey":key()},timeout=20)
 if "x-requests-remaining" in r.headers:put("remaining",r.headers["x-requests-remaining"])
 if not r.ok:raise RuntimeError(f"HTTP {r.status_code}: {r.text[:140]}")
 return r.json()
def candidates(e):
 books=[]
 for b in e.get("bookmakers",[]):
  for m in b.get("markets",[]):
   if m.get("key")!="h2h":continue
   try:
    od=[(z["name"],float(z["price"])) for z in m.get("outcomes",[]) if float(z["price"])>1]
   except (KeyError,ValueError,TypeError):continue
   if len(od)<2:continue
   denom=sum(1/d for _,d in od)
   books.append((b.get("title","book"),od,{name:(1/d)/denom for name,d in od}))
 if len(books)<2:return []
 names=set().union(*(set(x[2]) for x in books))
 result=[]
 for name in names:
  ps=[b[2][name] for b in books if name in b[2]]
  if len(ps)<2:continue
  fair=statistics.median(ps)
  best=max(((d,b[0]) for b in books for n,d in b[1] if n==name),default=None)
  if not best:continue
  edge=fair*best[0]-1
  result.append({"selection":name,"market_pct":round(fair*100,1),"decimal":round(best[0],2),"book":best[1],"gap_pct":round(edge*100,1),"observations":len(ps)})
 return sorted(result,key=lambda x:x["gap_pct"],reverse=True)
def sync():
 if not key():return {"error":"API 키가 아직 설정되지 않았습니다."}
 out=[];total=0
 with MUTEX:
  for label,sport in SPORTS.items():
   if kv("remaining","999999").isdigit() and int(kv("remaining","999999"))<=25:
    out.append({"sport":label,"events":0,"error":"API 사용량 보호로 중단"});break
   try:
    data=fetch("sports/"+sport+"/odds/",{"regions":"us","markets":"h2h","oddsFormat":"decimal"})
    with db() as c:
     for e in data:
      if not e.get("id"):continue
      c.execute("insert or replace into events values(?,?,?,?,?,?,?)",(e["id"],sport,e.get("home_team",""),e.get("away_team",""),e.get("commence_time",""),json.dumps(e),now()))
    out.append({"sport":label,"events":len(data),"error":""});total+=len(data)
   except Exception as ex:out.append({"sport":label,"events":0,"error":str(ex)[:160]})
   with db() as c:c.execute("insert or replace into logs values(?,?,?,?)",(sport,out[-1]["events"],out[-1]["error"],now()))
  put("last_sync",now())
 return {"fetched":total,"sports":out}
def score_results():
 with db() as c:picks=[dict(x) for x in c.execute("select * from picks where status='pending'")]
 if not key():return {"settled":0,"message":"API 키 없음"}
 settled=0;notes=[]
 for sport in set(p["sport"] for p in picks):
  try:results=fetch("sports/"+sport+"/scores/",{"daysFrom":3})
  except Exception as ex:notes.append(sport+": "+str(ex)[:100]);continue
  completed={e["id"]:e for e in results if e.get("completed")}
  for p in picks:
   if p["sport"]!=sport or p["event_id"] not in completed:continue
   scores=completed[p["event_id"]].get("scores") or []
   try:d={x["name"]:float(x["score"]) for x in scores}
   except (ValueError,KeyError,TypeError):continue
   if len(d)<2:continue
   maximum=max(d.values());winners=[name for name,n in d.items() if n==maximum]
   if len(winners)!=1:continue
   won=p["selection"]==winners[0]
   with db() as c:c.execute("update picks set status=?,payout=? where id=? and status='pending'",("won" if won else "lost",round(p["stake"]*p["decimal"],2) if won else 0,p["id"]))
   settled+=1
 return {"settled":settled,"notes":notes}
@app.get("/")
@login_required
def home():return render_template_string(PAGE)
@app.get("/api/status")
@login_required
def status():
 with db() as c:
  rows=[dict(x) for x in c.execute("select * from events order by start")]
  picks=[dict(x) for x in c.execute("select * from picks order by id desc limit 200")]
  logs=[dict(x) for x in c.execute("select * from logs")]
 current=now()
 matches=[]
 for r in rows:
  if r["start"]<current[:19]:continue
  e=json.loads(r["raw"])
  opts=candidates(e)
  if not opts:continue
  matches.append({"id":r["id"],"sport":r["sport"],"match":r["away"]+" vs "+r["home"],"start":r["start"],"options":opts})
 matches=sorted(matches,key=lambda e:max(o["gap_pct"] for o in e["options"]),reverse=True)
 settled=[x for x in picks if x["status"] in ("won","lost")]
 winnings=sum(x["payout"]-x["stake"] for x in settled)
 balance=1000+winnings-sum(x["stake"] for x in picks if x["status"]=="pending")
 return jsonify({"configured":bool(key()),"events":matches[:120],"log":logs,"last_sync":kv("last_sync"),"remaining":kv("remaining","unavailable"),"balance":round(balance,2),"profit":round(winnings,2),"picks":picks,"winrate":round(100*sum(x["status"]=="won" for x in settled)/len(settled),1) if settled else None})
@app.post("/api/key")
@login_required
def save_key():
 k=(request.json or {}).get("key","").strip()
 if len(k)<10 or len(k)>300:return jsonify(error="API 키를 확인하세요"),400
 try:
  r=requests.get("https://api.the-odds-api.com/v4/sports/",params={"apiKey":k},timeout=20)
  if not r.ok:return jsonify(error=f"키 확인 실패 HTTP {r.status_code}"),400
 except requests.RequestException as ex:return jsonify(error=str(ex)),400
 put("odds_key",k)
 return jsonify(ok=True,note="키가 서버 임시 저장소에 등록되었습니다. 무료 서버 재배포/재시작 시 사라질 수 있습니다.")
@app.post("/api/sync")
@login_required
def manual_sync():return jsonify(sync())
@app.post("/api/paper")
@login_required
def paper():
 data=request.get_json() or {}
 with db() as c:r=c.execute("select * from events where id=?",(data.get("id"),)).fetchone()
 if not r or r["start"]<now()[:19]:return jsonify(error="경기가 없거나 시작되었습니다."),400
 opt=next((x for x in candidates(json.loads(r["raw"])) if x["selection"]==data.get("selection")),None)
 if not opt:return jsonify(error="선택한 배당을 찾을 수 없습니다."),400
 with db() as c:
  if c.execute("select 1 from picks where event_id=? and selection=?",(r["id"],opt["selection"])).fetchone():return jsonify(error="이미 기록됨"),400
  # Fixed paper stake. No real orders.
  c.execute("insert into picks(event_id,sport,selection,decimal,stake,status,payout,created) values(?,?,?,?,?,'pending',0,?)",(r["id"],r["sport"],opt["selection"],opt["decimal"],10,now()))
 return jsonify(ok=True)
@app.post("/api/settle")
@login_required
def manual_settle():return jsonify(score_results())
@app.get("/health")
def health():return jsonify(ok=True)
PAGE='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SPORTS AI AUTO</title><style>
*{box-sizing:border-box}body{font:14px system-ui;background:#0b1220;color:#ecf4ff;margin:0}header{padding:16px 4%;background:#142039;border-bottom:1px solid #2b4162;display:flex;justify-content:space-between}main{max-width:1320px;margin:auto;padding:20px}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card,.panel{background:#16253d;border:1px solid #2d4362;border-radius:12px;padding:16px}h1{font-size:23px}h2{font-size:17px}button,input{padding:10px;border-radius:8px;background:#273e60;color:white;border:1px solid #4a6d99}button{cursor:pointer}button.blue{background:#2276e4}.line{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:16px 0}.muted{color:#afc2db}.big{font-size:25px;font-weight:800}.scroll{overflow:auto}table{border-collapse:collapse;width:100%}td,th{padding:10px;border-bottom:1px solid #304660;text-align:left;white-space:nowrap}.positive{color:#69eab6}.warning{color:#ffcb88}#notice{white-space:pre-wrap}@media(max-width:700px){.cards{grid-template-columns:repeat(2,1fr)}main{padding:12px}}</style><header><b>SPORTS AI · AUTO RESEARCH</b><a href="/logout" style="color:#c2d6f4">로그아웃</a></header><main><h1>NBA · NFL · MLB · NHL · UFC / MMA · 축구</h1><p class="muted">배당 기반 시장 확률 및 여러 스포츠북 간 가격 차이. 독립적인 AI 승률 예측 아님. 실전 베팅 주문 없음.</p>
<div class="cards"><div class="card"><div class="muted">가상 잔액</div><div class="big" id="balance">$1,000</div></div><div class="card"><div class="muted">정산 승률</div><div class="big" id="winrate">—</div></div><div class="card"><div class="muted">정산 손익</div><div class="big" id="profit">$0</div></div><div class="card"><div class="muted">비교 가능 경기</div><div class="big" id="count">0</div></div></div>
<div class="panel" style="margin:16px 0"><h2>API 연결 및 자동 분석</h2><p class="muted">The Odds API 키를 이 비공개 관리자 화면에 입력하세요. 무료 Render 서버에서는 재시작 시 임시 저장한 키와 기록이 삭제될 수 있습니다.</p><div class="line"><input id="key" type="password" autocomplete="off" placeholder="The Odds API 키"><button onclick="configure()">API 키 연결</button><button class="blue" onclick="sync()">모든 경기 새로고침</button><button onclick="settle()">결과 정산</button></div><div id="notice"></div><p class="muted" id="stats"></p></div>
<div class="panel"><h2>경기 분석 후보 — 시장 배당 기반</h2><p class="warning">표시된 가격 차이는 예측 수익률이 아닙니다. 배당이 유리해 보여도 손실이 발생할 수 있습니다.</p><div class="scroll"><table><thead><tr><th>종목 / 경기</th><th>시작 (현지)</th><th>선택</th><th>시장 기준 확률</th><th>최고 배당</th><th>가격 차이</th><th>가상 기록</th></tr></thead><tbody id="rows"></tbody></table></div></div>
<div class="panel" style="margin-top:16px"><h2>가상 기록</h2><div class="scroll"><table><thead><tr><th>선택</th><th>금액</th><th>배당</th><th>상태</th></tr></thead><tbody id="picks"></tbody></table></div></div>
<p class="muted">현재 서비스는 연구 및 가상 시뮬레이션 전용입니다. UFC 결과 API 미지원이나 무승부 경기는 자동 정산되지 않을 수 있습니다. 무료 서버에서는 24시간 실행과 기록 보존이 보장되지 않습니다.</p></main><script>
let state;const e=id=>document.getElementById(id),safe=s=>String(s??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
async function call(url,data){let r=await fetch(url,{method:data?'POST':'GET',headers:{"Content-Type":"application/json"},body:data?JSON.stringify(data):undefined});let j=await r.json();if(!r.ok)throw Error(j.error||r.status);return j}
async function load(){state=await call("/api/status");e("balance").textContent="$"+state.balance.toFixed(2);e("profit").textContent="$"+state.profit.toFixed(2);e("winrate").textContent=state.winrate===null?"—":state.winrate+"%";e("count").textContent=state.events.length;e("stats").textContent="API "+(state.configured?"연결 설정됨":"미설정")+" · 마지막 수집 "+(state.last_sync||"없음")+" · 남은 API 크레딧 "+state.remaining+" · "+state.log.map(x=>x.sport+": "+x.events+"건"+(x.err?" ("+x.err+")":"")).join(" / ");let arr=[];for(let v of state.events)for(let o of v.options){arr.push('<tr><td>'+safe(v.sport)+' · '+safe(v.match)+'</td><td>'+new Date(v.start).toLocaleString()+'</td><td>'+safe(o.selection)+' <small>'+safe(o.book)+'</small></td><td>'+o.market_pct+'%</td><td>'+o.decimal+'</td><td class="'+(o.gap_pct>0?'positive':'')+'">'+o.gap_pct+'%</td><td><button onclick="paper('+JSON.stringify(v.id).replace(/"/g,'&quot;')+','+JSON.stringify(o.selection).replace(/"/g,'&quot;')+')">가상 $10</button></td></tr>')}e("rows").innerHTML=arr.join("")||'<tr><td colspan="7">비교 가능한 경기 없음. API 키와 경기 일정을 확인하세요.</td></tr>';e("picks").innerHTML=state.picks.map(p=>'<tr><td>'+safe(p.selection)+'</td><td>$'+p.stake+'</td><td>'+p.decimal+'</td><td>'+safe(p.status)+'</td></tr>').join("")||'<tr><td colspan="4">기록 없음</td></tr>'}
async function task(fn){e("notice").textContent="처리 중...";try{let r=await fn();e("notice").textContent=JSON.stringify(r);await load()}catch(ex){e("notice").textContent="오류: "+ex.message}}
function configure(){let k=e("key").value;task(()=>call("/api/key",{key:k}));e("key").value=""}function sync(){task(()=>call("/api/sync",{}))}function settle(){task(()=>call("/api/settle",{}))}function paper(id,selection){if(confirm("실제 돈이 아닌 가상 $10 기록을 생성할까요?"))task(()=>call("/api/paper",{id,selection}))}
load().catch(ex=>e("notice").textContent=ex.message);setInterval(()=>load().catch(()=>{}),60000)
</script></html>'''
def worker():
 while True:
  try:
   if key():
    sync()
    score_results()
  except Exception as ex:put("worker_error",str(ex)[:200])
  time.sleep(12*3600)
if os.getenv("SPORTS_AUTO","1")=="1":
 threading.Thread(target=worker,daemon=True).start()
