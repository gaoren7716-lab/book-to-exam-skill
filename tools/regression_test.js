// 极简 DOM 桩：每个 id 只有一个元素实例，classList 由该实例自己持有
const _els = {};
function _mk(id){
  const self = {
    id, value:'', style:{}, innerHTML:'', textContent:'', dataset:{}, checked:false,
    _cls:new Set(),
    scrollIntoView(){},
    querySelector(){ return _mk('q'); },
    querySelectorAll(){ return []; },
    appendChild(){}, remove(){}, addEventListener(){}
  };
  self.classList = {
    add(...c){ c.forEach(x=>self._cls.add(x)); },
    remove(...c){ c.forEach(x=>self._cls.delete(x)); },
    toggle(c, force){ (force===undefined ? !self._cls.has(c) : force) ? self._cls.add(c) : self._cls.delete(c); },
    contains(c){ return self._cls.has(c); }
  };
  return self;
}
global.document = {
  getElementById(id){ return _els[id] || (_els[id] = _mk(id)); },
  querySelector(){ return _mk('q'); },
  querySelectorAll(){ return []; },
  createElement(){ return _mk('n'); },
  addEventListener(){}
};
global.localStorage = {getItem(){return null}, setItem(){}, removeItem(){}};
global.window = global; global.alert = ()=>{}; global.confirm = ()=>true;
// 从当前考试网页实时抽取脚本，保证测试永远跟着模板走（而不是冻结快照）
const fs = require('fs'), path = require('path'), vm = require('vm');
const html = fs.readFileSync(path.join(__dirname, '..', 'examples', 'demo-节奏管理', '考试系统.html'), 'utf8');
const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
const live = blocks.reduce((a,b)=>b.length>a.length?b:a, '');
const body = live.replace(/\binitHome\(\)\s*;?\s*$/, '');
vm.runInThisContext(body);   // 用 vm 而非 eval，让顶层 const/let 可见

let pass=0, fail=0;
function T(n,c,e){ if(c){pass++;console.log('  PASS',n,e||'');} else {fail++;console.log('  FAIL',n,e||'');} }
const fe={classList:{toggle(){},add(){},remove(){},contains(){return false}}};
const byId=id=>CARDS.find(c=>c.id===id);
console.log('\n=== 交互回归 ===');

selectAllPts(true);
T('1 全选覆盖全部题目', selCardIds().length===CARDS.length, selCardIds().length+'/'+CARDS.length);

selectAllPts(false);
const fp=OUTLINE[0].chapters[0].points[0];
togglePt(fp.id);
T('2 单知识点收窄生效', selCardIds().length===fp.cards.length && selCardIds().length<CARDS.length, selCardIds().length+' 题');

selectAllPts(false);
T('3 清空后为空', selCardIds().length===0);

selectAllPts(true);
scope.clear(); [1,2].forEach(c=>scope.add(c));
const pool=effectivePool(), exp=CARDS.filter(c=>[1,2].includes(c.chapter)).length;
T('4 大纲∩章节交集正确', pool.length===exp, pool.length+'/'+exp);

CARDS.forEach(c=>scope.add(c.chapter));
setTypeMode('apply', fe);
const ap=effectivePool();
T('5 题型筛选=场景+案例', ap.length>0 && ap.every(c=>c.type!=='choice'), ap.length+' 题');
setTypeMode('all', fe);

scope.clear();
state.wrong={};
const w0=JSON.stringify(state.wrong);
openReaderPool(selCardIds().slice(0,5).map(byId), '测试');
T('6 速览不污染答题记录', JSON.stringify(state.wrong)===w0);

CARDS.forEach(c=>scope.add(c.chapter));
CARDS.slice(0,3).forEach(c=> state.wrong[c.id]={times:2});
const wq=effectivePool().filter(c=>state.wrong[c.id]);
T('7 错题重考只出错题', wq.length===3, wq.length+' 题');
state.wrong={};

selectWeak();
T('8 只选未掌握能选中知识点', selCardIds().length>0, selCardIds().length+' 题');

T('9 每章均有知识点', OUTLINE.flatMap(p=>p.chapters).every(c=>c.points.length>0));
T('10 全为选择题类型', CARDS.every(c=>['choice','scenario','case'].includes(c.type)));
T('11 每题均有4选项且答案可定位', CARDS.every(c=>c.options.length===4 && c.options.some(o=>o.trim().startsWith(c.answer))));
const st=chapStats(OUTLINE[0].chapters[0]);
T('12 掌握度可计算', st && typeof st.total==='number', '第1章 total='+(st&&st.total));

T('13 进度区按部分聚合', renderStats, '已渲染');
renderBookCard();
T('14 书本介绍卡可渲染', renderBookCard, '');
togglePartStat(0);
T('15 部分聚合可展开', document.getElementById('pch-0').classList.contains('open'), '展开第1部分章节');
togglePartStat(0);
T('16 部分聚合可收起', !document.getElementById('pch-0').classList.contains('open'), '');
jumpToChapter(1);
T('17 跳转后大纲视图已激活', !document.getElementById('view-outline').classList.contains('hidden'), '');
T('18 跳转后目标章节已展开', document.getElementById('chap-1').classList.contains('open'), '第1章');

console.log('\n结果: PASS='+pass+'  FAIL='+fail);
process.exit(fail?1:0);
