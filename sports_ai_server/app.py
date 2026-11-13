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
 return jsonify({"configured":bool(key()),"events":matches[:500],"log":logs,"last_sync":kv("last_sync"),"remaining":kv("remaining","unavailable"),"balance":round(balance,2),"profit":round(winnings,2),"picks":picks,"winrate":round(100*sum(x["status"]=="won" for x in settled)/len(settled),1) if settled else None})
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
*{box-sizing:border-box}body{font:14px/1.5 system-ui,"Malgun Gothic",sans-serif;background:#0b1220;color:#ecf4ff;margin:0}header{padding:15px 4%;background:#142039;border-bottom:1px solid #2b4162;display:flex;justify-content:space-between}main{max-width:1320px;margin:auto;padding:20px}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card,.panel{background:#16253d;border:1px solid #2d4362;border-radius:12px;padding:15px}h1{font-size:22px;margin:0 0 6px}h2{font-size:17px;margin:0 0 10px}button,input,select{padding:9px 12px;border-radius:8px;background:#263d5e;color:white;border:1px solid #4a6d99;font:inherit}button{cursor:pointer}button:hover{background:#375780}button.blue{background:#2276e4}.line{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:10px 0}.muted{color:#afc2db}.big{font-size:24px;font-weight:800}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%}td,th{padding:11px 9px;border-bottom:1px solid #304660;text-align:left}th{font-size:12px;color:#b5c9e3;white-space:nowrap}td{vertical-align:middle}.positive{color:#69eab6}.warning{color:#ffcb88}.pill{background:#304d76;border-radius:8px;padding:4px 9px;white-space:nowrap}.right{text-align:right}.toolbar{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin:12px 0}.toolbar input{flex:1;min-width:150px}.panel{margin:13px 0}#notice{white-space:pre-wrap;font-size:13px}small{display:block;color:#adc2dd}#rows tr:hover{background:#223653}.book{color:#a7c9f8}.srow{display:flex;justify-content:space-between;gap:12px;align-items:center}.details{border:1px solid #384f71;padding:8px 12px;border-radius:9px}.details summary{cursor:pointer}.option-row{display:flex;gap:12px;align-items:center;padding:8px 0;border-bottom:1px solid #324763;flex-wrap:wrap}.option-row:last-child{border:0}.nowrap{white-space:nowrap}@media(max-width:700px){.cards{grid-template-columns:repeat(2,1fr)}main{padding:12px}.card,.panel{padding:12px}.big{font-size:20px}}
</style><header><b>SPORTS AI · AUTO</b><a href="/logout" style="color:#c2d6f4">로그아웃</a></header><main><h1>스포츠 경기 분석</h1><p class="muted">종목 선택 → 경기 비교 → 가상 기록. 시장 배당을 비교한 참고용 데이터이며, AI가 독립적으로 예측한 승률은 아닙니다.</p>
<div class="cards"><div class="card"><div class="muted">가상 잔액</div><div class="big" id="balance">$1,000</div></div><div class="card"><div class="muted">정산 승률</div><div class="big" id="winrate">—</div></div><div class="card"><div class="muted">정산 손익</div><div class="big" id="profit">$0</div></div><div class="card"><div class="muted">표시 경기</div><div class="big" id="count">0</div></div></div>
<div class="panel"><div class="srow"><h2>데이터 연결</h2><span class="pill" id="connection">확인 중</span></div><div class="line"><input id="key" type="password" autocomplete="off" placeholder="The Odds API 키 · 필요할 때만 입력"><button onclick="configure()">API 키 연결</button><button class="blue" onclick="sync()">경기 업데이트</button><button onclick="settle()">결과 정산</button></div><div id="notice" class="muted"></div><div id="stats" class="muted"></div></div>
<div class="panel"><div class="srow"><h2>경기별 배당 비교</h2><span class="muted" id="filtered"></span></div>
<div class="toolbar"><label for="sport">종목</label><select id="sport" onchange="changed()"><option value="ALL">전체 종목</option><option value="basketball_nba">🏀 NBA</option><option value="americanfootball_nfl">🏈 NFL</option><option value="baseball_mlb">⚾ MLB</option><option value="icehockey_nhl">🏒 NHL</option><option value="mma_mixed_martial_arts">🥊 UFC / MMA</option><option value="soccer_epl">⚽ EPL</option><option value="soccer_usa_mls">⚽ MLS</option><option value="soccer_spain_la_liga">⚽ La Liga</option></select><input id="search" oninput="changed()" placeholder="팀 · 선수 · 경기 검색"><select id="sort" onchange="changed()"><option value="time">경기 시간순</option><option value="gap">배당 차이 큰 순</option></select></div>
<p class="muted">경기 하나당 한 줄만 표시합니다. <b>선택지 비교</b>를 눌러 해당 경기의 모든 베팅 옵션과 스포츠북을 확인할 수 있습니다. 배당 차이는 예상 수익률이 아닙니다.</p>
<div class="scroll"><table><thead><tr><th>종목 / 경기</th><th>시작 · 현지 시간</th><th>시장 기준 대표 선택지</th><th>확률 · 배당</th><th>비교</th></tr></thead><tbody id="rows"></tbody></table></div>
<div class="line" style="justify-content:space-between"><span id="pageinfo" class="muted"></span><span class="line"><button onclick="prev()">이전</button><button onclick="next()">다음</button></span></div></div>
<div class="panel"><h2>가상 베팅 기록</h2><div class="scroll"><table><thead><tr><th>선택</th><th>금액</th><th>배당</th><th>상태</th></tr></thead><tbody id="picks"></tbody></table></div></div>
<p class="muted">실제 베팅 주문은 실행하지 않습니다. 무료 Render 서버에서는 재시작이나 재배포 시 임시 키·기록이 사라질 수 있으며, 자동 실행도 보장되지 않습니다.</p></main>
<script>
let state=null,page=0,pageSize=15,filtered=[];
const $=id=>document.getElementById(id);
const safe=s=>String(s??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;':'&quot;',"'":'&#39;'}[x]));
const name=s=>({'basketball_nba':'NBA','americanfootball_nfl':'NFL','baseball_mlb':'MLB','icehockey_nhl':'NHL','mma_mixed_martial_arts':'UFC / MMA','soccer_epl':'EPL','soccer_usa_mls':'MLS','soccer_spain_la_liga':'La Liga'}[s]||s);
async function api(url,data){let r=await fetch(url,{method:data?'POST':'GET',headers:{"Content-Type":"application/json"},body:data?JSON.stringify(data):undefined});let j=await r.json();if(!r.ok)throw Error(j.error||r.status);return j}
function changed(){page=0;render()}
function render(){
 if(!state)return;
 const term=$('search').value.trim().toLowerCase(),sport=$('sport').value;
 filtered=state.events.filter(v=>(sport==='ALL'||v.sport===sport)&&(!term||(v.match+' '+v.sport).toLowerCase().includes(term)));
 filtered.sort((a,b)=>$('sort').value==='gap'?Math.max(...b.options.map(o=>o.gap_pct))-Math.max(...a.options.map(o=>o.gap_pct)):a.start.localeCompare(b.start));
 $('count').textContent=filtered.length;
 $('filtered').textContent=filtered.length+'경기';
 const pages=Math.max(1,Math.ceil(filtered.length/pageSize));page=Math.min(page,pages-1);
 $('pageinfo').textContent=(page+1)+' / '+pages+' 페이지 · 한 페이지 15경기';
 $('rows').innerHTML=filtered.slice(page*pageSize,(page+1)*pageSize).map(v=>{
 const o=v.options[0],idx=state.events.indexOf(v);
 return '<tr><td><b>'+safe(name(v.sport))+'</b><small>'+safe(v.match)+'</small></td><td class="nowrap">'+safe(new Date(v.start).toLocaleString('ko-KR'))+'</td><td>'+safe(o.selection)+'<small class="book">'+safe(o.book)+'</small></td><td class="nowrap">'+o.market_pct+'% · '+o.decimal+'</td><td><details class="details"><summary>선택지 비교 ('+v.options.length+')</summary>'+v.options.map(z=>'<div class="option-row"><b>'+safe(z.selection)+'</b><span>'+z.market_pct+'% · 배당 '+z.decimal+'</span><span class="muted">'+safe(z.book)+'</span><button onclick="paper('+idx+','+v.options.indexOf(z)+')">가상 $10</button></div>').join('')+'</details></td></tr>'
 }).join('')||'<tr><td colspan="5">선택한 종목에 예정된 비교 가능 경기가 없습니다. 다른 종목을 선택해보세요.</td></tr>';
 $('picks').innerHTML=state.picks.map(p=>'<tr><td>'+safe(p.selection)+'</td><td>$'+p.stake+'</td><td>'+p.decimal+'</td><td>'+safe(p.status)+'</td></tr>').join('')||'<tr><td colspan="4">아직 가상 기록 없음</td></tr>';
}
async function load(){
 state=await api('/api/status');$('balance').textContent='$'+state.balance.toFixed(2);$('profit').textContent='$'+state.profit.toFixed(2);$('winrate').textContent=state.winrate===null?'—':state.winrate+'%';
 $('connection').textContent=state.configured?'API 연결됨':'API 키 필요';
 $('stats').textContent='마지막 업데이트: '+(state.last_sync?new Date(state.last_sync).toLocaleString('ko-KR'):'없음')+' · 스포츠별 수집: '+state.log.map(x=>name(x.sport)+' '+x.events+'건'+(x.err?' (오류)':'')).join(' / ');
 render()
}
async function action(f,success){$('notice').textContent='작업 중...';try{let r=await f();$('notice').textContent=success(r);await load()}catch(ex){$('notice').textContent='오류: '+ex.message}}
function configure(){const k=$('key').value;$('key').value='';action(()=>api('/api/key',{key:k}),()=> 'API 키가 확인되었습니다. 경기 업데이트를 누르세요.')}
function sync(){action(()=>api('/api/sync',{}),r=>'경기 수집 완료: '+(r.fetched||0)+'건'+(r.sports||[]).filter(x=>x.error).map(x=>'\n'+x.sport+': '+x.error).join(''))}
function settle(){action(()=>api('/api/settle',{}),r=>'정산 완료: '+r.settled+'건'+(r.notes?.length?'\n'+r.notes.join('\n'):''))}
function paper(i,j){const v=state.events[i],o=v.options[j];if(confirm('실제 돈이 아닌 가상 $10을 '+o.selection+'에 기록할까요?'))action(()=>api('/api/paper',{id:v.id,selection:o.selection}),()=> '가상 베팅이 기록되었습니다.')}
function prev(){page=Math.max(0,page-1);render()}function next(){page=Math.min(Math.max(0,Math.ceil(filtered.length/pageSize)-1),page+1);render()}
load().catch(ex=>$('notice').textContent=ex.message);setInterval(()=>load().catch(()=>{}),60000);
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
