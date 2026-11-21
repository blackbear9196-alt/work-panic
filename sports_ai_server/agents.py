"""Deterministic review agents. These do NOT predict outcomes or run LLMs."""
import datetime
from statistics import median

SPORT_AGENTS = {
    "basketball_nba":("NBA 분석 에이전트","팀 최근 성적·부상·휴식일 미연결"),
    "americanfootball_nfl":("NFL 분석 에이전트","쿼터백·부상·날씨·라인업 미연결"),
    "baseball_mlb":("MLB 분석 에이전트","선발 투수·불펜·라인업 미연결"),
    "icehockey_nhl":("NHL 분석 에이전트","선발 골리·부상·백투백 미연결"),
    "mma_mixed_martial_arts":("UFC 분석 에이전트","선수 전적·스타일 상성·계체 데이터 미연결"),
    "soccer_epl":("EPL 분석 에이전트","선발 명단·부상·최근 xG 미연결"),
    "soccer_usa_mls":("MLS 분석 에이전트","선발 명단·부상·최근 xG 미연결"),
    "soccer_spain_la_liga":("La Liga 분석 에이전트","선발 명단·부상·최근 xG 미연결")
}
def review(event,sport):
    agent,missing=SPORT_AGENTS.get(sport,("종목 분석 에이전트","경기력 데이터 미연결"))
    books=[]
    for b in event.get("bookmakers",[]):
        m=next((m for m in b.get("markets",[]) if m.get("key")=="h2h"),None)
        if m and len(m.get("outcomes",[]))>=2:
            books.append((b,m))
    count=len(books)
    outcomes={}
    for b,m in books:
        for o in m["outcomes"]:
            try:
                price=float(o["price"])
                if price>1:outcomes.setdefault(o["name"],[]).append(price)
            except (KeyError,ValueError,TypeError):continue
    comparisons={name:len(prices) for name,prices in outcomes.items()}
    timestamps=[]
    for b,m in books:
        try:
            t=datetime.datetime.fromisoformat(b["last_update"].replace("Z","+00:00"))
            timestamps.append(t)
        except (KeyError,ValueError,TypeError):pass
    age=None
    if timestamps:
        age=round((datetime.datetime.now(datetime.timezone.utc)-max(timestamps)).total_seconds()/60)
    items=[
      {"agent":"경기 일정 검사","state":"확인","detail":"예정 경기 표시 여부를 검사합니다. 이미 시작된 경기는 제외합니다."},
      {"agent":agent,"state":"데이터 부족","detail":missing+" — 독립 예상 승률을 계산하지 않습니다."},
      {"agent":"배당 비교 에이전트","state":"확인" if count>=2 else "데이터 부족","detail":f"배당 제공 스포츠북 {count}곳, 선택지 {len(outcomes)}개 비교."},
      {"agent":"데이터 품질 에이전트","state":"확인" if count>=3 and (age is None or age<=120) else "주의","detail":f"최근 업데이트 추정 {age}분 전." if age is not None else "스포츠북 업데이트 시각 미확인."},
      {"agent":"리스크 검토 에이전트","state":"보류","detail":"경기력·부상·선발 데이터와 독립 모델 검증이 없어 실제 추천은 보류합니다."}
    ]
    return {"agents":items,"sportsbooks":count,"bookmaker_age_minutes":age,
       "status":"추가 검증 필요","reason":"현재는 배당 비교만 가능하며 실제 승률 예측 자료가 부족합니다."}
