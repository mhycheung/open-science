"""The interactive view of a graph drawing (``graphdraw``): one self-contained HTML file with
the cards and arrows where Graphviz placed them, and a small script for pan, zoom, search,
status filters, a minimap, and a details panel. Clicking a card shows what it rests on and
what uses it; its link opens the node's file, or its Notion page once the Notion sync has
written the page links in (``with_links``).

The file needs no network: KaTeX, loaded from a CDN for the $LaTeX$ in titles, is optional,
and a title stays plain text without it.
"""

from __future__ import annotations

import json
import re

DATA_RE = re.compile(r'(<script id="opsci-graph-data" type="application/json">)(.*?)(</script>)', re.S)
MARGIN = 16.0
CARD_KEYS = ("id", "title", "meta", "status", "verification", "at_risk", "rank", "badge", "tags", "summary",
             "href", "box")


def data(d: dict, lay: dict, colours: dict, labels: dict) -> dict:
    """The drawing in page coordinates (px, y down, origin at the top left)."""
    x0, y0, x1, y1 = lay["bb"]

    def X(x):
        return round(x - x0 + MARGIN, 1)

    def Y(y):
        return round(y1 - y + MARGIN, 1)

    cards = []
    for i, c in enumerate(d["cards"]):
        x, y, w, h = lay["pos"][f"c{i}"]
        cards.append({**{k: c[k] for k in CARD_KEYS if c.get(k)},
                      "x": X(x - w / 2), "y": Y(y + h / 2), "w": round(w, 1), "h": round(h, 1)})
    boxes = []
    for i, b in enumerate(d["boxes"]):
        bx0, by0, bx1, by1 = lay["bbs"][f"b{i}"]
        boxes.append({**{k: b[k] for k in ("id", "kicker", "title", "style", "badge", "href") if b.get(k)},
                      "x": X(bx0), "y": Y(by1), "w": round(bx1 - bx0, 1), "h": round(by1 - by0, 1)})
    edges = []
    for i, (a, b, k) in enumerate(d["edges"]):
        pts, endp, lp = lay["splines"].get(f"e{i}", ([], None, None))
        if not pts:
            continue
        path = "M%s %s" % (X(pts[0][0]), Y(pts[0][1]))
        for j in range(1, len(pts) - 2, 3):
            path += " C" + " ".join("%s %s" % (X(p[0]), Y(p[1])) for p in pts[j:j + 3])
        if endp:
            path += " L%s %s" % (X(endp[0]), Y(endp[1]))
        e = {"a": a, "b": b, "k": k, "d": path}
        if lp and k in labels:
            e["lp"] = [X(lp[0]), Y(lp[1])]
        edges.append(e)
    return {"W": round(x1 - x0 + 2 * MARGIN, 1), "H": round(y1 - y0 + 2 * MARGIN, 1), "cards": cards,
            "boxes": boxes, "edges": edges, "legend": d.get("legend", ""), "labels": labels,
            "colours": colours, "linkLabel": "Open file", "onlyUrls": False}


