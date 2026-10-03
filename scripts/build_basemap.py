#!/usr/bin/env python3
"""Download streets, parks, water and coastline for central Edinburgh from the
OpenStreetMap Overpass API, simplify them, and write public/data/basemap.json.

The site draws this as its own vector basemap, so it needs no tile server.
Map data © OpenStreetMap contributors (ODbL).

Usage: python3 scripts/build_basemap.py
"""
import json, math, urllib.parse, urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "public" / "data" / "basemap.json"
QUERY = """[out:json][timeout:120];(
way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|pedestrian|living_street)$"](55.915,-3.26,55.99,-3.14);
way["leisure"="park"](55.915,-3.26,55.99,-3.14);
way["landuse"~"^(grass|recreation_ground)$"](55.915,-3.26,55.99,-3.14);
way["natural"~"^(water|coastline)$"](55.90,-3.30,56.01,-3.10);
way["waterway"="river"](55.915,-3.26,55.99,-3.14);
way["railway"="rail"](55.915,-3.26,55.99,-3.14);
relation["leisure"="park"](55.915,-3.26,55.99,-3.14););out geom;"""
req = urllib.request.Request("https://overpass-api.de/api/interpreter",
                             data=urllib.parse.urlencode({"data": QUERY}).encode(),
                             headers={"User-Agent": "edinburghcocktailweek-map/1.0"})
with urllib.request.urlopen(req, timeout=300) as r:
    els = json.load(r)["elements"]
K=1e5
def key(p): return (round(p['lat'],7),round(p['lon'],7))
# --- simplification (Douglas-Peucker in metres)
def dp(pts,tol):
    if len(pts)<3: return pts
    def proj(p): return (p[1]*math.cos(math.radians(55.95))*111320,p[0]*110574)
    P=[proj(p) for p in pts]
    keep=[False]*len(pts);keep[0]=keep[-1]=True
    st=[(0,len(pts)-1)]
    while st:
        a,b=st.pop();ax,ay=P[a];bx,by=P[b];dx,dy=bx-ax,by-ay;L=math.hypot(dx,dy) or 1e-9
        best,bi=0,-1
        for i in range(a+1,b):
            x,y=P[i];dd=abs(dy*x-dx*y+bx*ay-by*ax)/L if L>1e-9 else math.hypot(x-ax,y-ay)
            if dd>best: best,bi=dd,i
        if best>tol: keep[bi]=True;st+=[(a,bi),(bi,b)]
    return [p for p,k in zip(pts,keep) if k]
def enc(pts):
    out=[];px=py=0
    for la,lo in pts:
        a,b=round(la*K),round(lo*K);out+= [a-px,b-py];px,py=a,b
    return out
BB=(55.912,-3.27,55.995,-3.13)
def inbb(g): return any(BB[0]<=p['lat']<=BB[2] and BB[1]<=p['lon']<=BB[3] for p in g)
layers={k:[] for k in ['major','minor','path','park','water','river','rail','sea']}
names={}
for e in els:
    t=e.get('tags',{})
    if e['type']=='relation':
        for m in e.get('members',[]):
            if m.get('role')=='outer' and 'geometry' in m:
                g=[(p['lat'],p['lon']) for p in m['geometry']]
                if len(g)>3: layers['park'].append(enc(dp(g,4)))
        continue
    g=e.get('geometry');
    if not g or not inbb(g): continue
    pts=[(p['lat'],p['lon']) for p in g]
    h=t.get('highway')
    if h in('motorway','trunk','primary','secondary'): L='major'
    elif h in('tertiary',): L='major' if False else 'minor'
    elif h in('residential','unclassified','living_street'): L='minor'
    elif h=='pedestrian': L='path'
    elif t.get('leisure')=='park' or t.get('landuse') in('grass','recreation_ground'):
        L='park'
        if t.get('landuse')=='grass':
            # drop tiny verges
            la=[p[0] for p in pts];lo=[p[1] for p in pts]
            if (max(la)-min(la))*(max(lo)-min(lo))<2e-7: continue
    elif t.get('natural')=='water': L='water'
    elif t.get('waterway')=='river': L='river'
    elif t.get('railway')=='rail':
        if t.get('service'): continue
        L='rail'
    else: continue
    tol=2.5 if L in('major','minor','path') else 4
    layers[L].append(enc(dp(pts,tol)))
    if h in('primary','secondary','tertiary','pedestrian','residential','unclassified') and t.get('name'):
        n=t['name'];ln=sum(math.hypot(pts[i][0]-pts[i-1][0],pts[i][1]-pts[i-1][1]) for i in range(1,len(pts)))
        rank={'primary':3,'secondary':3,'tertiary':2,'pedestrian':2}.get(h,1)
        if n not in names or ln>names[n][0]:
            mid=pts[len(pts)//2]
            a=pts[max(0,len(pts)//2-1)];b=pts[min(len(pts)-1,len(pts)//2+1)]
            ang=math.degrees(math.atan2(-(b[0]-a[0])*110574,(b[1]-a[1])*math.cos(math.radians(55.95))*111320))
            if ang>90: ang-=180
            if ang<-90: ang+=180
            names[n]=(ln,round(mid[0],5),round(mid[1],5),rank,round(ang))
# coastline chain -> sea polygon
co={e['id']:[(p['lat'],p['lon']) for p in e['geometry']] for e in els if e.get('tags',{}).get('natural')=='coastline'}
starts={}
for i,g in co.items(): starts.setdefault(g[0],[]).append(i)
# start from the easternmost coastline segment (Portobello) and walk west
first=max(co,key=lambda i:co[i][0][1])
chain=list(co[first]);used={first}
while True:
    nxt=[i for i in starts.get(chain[-1],[]) if i not in used]
    if not nxt: break
    i=max(nxt,key=lambda i:len(co[i]));used.add(i);chain+=co[i][1:]
print('coast chain end',chain[-1],len(chain))
sea=dp(chain,4)+[(56.08,chain[-1][1]),(56.08,-3.0),(chain[0][0],-3.0)]
layers['sea'].append(enc(sea))
# keep street names with length (filter short residential)
lab=[[n,v[1],v[2],v[3],v[4]] for n,v in names.items() if v[3]>=2 or v[0]>0.002]
json.dump({'layers':layers,'labels':lab},open(OUT,'w'),separators=(',',':'),ensure_ascii=False)
print(OUT.stat().st_size,{k:len(v) for k,v in layers.items()},len(lab))