def _dump(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def page(d: dict, lay: dict, colours: dict, labels: dict, mark: str) -> str:
    """The HTML page of drawing ``d`` laid out as ``lay``. ``mark``: a comment that names the
    drawing's hash."""
    return PAGE.replace("@MARK@", mark).replace("@DATA@", _dump(data(d, lay, colours, labels)))


def with_links(html: str, urls: dict[str, str], label: str = "Open in Notion") -> str:
    """``html`` with each card and box whose id is in ``urls`` linked to that URL, and no
    link to a file (which does not resolve where the page is shown)."""
    m = DATA_RE.search(html)
    if not m:
        return html
    D = json.loads(m.group(2).replace("<\\/", "</"))
    for x in D["cards"] + D["boxes"]:
        x.pop("href", None)
        if x["id"] in urls:
            x["url"] = urls[x["id"]]
    D["linkLabel"], D["onlyUrls"] = label, True
    return html[:m.start(2)] + _dump(D) + html[m.end(2):]


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
@MARK@
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Graph</title>
<style>
html,body{margin:0;height:100%;overflow:hidden;font:13px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;color:#1f2937;background:#fff}
#bar{position:absolute;top:0;left:0;right:0;height:34px;display:flex;align-items:center;gap:6px;padding:0 8px;border-bottom:1px solid #e5e7eb;background:#f9fafb;z-index:3;overflow-x:auto;white-space:nowrap}
#bar input{width:170px;padding:3px 6px;border:1px solid #d1d5db;border-radius:4px;font:inherit}
#bar button{padding:2px 8px;border:1px solid #d1d5db;border-radius:4px;background:#fff;cursor:pointer;font:inherit}
#bar button.on{background:#1f2937;color:#fff;border-color:#1f2937}
.chip{display:inline-flex;align-items:center;gap:4px;padding:1px 7px;margin-right:3px;border-radius:10px;border:1px solid #d1d5db;cursor:pointer;font-size:12px;background:#fff;user-select:none}
.chip.off{opacity:.35;text-decoration:line-through}
.chip i{width:9px;height:9px;border-radius:2px;display:inline-block}
#help{color:#6b7280;font-size:11px;margin-left:4px}
#view{position:absolute;top:35px;left:0;right:0;bottom:0;overflow:hidden;cursor:grab;touch-action:none;user-select:none;background:#fff}
#view.drag{cursor:grabbing}
#world{position:absolute;left:0;top:0;transform-origin:0 0}
#edges{position:absolute;left:0;top:0;overflow:visible;pointer-events:none}
.box{position:absolute;box-sizing:border-box;border:1.5px solid #8c8c8c;border-radius:8px;background:#f5f5f5}
.box.brain{border:1.5px dashed #c2410c;background:#fff7ed}
.box .hd{position:absolute;left:8px;top:5px;right:8px;font-size:11px;line-height:1.25;color:#374151}
.box .hd b{font-variant:small-caps;font-weight:600}
.box .hd a{color:inherit;text-decoration:none;cursor:pointer}
.box .hd a:hover b{text-decoration:underline}
.box.brain .hd{color:#c2410c}
.card{position:absolute;box-sizing:border-box;background:#fff;border:1px solid;border-radius:4px;padding:3px 5px 3px 9px;overflow:hidden;cursor:pointer;line-height:1.2;font-size:10px}
.card.grow{height:auto !important;z-index:1}
.card:hover{box-shadow:0 0 0 2px #93c5fd}
.card .bar{position:absolute;left:1px;top:1px;bottom:1px;width:3px}
.card .id{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.8em;color:#6b7280;word-break:break-all}
.card .t{margin-top:1px}
.card .m{font-size:.8em;font-style:italic;color:#6b7280;margin-top:2px}
.card .g{font-family:ui-monospace,Menlo,monospace;font-size:.8em;color:#6d28d9;margin-top:2px}
.card.premise{background:#fafafa}
.card.premise .t{color:#555}
.card.verif{border-style:double;border-width:3px}
.card.aband{border-style:dashed}
.card.risk{border:2.5px solid #dc2626}
.badge{display:inline-block;font-size:7px;line-height:1.3;color:#fff;padding:0 3px;border-radius:2px;text-transform:uppercase;letter-spacing:.03em;vertical-align:middle}
.card .badge{position:absolute;right:3px;top:2px}
.card.hasb{padding-top:9px}
.lp{position:absolute;font-size:8.5px;font-style:italic;color:#6b7280;background:#fff;padding:0 1px;transform:translate(-50%,-50%);white-space:nowrap;pointer-events:none}
.dim{opacity:.13}
.gone{visibility:hidden}
.hit{box-shadow:0 0 0 3px #f59e0b}
.sel{box-shadow:0 0 0 3px #2563eb !important}
#panel{position:absolute;top:35px;right:0;bottom:0;width:300px;max-width:85%;box-sizing:border-box;background:#fff;border-left:1px solid #e5e7eb;box-shadow:-2px 0 6px rgba(0,0,0,.06);padding:8px 12px 16px;overflow:auto;z-index:4;display:none}
#panel .x{float:right;cursor:pointer;border:0;background:none;font-size:18px;line-height:1;color:#6b7280}
#panel h3{margin:4px 0 4px;font-size:14px}
#panel .pid{font:11px ui-monospace,Menlo,monospace;color:#6b7280;word-break:break-all;margin-top:4px}
#panel .meta{color:#6b7280;font-style:italic;font-size:12px}
#panel a.open{display:inline-block;margin:8px 6px 2px 0;padding:4px 10px;background:#2563eb;color:#fff;border-radius:4px;text-decoration:none}
#panel a.open.alt{background:#fff;color:#2563eb;border:1px solid #2563eb;padding:3px 9px}
#panel p{margin:8px 0}
#panel b.h{display:block;margin-top:10px;font-size:12px;color:#374151}
#panel ul{padding-left:16px;margin:3px 0}
#panel li{margin:2px 0}
#panel li a{cursor:pointer;color:#2563eb;word-break:break-all}
#mini{position:absolute;left:8px;bottom:8px;border:1px solid #d1d5db;background:rgba(255,255,255,.92);z-index:2;cursor:crosshair;border-radius:3px}
</style></head><body>
<div id="bar"><input id="q" placeholder="Search id or title (Enter: next)"><button id="fit" title="Show the whole graph">Fit</button><button id="zin" title="Zoom in">+</button><button id="zout" title="Zoom out">&minus;</button><button id="focus" title="Show only what the selected card rests on and what uses it">Lineage only</button><span id="chips"></span><span id="help">wheel: zoom &middot; drag: pan &middot; click: details &middot; double-click: open</span></div>
<div id="view"><div id="world"><svg id="edges" xmlns="http://www.w3.org/2000/svg"></svg></div></div>
<canvas id="mini"></canvas>
<div id="panel"></div>
<script id="opsci-graph-data" type="application/json">@DATA@</script>
<script>
(function(){
var D=JSON.parse(document.getElementById('opsci-graph-data').textContent);
var C=D.colours,NS='http://www.w3.org/2000/svg';
var view=document.getElementById('view'),world=document.getElementById('world'),svg=document.getElementById('edges');
var P=document.getElementById('panel'),mini=document.getElementById('mini');
var BADGE={'public':'#15803d','soft private':'#b45309','hard private':'#7f1d1d','not published':'#595959'};
var byId={},boxById={},UP={},DOWN={},sel=null,focus=false,hidden={},lineage=null;
world.style.width=D.W+'px';world.style.height=D.H+'px';svg.setAttribute('width',D.W);svg.setAttribute('height',D.H);
function el(tag,cls,parent,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;if(parent)parent.appendChild(e);return e}
function place(e,o){e.style.left=o.x+'px';e.style.top=o.y+'px';e.style.width=o.w+'px';e.style.height=o.h+'px'}
function stat(s){return C[s]?s:'active'}
function badge(parent,b){var s=el('span','badge',parent,b);s.style.background=BADGE[b]||'#555';return s}
function tint(hex,a){var n=parseInt(hex,16),r=n>>16,g=(n>>8)&255,b=n&255;function m(c){return Math.round(255-(255-c)*a)}return 'rgb('+m(r)+','+m(g)+','+m(b)+')'}
function link(x){return x.url||(D.onlyUrls?null:x.href)||null}
// A notion.so link opens the browser from inside an HTML block; notion:// opens the Notion app.
var UA=navigator.userAgent,APP=/iPad|iPhone|iPod/.test(UA)||(/Macintosh/.test(UA)&&navigator.maxTouchPoints>1)||/Notion\//.test(UA);
function appUrl(u){return /^https:\/\/(www\.)?notion\.so\//.test(u||'')?u.replace(/^https:\/\//,'notion://'):null}
function primary(x){var u=link(x),a=appUrl(u);return APP&&a?a:u}
function go(u){if(/^notion:/.test(u))window.location.href=u;else window.open(u,'_blank','noopener')}
function rel(map,a,b){(map[a]=map[a]||[]).push(b)}
// boxes, under the arrows
D.boxes.forEach(function(b){boxById[b.id]=b;var e=el('div','box'+(b.style==='brainstorm'?' brain':''));world.insertBefore(e,svg);place(e,b);
  var h=el('div','hd',e),u=primary(b),k=u?el('a',null,h):h;if(u){k.href=u;k.title=D.linkLabel;if(/^notion:/.test(u))k.onclick=function(ev){ev.preventDefault();go(u)};else{k.target='_blank';k.rel='noopener'}}
  el('b',null,k,b.kicker);if(b.badge){h.appendChild(document.createTextNode(' '));badge(h,b.badge)}
  if(b.title){el('br',null,h);el('i',null,h,b.title)}b.el=e;
  if(u)k.addEventListener('pointerdown',function(ev){ev.stopPropagation()})});
// arrows
var defs=document.createElementNS(NS,'defs');defs.innerHTML='<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#8a8a8a"/></marker>';svg.appendChild(defs);
D.edges.forEach(function(e){var p=document.createElementNS(NS,'path');p.setAttribute('d',e.d);p.setAttribute('fill','none');p.setAttribute('stroke','#8a8a8a');p.setAttribute('stroke-width','1');
  if(e.k in D.labels)p.setAttribute('stroke-dasharray','4 3');
  if(e.k==='related')p.setAttribute('stroke-dasharray','1.5 2.5');else p.setAttribute('marker-end','url(#ah)');
  svg.appendChild(p);e.el=p;
  if(e.lp){e.lel=el('div','lp',world,D.labels[e.k]);e.lel.style.left=e.lp[0]+'px';e.lel.style.top=e.lp[1]+'px'}
  if(e.k!=='related'){rel(UP,e.b,e.a);rel(DOWN,e.a,e.b)}});
// cards
var moved=false;
D.cards.forEach(function(c){byId[c.id]=c;var s=stat(c.status),col=C[s],e=el('div','card',world);place(e,c);
  if(c.rank==='premise')e.classList.add('premise');if(c.verification)e.classList.add('verif');if(s==='abandoned')e.classList.add('aband');if(c.at_risk)e.classList.add('risk');
  if(!c.at_risk)e.style.borderColor=c.rank==='premise'?'#c7c7c7':'#'+col[1];
  if(c.rank==='milestone'&&!c.at_risk){e.style.borderWidth='2px';e.style.background=tint(col[0],.45)}
  var bar=el('div','bar',e);bar.style.background='#'+col[1];if(c.rank==='premise')bar.style.opacity=.4;
  el('div','id',e,c.id);if(c.title)el('div','t',e,c.title);if(c.meta)el('div','m',e,c.meta);
  if(c.tags&&c.tags.length)el('div','g',e,'['+c.tags.join(', ')+']');if(c.badge){badge(e,c.badge);e.classList.add('hasb')}
  c.el=e;if(c.box){rel(UP,c.id,c.box);rel(DOWN,c.box,c.id)}
  e.addEventListener('click',function(ev){ev.stopPropagation();if(!moved)select(c)});
  e.addEventListener('dblclick',function(ev){ev.stopPropagation();var u=primary(c);if(u)go(u)})});
// the text of a card was measured by LaTeX; a browser font may need more room: shrink it, then grow the card
function fitCards(){D.cards.forEach(function(c){var e=c.el,s=10;e.classList.remove('grow');e.style.fontSize='';
  while(e.scrollHeight>e.clientHeight+1&&s>7){s-=0.5;e.style.fontSize=s+'px'}
  if(e.scrollHeight>e.clientHeight+1)e.classList.add('grow')})}
fitCards();
// status filters
var chips=document.getElementById('chips');
Object.keys(C).forEach(function(s){if(!D.cards.some(function(c){return stat(c.status)===s}))return;
  var ch=el('span','chip',chips);var i=el('i',null,ch);i.style.background='#'+C[s][1];ch.appendChild(document.createTextNode(s));
  ch.title='Show or hide '+s+' cards';ch.onclick=function(){hidden[s]=!hidden[s];ch.classList.toggle('off',!!hidden[s]);refresh()}});
// lineage
function walk(start,map){var seen={},todo=[start];while(todo.length){var x=todo.pop();(map[x]||[]).forEach(function(y){if(!seen[y]){seen[y]=1;todo.push(y)}})}return seen}
function refresh(){
  D.cards.forEach(function(c){var off=!!hidden[stat(c.status)],out=lineage&&!lineage[c.id];
    c.el.classList.toggle('dim',off||(!!out&&!focus));c.el.classList.toggle('gone',!!out&&focus);c.el.classList.toggle('sel',sel===c)});
  D.boxes.forEach(function(b){var out=lineage&&!lineage[b.id]&&!D.cards.some(function(c){return c.box===b.id&&lineage[c.id]});
    b.el.classList.toggle('dim',!!out&&!focus);b.el.classList.toggle('gone',!!out&&focus)});
  D.edges.forEach(function(e){function vis(i){var c=byId[i];if(c&&hidden[stat(c.status)])return false;return !lineage||!!lineage[i]}
    var on=vis(e.a)&&vis(e.b);e.el.setAttribute('opacity',on?1:(focus&&lineage?0:.13));if(e.lel)e.lel.style.opacity=on?1:(focus&&lineage?0:.13)});
  drawMini()}
function select(c){sel=c;var up=walk(c.id,UP),dn=walk(c.id,DOWN);lineage={};lineage[c.id]=1;
  for(var k in up)lineage[k]=1;for(var k2 in dn)lineage[k2]=1;refresh();showPanel(c)}
function clear(){sel=null;lineage=null;P.style.display='none';refresh()}
function showPanel(c){P.innerHTML='';var x=el('button','x',P,'×');x.title='Close';x.onclick=clear;
  el('div','pid',P,c.id);el('h3',null,P,c.title||c.id);if(c.meta)el('div','meta',P,c.meta);
  var u=link(c),au=appUrl(u);
  if(u&&au){button(au,'Open in the Notion app',!APP);button(u,'Open in the browser',APP)}else if(u)button(u,D.linkLabel+' ↗',false);
  if(c.summary)el('p',null,P,c.summary);if(c.tags&&c.tags.length)el('p',null,P,'Uses: '+c.tags.join(', '));
  list('Rests on',(UP[c.id]||[]));list(D.legend?D.legend.charAt(0).toUpperCase()+D.legend.slice(1):'Used by',(DOWN[c.id]||[]));
  P.style.display='block';math(P)}
function button(u,label,alt){var a=el('a','open'+(alt?' alt':''),P,label);a.href=u;
  if(/^notion:/.test(u))a.onclick=function(e){e.preventDefault();go(u)};else{a.target='_blank';a.rel='noopener'}}
function list(t,ids){ids=ids.filter(function(i){return byId[i]||boxById[i]});if(!ids.length)return;el('b','h',P,t);var ul=el('ul',null,P);
  ids.forEach(function(i){var a=el('a',null,el('li',null,ul),i);a.onclick=function(){if(byId[i]){select(byId[i]);centerOn(byId[i])}else centerOn(boxById[i])}})}
// pan and zoom
var k=1,tx=0,ty=0,raf=0;
function apply(){world.style.transform='translate('+tx+'px,'+ty+'px) scale('+k+')';if(!raf)raf=requestAnimationFrame(function(){raf=0;drawMini()})}
function zoomAt(f,px,py){var nk=Math.min(4,Math.max(0.02,k*f));tx=px-(px-tx)*nk/k;ty=py-(py-ty)*nk/k;k=nk;apply()}
function size(){return view.getBoundingClientRect()}
function fit(){var r=size();k=Math.min(r.width/D.W,r.height/D.H)*0.97;tx=(r.width-D.W*k)/2;ty=(r.height-D.H*k)/2;apply()}
function first(){var r=size();k=Math.min(1,r.width/D.W);tx=Math.max(0,(r.width-D.W*k)/2);ty=Math.max(0,(r.height-D.H*k)/2);apply()}
function centerOn(o){var r=size();if(k<0.8)k=0.9;var w=r.width-(P.style.display==='block'?P.offsetWidth:0);tx=w/2-(o.x+o.w/2)*k;ty=r.height/2-(o.y+o.h/2)*k;apply()}
view.addEventListener('wheel',function(e){e.preventDefault();var r=size(),dy=e.deltaMode?e.deltaY*30:e.deltaY;zoomAt(Math.exp(-dy*0.0015),e.clientX-r.left,e.clientY-r.top)},{passive:false});
var pts={},start=null;
view.addEventListener('pointerdown',function(e){pts[e.pointerId]=[e.clientX,e.clientY];moved=false;start=[e.clientX,e.clientY];view.classList.add('drag')});
window.addEventListener('pointermove',function(e){var p=pts[e.pointerId];if(!p)return;var ids=Object.keys(pts);
  if(ids.length===1){tx+=e.clientX-p[0];ty+=e.clientY-p[1];if(Math.abs(e.clientX-start[0])+Math.abs(e.clientY-start[1])>4)moved=true;pts[e.pointerId]=[e.clientX,e.clientY];apply()}
  else if(ids.length===2){var o=pts[ids[0]===String(e.pointerId)?ids[1]:ids[0]],d0=Math.hypot(p[0]-o[0],p[1]-o[1]),d1=Math.hypot(e.clientX-o[0],e.clientY-o[1]),r=size();
    pts[e.pointerId]=[e.clientX,e.clientY];moved=true;if(d0>0)zoomAt(d1/d0,(e.clientX+o[0])/2-r.left,(e.clientY+o[1])/2-r.top)}});
function lift(e){delete pts[e.pointerId];if(!Object.keys(pts).length)view.classList.remove('drag')}
window.addEventListener('pointerup',lift);window.addEventListener('pointercancel',lift);
view.addEventListener('click',function(){if(!moved&&sel)clear()});
document.getElementById('fit').onclick=fit;
document.getElementById('zin').onclick=function(){var r=size();zoomAt(1.25,r.width/2,r.height/2)};
document.getElementById('zout').onclick=function(){var r=size();zoomAt(0.8,r.width/2,r.height/2)};
var fb=document.getElementById('focus');fb.onclick=function(){focus=!focus;fb.classList.toggle('on',focus);refresh()};
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&document.activeElement!==q)clear()});
// search
var q=document.getElementById('q'),hits=[],hi=-1;
q.addEventListener('input',function(){var s=q.value.trim().toLowerCase();hits=[];hi=-1;
  D.cards.forEach(function(c){var m=!!s&&(c.id.toLowerCase().indexOf(s)>=0||(c.title||'').toLowerCase().indexOf(s)>=0);c.el.classList.toggle('hit',m);if(m)hits.push(c)})});
q.addEventListener('keydown',function(e){if(e.key==='Enter'&&hits.length){hi=(hi+1)%hits.length;centerOn(hits[hi])}
  if(e.key==='Escape'){q.value='';q.dispatchEvent(new Event('input'))}});
// minimap
var MW=110,MH=Math.max(30,Math.min(110,MW*D.H/D.W)),ms=Math.min(MW/D.W,MH/D.H),dpr=window.devicePixelRatio||1;
mini.width=Math.round(D.W*ms*dpr);mini.height=Math.round(D.H*ms*dpr);mini.style.width=D.W*ms+'px';mini.style.height=D.H*ms+'px';
function drawMini(){var r=size(),g=mini.getContext('2d');
  mini.style.display=(D.W*k<=r.width+1&&D.H*k<=r.height+1)?'none':'block';
  g.setTransform(dpr*ms,0,0,dpr*ms,0,0);g.clearRect(0,0,D.W,D.H);
  g.fillStyle='#ececec';D.boxes.forEach(function(b){g.fillRect(b.x,b.y,b.w,b.h)});
  D.cards.forEach(function(c){var s=stat(c.status);g.globalAlpha=c.el.classList.contains('dim')||c.el.classList.contains('gone')?.2:1;g.fillStyle='#'+C[s][1];g.fillRect(c.x,c.y,c.w,c.h)});
  g.globalAlpha=1;g.strokeStyle='#dc2626';g.lineWidth=2/ms;g.strokeRect(-tx/k,-ty/k,r.width/k,r.height/k)}
function jump(e){var b=mini.getBoundingClientRect(),r=size(),x=(e.clientX-b.left)/ms,y=(e.clientY-b.top)/ms;tx=r.width/2-x*k;ty=r.height/2-y*k;apply()}
var mdrag=false;mini.addEventListener('pointerdown',function(e){mdrag=true;jump(e);e.stopPropagation()});
window.addEventListener('pointermove',function(e){if(mdrag)jump(e)});window.addEventListener('pointerup',function(){mdrag=false});
// $LaTeX$ in titles, when KaTeX can be loaded
function math(root){if(window.renderMathInElement)renderMathInElement(root,{delimiters:[{left:'$',right:'$',display:false}],throwOnError:false})}
function load(src,cb){var s=document.createElement('script');s.src=src;s.onload=cb;document.head.appendChild(s)}
if(D.cards.concat(D.boxes).some(function(x){return /\$[^$]+\$/.test((x.title||'')+(x.summary||''))})){
  var css=document.createElement('link');css.rel='stylesheet';css.href='https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css';document.head.appendChild(css);
  load('https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js',function(){load('https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js',function(){math(world);fitCards()})})}
window.addEventListener('resize',drawMini);
first();refresh();
})();
</script>
</body></html>
"""
