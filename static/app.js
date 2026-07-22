const $=id=>document.getElementById(id);const fmt=(x,d=2)=>x===null||x===undefined||Number.isNaN(Number(x))?'—':Number(x).toFixed(d);const pct=x=>`${fmt(x,2)}%`;
function setStatus(id,text,type='idle'){const el=$(id);el.textContent=text;el.className=`status ${type}`}
function show(id,text){const el=$(id);el.classList.remove('hidden');el.textContent=text}
const activeRequests=new Map();
async function api(url,options={},requestKey=url,timeoutMs=30000){
  const previous=activeRequests.get(requestKey);if(previous)previous.abort();
  const controller=new AbortController();activeRequests.set(requestKey,controller);
  const timer=setTimeout(()=>controller.abort('timeout'),timeoutMs);
  try{
    const r=await fetch(url,{...options,signal:controller.signal,cache:'no-store'});
    let j;try{j=await r.json()}catch{throw new Error('服务器返回了无法解析的数据')}
    if(!r.ok)throw new Error(j.detail||'请求失败');return j;
  }catch(e){
    if(e.name==='AbortError')throw new Error('请求已取消或超过30秒，请重试');
    throw e;
  }finally{
    clearTimeout(timer);if(activeRequests.get(requestKey)===controller)activeRequests.delete(requestKey);
  }
}
function cards(target,items){$(target).innerHTML=items.map(([k,v])=>`<article class="result-card"><span>${k}</span><strong>${v}</strong></article>`).join('')}
function initDates(){const end=new Date(),start=new Date(end.getTime()-90*864e5);$('end').value=end.toISOString().slice(0,10);$('start').value=start.toISOString().slice(0,10)}
function currentContract(){return $('contract').value.trim().toUpperCase()||'BTC_USDT'}
$('contract').addEventListener('change',()=>{for(const c of activeRequests.values())c.abort();activeRequests.clear();setStatus('analysisStatus','币种已切换，请重新分析','idle');setStatus('overviewStatus','币种已切换，请刷新','idle')});
let strategyTimer=null;
let lastDecisionData=null;
const localTime=ts=>ts?new Date(ts*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—';
function startCountdown(expiresAt){
  if(strategyTimer)clearInterval(strategyTimer);
  const render=()=>{const left=Math.max(0,expiresAt-Math.floor(Date.now()/1000));const m=Math.floor(left/60),sec=left%60;$('countdown').textContent=left?`${m}:${String(sec).padStart(2,'0')}`:'已过期';if(!left){$('countdown').classList.add('expired');clearInterval(strategyTimer)}};
  $('countdown').classList.remove('expired');render();strategyTimer=setInterval(render,1000);
}

document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.panel').forEach(x=>x.classList.add('hidden'));b.classList.add('active');$(b.dataset.target).classList.remove('hidden')});
$('themeBtn').onclick=()=>{document.body.classList.toggle('dark');localStorage.setItem('gate-theme',document.body.classList.contains('dark')?'dark':'light')};if(localStorage.getItem('gate-theme')==='dark')document.body.classList.add('dark');

$('overviewBtn').onclick=async()=>{setStatus('overviewStatus','正在读取多周期数据…','loading');$('overviewBtn').disabled=true;try{const j=await api(`/api/professional-overview?contract=${encodeURIComponent(currentContract())}&_=${Date.now()}`,{},'overview',15000);const c=j.consensus;$('overviewSide').textContent=c.side;$('overviewLong').textContent=c.long_score;$('overviewShort').textContent=c.short_score;$('overviewCount').textContent=j.signal_history.count;$('timeframeCards').innerHTML=(c.timeframes||[]).map(x=>`<article class="tf-card"><small>${x.interval}</small><strong>${x.side}</strong><small>多 ${x.long_score}｜空 ${x.short_score}｜信心 ${x.confidence}</small></article>`).join('');$('overviewResult').textContent=`日志：LONG ${j.signal_history.long}｜SHORT ${j.signal_history.short}｜FLAT ${j.signal_history.flat}\n平均信心：${j.signal_history.average_confidence}\n${j.notice}`;setStatus('overviewStatus',c.partial?'部分周期读取完成':'读取完成','success')}catch(e){setStatus('overviewStatus',e.message,'error')}finally{$('overviewBtn').disabled=false}};

$('dashboardBtn').onclick=async()=>{try{const j=await api('/api/dashboard');const s=j.summary;const rows=(j.recent||[]).map(x=>`<article class="rank-item"><b class="rank-num">•</b><div><strong>${x.contract}</strong><br><small>${x.interval}</small></div><b class="rank-side ${x.side==='LONG'?'long':x.side==='SHORT'?'short':''}">${x.side}</b><small class="rank-extra">信心 ${x.confidence}</small></article>`).join('');$('dashboardResult').innerHTML=`<div class="metric-grid"><article><span>信号</span><strong>${s.signals_logged}</strong></article><article><span>LONG</span><strong>${s.long_signals}</strong></article><article><span>SHORT</span><strong>${s.short_signals}</strong></article><article><span>平均信心</span><strong>${s.average_confidence}</strong></article></div>${rows||'<div class="list-empty">暂无信号。先运行一次实时分析。</div>'}<div class="notice-box">${j.notice}</div>`}catch(e){$('dashboardResult').textContent=`错误：${e.message}`}};

$('analyzeBtn').onclick=async()=>{setStatus('analysisStatus','正在读取 Gate 公开行情…','loading');$('analyzeBtn').disabled=true;$('signalCard').classList.add('hidden');try{const j=await api(`/api/analyze?contract=${encodeURIComponent(currentContract())}&interval=${encodeURIComponent($('interval').value)}&_=${Date.now()}`,{},'analyze'),s=j.signal,f=j.funding||{};$('signalSide').textContent=s.side;$('signalConfidence').textContent=`${s.confidence}/100`;$('signalRisk').textContent=s.risk_level||'—';$('signalRegime').textContent=s.regime||'—';$('signalAdx').textContent=fmt(s.adx,1);$('signalVolPct').textContent=`${fmt(s.volatility_percentile,1)}%`;$('fundingRate').textContent=f.funding_rate_pct==null?'—':`${Number(f.funding_rate_pct)>=0?'+':''}${fmt(f.funding_rate_pct,4)}%`;$('fundingCrowding').textContent=f.crowding||'—';$('generatedAt').textContent=localTime(j.generated_at);$('expiresAt').textContent=localTime(j.expires_at);startCountdown(j.expires_at);const q=j.data_quality||{};$('dataQualityGrade').textContent=q.grade?`${q.grade}｜${q.status}`:'—';$('dataQualityScore').textContent=q.score??'—';$('qualityIssues').innerHTML=(q.issues||[]).map(x=>`<div>数据：${x}</div>`).join('');$('scoreNotice').textContent=j.score_notice||'模型评分，不等于真实胜率。';$('longScore').textContent=s.long_score;$('shortScore').textContent=s.short_score;$('longBar').style.width=`${s.long_score}%`;$('shortBar').style.width=`${s.short_score}%`;$('entryValue').textContent=fmt(s.entry);$('stopValue').textContent=fmt(s.stop);$('targetValue').textContent=fmt(s.target);$('rrValue').textContent=s.risk_reward?`1:${fmt(s.risk_reward,1)}`:'—';$('recommendation').textContent=s.recommendation||'';$('factorList').innerHTML=(s.factor_details||s.reasons||[]).map(x=>`<div>✓ ${x}</div>`).join('')||'<div>暂无强方向因子</div>';$('positionEntry').value=s.entry??'';$('positionStop').value=s.stop??'';$('analysis').textContent=`${j.contract}｜${j.interval}\nRSI14 ${fmt(s.rsi)}｜ATR ${fmt(s.atr)} (${fmt(s.atr_pct)}%)\nEMA20/50/200 ${fmt(s.ema20)} / ${fmt(s.ema50)} / ${fmt(s.ema200)}\n支撑/阻力 ${fmt(s.support)} / ${fmt(s.resistance)}\n资金费率：${f.funding_rate_pct==null?'不可用':`${fmt(f.funding_rate_pct,4)}%`}｜${f.crowding||'—'}｜历史样本 ${f.history_samples||0}\n数据提示：${(j.data_warnings||[]).join('；')||'无'}`;$('signalCard').classList.remove('hidden');setStatus('analysisStatus','分析完成','success')}catch(e){setStatus('analysisStatus',e.message,'error')}finally{$('analyzeBtn').disabled=false}};

function commonBody(){return{contract:currentContract(),interval:$('interval').value,start:$('start').value,end:$('end').value,fee_rate:Number($('fee').value),slippage_rate:Number($('slippage').value)}}
$('directionBtn').onclick=async()=>{show('directionResult','正在下载历史K线并逐次验证下一根K线方向…');try{const body={...commonBody(),interval:$('directionInterval').value,threshold:Number($('threshold').value),sample_size:Number($('directionSamples').value)};const j=await api('/api/direction-validation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),x=j.result,o=x.overall,l=x.long,sh=x.short,ci=o.confidence_interval_95_pct;$('directionResult').textContent=`${x.contract} · ${x.interval} 下一根K线方向验证
有效样本 ${x.used_samples}/${x.requested_samples}｜阈值 ${x.threshold}
总体正确 ${o.correct}/${o.signals}｜命中率 ${fmt(o.accuracy_pct)}%
95%置信区间 ${fmt(ci[0])}%–${fmt(ci[1])}%
手续费与滑点后有效率 ${fmt(o.cost_adjusted_accuracy_pct)}%
做多 ${l.correct}/${l.signals}（${fmt(l.accuracy_pct)}%）
做空 ${sh.correct}/${sh.signals}（${fmt(sh.accuracy_pct)}%）
信号覆盖率 ${fmt(x.signal_coverage_pct)}%｜最大连续错误 ${x.max_consecutive_wrong}
${x.notice}`;$('directionResult').className='notice-box'}catch(e){$('directionResult').className='notice-box';$('directionResult').textContent=`错误：${e.message}`}};
$('backtestBtn').onclick=async()=>{show('backtest','正在下载历史K线并回测…');try{const j=await api('/api/backtest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...commonBody(),threshold:Number($('threshold').value),risk_fraction:Number($('risk').value),max_holding_bars:24})}),x=j.result;cards('backtest',[['交易数',x.trades],['胜率',pct(x.win_rate_pct)],['净收益',pct(x.net_return_pct)],['最大回撤',pct(x.max_drawdown_pct)],['Profit Factor',x.profit_factor===null?'—':fmt(x.profit_factor)],['Sharpe-like',fmt(x.sharpe_like)],['单笔期望',pct(x.expectancy_pct)],['K线数',x.bars]]);$('backtest').className='result-grid'}catch(e){$('backtest').className='notice-box';$('backtest').textContent=`错误：${e.message}`}};
$('optimizeBtn').onclick=async()=>{show('optimize','正在优化训练样本…');try{const j=await api('/api/optimize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(commonBody())}),b=j.best;$('optimize').textContent=b?`最佳训练参数：阈值 ${b.threshold}｜风险 ${(b.risk_fraction*100).toFixed(1)}%｜持有 ${b.max_holding_bars} 根\n交易 ${b.trades}｜净收益 ${fmt(b.net_return_pct)}%｜回撤 ${fmt(b.max_drawdown_pct)}%\n${j.notice}`:'没有可用结果'}catch(e){$('optimize').textContent=`错误：${e.message}`}};
$('wfBtn').onclick=async()=>{show('wf','正在进行滚动样本外验证…');try{const j=await api('/api/walk-forward',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...commonBody(),train_bars:Number($('trainBars').value),test_bars:Number($('testBars').value)})}),x=j.result;$('wf').textContent=`折数 ${x.fold_count}｜正收益折数 ${x.positive_test_folds}\n正收益比例 ${fmt(x.positive_fold_rate_pct)}%｜样本外平均收益 ${fmt(x.average_test_return_pct)}%\n样本外平均回撤 ${fmt(x.average_test_drawdown_pct)}%｜交易总数 ${x.total_test_trades}`}catch(e){$('wf').textContent=`错误：${e.message}`}};
$('mcBtn').onclick=async()=>{show('mc','正在运行 Monte Carlo…');try{const j=await api('/api/monte-carlo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...commonBody(),threshold:Number($('threshold').value),risk_fraction:Number($('risk').value),max_holding_bars:24})}),x=j.result;$('mc').textContent=`模拟 ${x.simulations} 次\n最终收益 P05 / P50 / P95：${fmt(x.final_return_p05_pct)}% / ${fmt(x.final_return_p50_pct)}% / ${fmt(x.final_return_p95_pct)}%\n最大回撤 P50 / P95：${fmt(x.max_drawdown_p50_pct)}% / ${fmt(x.max_drawdown_p95_pct)}%`}catch(e){$('mc').textContent=`错误：${e.message}`}};

$('positionBtn').onclick=async()=>{try{const j=await api('/api/position-size',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account_balance:Number($('accountBalance').value),risk_fraction:Number($('positionRisk').value),entry:Number($('positionEntry').value),stop:Number($('positionStop').value),leverage:Number($('positionLeverage').value)})}),x=j.result;$('positionResult').textContent=`最大计划亏损：${fmt(x.max_loss)} USDT\n参考数量：${fmt(x.quantity,6)}\n名义价值：${fmt(x.notional)} USDT\n估算保证金：${fmt(x.estimated_margin)} USDT\n${j.notice}`}catch(e){$('positionResult').textContent=`错误：${e.message}`}};
$('kellyBtn').onclick=async()=>{try{const j=await api('/api/advanced-risk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account_balance:Number($('kellyBalance').value),win_probability:Number($('kellyWin').value),confidence_lower:Number($('kellyLower').value),data_quality_score:Number($('kellyQuality').value),risk_reward:Number($('kellyRR').value),max_risk_fraction:Number($('kellyRiskCap').value),kelly_cap:.05,kelly_fraction:.25})}),x=j.result;$('kellyResult').textContent=`输入胜率：${pct(x.input_probability*100)}\n调整后胜率：${pct(x.adjusted_probability*100)}\n完整 Kelly：${pct(x.full_kelly_fraction*100)}\n四分之一 Kelly：${pct(x.fractional_kelly_fraction*100)}\n最终风险比例：${pct(x.capped_risk_fraction*100)}\n最大计划亏损：${fmt(x.max_loss)} USDT\n每笔期望 R：${fmt(x.expected_r,3)}\n${x.decision}\n${j.notice}`}catch(e){$('kellyResult').textContent=`错误：${e.message}`}};

$('scanBtn').onclick=async()=>{setStatus('scanStatus','正在扫描高流动性合约…','loading');$('scanBtn').disabled=true;try{const url=`/api/scanner?interval=${$('scanInterval').value}&limit=${$('scanLimit').value}&min_confidence=55`;const j=await api(url,{},'scanner',16000),x=j.result;$('scanResult').innerHTML=(x.rows||[]).map((r,i)=>`<article class="rank-item"><b class="rank-num">${i+1}</b><div><strong>${r.contract}</strong><br><small>价格 ${r.last_price}</small></div><b class="rank-side ${r.side==='LONG'?'long':r.side==='SHORT'?'short':''}">${r.side}</b><small class="rank-extra">信心 ${r.confidence}<br>多 ${r.long_score} / 空 ${r.short_score}</small></article>`).join('')||'<div class="list-empty">没有达到最低评分的信号。</div>';const cacheText=x.cached?`｜缓存 ${x.age_seconds||0}秒`:'';setStatus('scanStatus',`已分析 ${x.analyzed}/${x.requested}${cacheText}`,'success')}catch(e){setStatus('scanStatus',e.message,'error')}finally{$('scanBtn').disabled=false}};
$('consensusBtn').onclick=async()=>{try{const j=await api(`/api/consensus?contract=${encodeURIComponent(currentContract())}`),x=j.result;$('consensusResult').innerHTML=`<article class="tf-card"><small>综合</small><strong>${x.side}</strong><small>多 ${x.long_score}｜空 ${x.short_score}</small></article>`+(x.timeframes||[]).map(r=>`<article class="tf-card"><small>${r.interval}</small><strong>${r.side}</strong><small>多 ${r.long_score}｜空 ${r.short_score}｜信心 ${r.confidence}</small></article>`).join('')}catch(e){$('consensusResult').innerHTML=`<div class="notice-box">错误：${e.message}</div>`}};
$('predictionBtn').onclick=async()=>{try{const j=await api('/api/prediction-value',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({market_price:Number($('marketPrice').value),model_probability:Number($('modelProbability').value),fee_rate:Number($('predictionFee').value),kelly_cap:Number($('kellyCap').value),minimum_edge:.05})}),x=j.result;cards('predictionResult',[['判断',x.recommendation],['概率优势',`${fmt(x.edge_pct_points,1)}pp`],['每份期望值',fmt(x.expected_value_per_share,4)],['期望收益率',pct(x.expected_roi_pct)],['完整 Kelly',pct(x.full_kelly_fraction*100)],['建议上限',pct(x.capped_kelly_fraction*100)]]);$('predictionResult').insertAdjacentHTML('beforeend',`<div class="notice-box" style="grid-column:1/-1">${x.explanation}<br>${j.notice}</div>`)}catch(e){$('predictionResult').innerHTML=`<div class="notice-box">错误：${e.message}</div>`}};

(async()=>{initDates();try{const j=await api('/api/health');$('healthBadge').textContent=`服务正常｜版本 ${j.version}`}catch{$('healthBadge').textContent='服务检查失败'} })();

const termGlossary={
 threshold:['信号阈值','模型评分达到该数值后，才允许产生 LONG 或 SHORT 信号。阈值越高，信号通常越少，但不代表一定更准确。'],
 fee:['单边手续费','每次开仓或平仓分别收取的费用。完整交易通常要计算开仓与平仓两次费用。'],
 slippage:['滑点','下单预期价格与实际成交价格之间的偏差。波动越大、流动性越差，滑点通常越明显。'],
 kelly:['Kelly 仓位','根据历史胜率与盈亏比估算理论仓位。实际使用应大幅折扣并设置风险上限，不能把历史胜率当成未来保证。'],
 rr:['盈亏比','目标收益与止损风险的比值。例如 1:2 表示每承担1单位风险，目标收益为2单位。'],
 montecarlo:['Monte Carlo','将历史交易结果反复随机抽样，估计可能的收益区间、最大回撤和连续亏损风险。它不能直接证明预测准确率。'],
 walkforward:['滚动验证','用较早数据训练或选参数，再用后续未参与调参的数据测试，并不断向前滚动，减少过拟合。'],
 hitrate:['历史命中率','在指定历史样本中，同方向信号预测下一根K线方向正确的比例。它不是未来保证。'],
 ci:['95%置信区间','根据样本量估计真实命中率可能落入的范围。区间越宽，说明样本越少或结果越不稳定。']
};
function openTerm(key){const x=termGlossary[key];if(!x)return;$('termTitle').textContent=x[0];$('termBody').textContent=x[1];$('termModal').classList.remove('hidden')}
document.addEventListener('click',e=>{const t=e.target.closest('[data-term]');if(t){e.preventDefault();e.stopPropagation();openTerm(t.dataset.term)}});$('termClose').onclick=()=>$('termModal').classList.add('hidden');$('termModal').onclick=e=>{if(e.target===$('termModal'))$('termModal').classList.add('hidden')};

$('tradePlanBtn').onclick=async()=>{setStatus('tradePlanStatus','正在下载历史数据、验证概率并生成计划…','loading');$('tradePlanBtn').disabled=true;$('tradePlanResult').classList.add('hidden');try{const body={...commonBody(),threshold:Number($('threshold').value),sample_size:Number($('directionSamples').value),account_balance:Number($('planBalance').value),max_risk_fraction:Number($('planRiskCap').value),kelly_cap:.05};const j=await api('/api/trade-plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),x=j.result,ci=x.confidence_interval_95_pct||[0,0],rb=x.risk_budget||{},cls=x.action==='LONG'?'long':x.action==='SHORT'?'short':'wait';$('tradePlanResult').innerHTML=`<div class="strategy-summary"><div class="strategy-main ${cls}"><span>${x.contract} · ${x.interval}</span><strong>${x.action_zh}</strong><small>${x.entry_instruction}</small></div><div class="plan-block"><h3>方向与价格</h3>方向：${x.side}<br>参考入场：${fmt(x.entry,4)}<br>止损：${fmt(x.stop,4)}<br>目标：${fmt(x.target,4)}<br>盈亏比：${x.risk_reward?`1:${fmt(x.risk_reward,2)}`:'—'}</div><div class="plan-block"><h3>概率依据 <button type="button" class="term" data-term="hitrate">?</button></h3><span class="probability-badge">${fmt(x.historical_hit_rate_pct,1)}%</span><br>${x.probability_label}<br>成本后有效率：${fmt(x.cost_adjusted_hit_rate_pct,1)}%<br>样本：${x.historical_samples}<br>95%区间：${fmt(ci[0],1)}%–${fmt(ci[1],1)}% <button type="button" class="term" data-term="ci">?</button></div><div class="plan-block kelly-highlight"><h3>调整 Kelly 风险预算</h3>调整后胜率：${pct((rb.adjusted_probability||0)*100)}<br>完整 Kelly：${pct((rb.full_kelly_fraction||0)*100)}<br>四分之一 Kelly：${pct((rb.quarter_kelly_fraction||0)*100)}<br><b>最终单笔风险：${pct((rb.final_risk_fraction||0)*100)}</b><br>最大计划亏损：${fmt(rb.max_loss,2)} USDT</div><div class="plan-block"><h3>离场规则</h3><ul class="plan-list">${(x.exit_rules||[]).map(v=>`<li>${v}</li>`).join('')}</ul></div><div class="plan-block"><h3>结论依据</h3><ul class="plan-list">${(x.rationale||[]).map(v=>`<li>${v}</li>`).join('')}</ul></div><div class="notice-box" style="grid-column:1/-1">${x.notice}</div></div>`;$('tradePlanResult').classList.remove('hidden');setStatus('tradePlanStatus','策略生成完成','success')}catch(e){setStatus('tradePlanStatus',e.message,'error')}finally{$('tradePlanBtn').disabled=false}};

$('decisionBtn').onclick=async()=>{
  setStatus('decisionStatus','正在执行历史校准、多周期共振与风险评估…','loading');
  $('decisionBtn').disabled=true;$('decisionResult').classList.add('hidden');
  try{
    const body={...commonBody(),threshold:Number($('threshold').value),sample_size:Math.max(30,Number($('directionSamples').value)),account_balance:Number($('decisionBalance').value),max_risk_fraction:Number($('decisionRiskCap').value),kelly_cap:.05};
    const j=await api('/api/decision-engine',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),x=j.result,c=x.calibration||{},e=x.economics||{},s=x.signal||{},pi=c.probability_interval_pct||[0,0],cls=x.action==='LONG'?'long':x.action==='SHORT'?'short':'wait';
    lastDecisionData=x;
    const list=(title,rows,klass)=>rows&&rows.length?`<div class="plan-block ${klass}"><h3>${title}</h3><ul class="decision-list">${rows.map(v=>`<li>${v}</li>`).join('')}</ul></div>`:'';
    $('decisionResult').innerHTML=`<div class="decision-summary"><div class="decision-hero"><div class="decision-action ${cls}"><span>${x.contract} · ${x.interval}</span><strong>${x.action_zh}</strong><small>技术方向 ${s.side||'FLAT'}｜等级 ${x.grade}</small></div><div><span>决策评分</span><strong>${fmt(x.decision_score,1)}/100</strong><small>${x.explanation}</small></div></div><div class="decision-grid"><article><span>校准概率</span><b>${pct((c.calibrated_probability||0)*100)}</b></article><article><span>概率区间</span><b>${fmt(pi[0],1)}%–${fmt(pi[1],1)}%</b></article><article><span>样本外成本后率</span><b>${pct((c.empirical_cost_adjusted_probability||0)*100)}</b></article><article><span>同方向样本</span><b>${c.samples||0}</b></article><article><span>样本外总样本</span><b>${c.holdout_samples||0}</b></article><article><span>样本外最大连错</span><b>${c.max_consecutive_wrong_oos||0}</b></article><article><span>多周期对齐</span><b>${pct((c.timeframe_alignment||0)*100)}</b></article><article><span>数据质量</span><b>${x.quality?.grade||'—'} / ${x.quality?.score||0}</b></article><article><span>期望值</span><b>${fmt(e.expected_r,3)}R</b></article><article><span>最终风险</span><b>${pct((e.final_risk_fraction||0)*100)}</b></article><article><span>最大计划亏损</span><b>${fmt(e.max_loss,2)} USDT</b></article><article><span>四分之一Kelly</span><b>${pct((e.quarter_kelly_fraction||0)*100)}</b></article><article><span>资金费率</span><b>${x.funding?.funding_rate_pct==null?'—':`${fmt(x.funding.funding_rate_pct,4)}%`}</b></article><article><span>拥挤状态</span><b>${x.funding?.crowding||'—'}</b></article></div>${list('通过项',x.positives,'decision-positive')}${list('阻止交易的条件',x.blockers,'decision-blocker')}${list('风险提醒',x.warnings,'decision-warning')}<div class="notice-box">${x.notice}</div><button id="decisionToSimBtn" ${s.side==='LONG'||s.side==='SHORT'?'':'disabled'}>带入模拟交易</button></div>`;
    $('decisionResult').classList.remove('hidden');const toSim=$('decisionToSimBtn');if(toSim)toSim.onclick=()=>prefillSimulation(lastDecisionData);setStatus('decisionStatus','决策完成','success');
  }catch(e){setStatus('decisionStatus',e.message,'error')}
  finally{$('decisionBtn').disabled=false}
};


function prefillSimulation(x){
  const sig=x?.signal||{};
  if(!['LONG','SHORT'].includes(sig.side)){setStatus('simStatus','当前决策没有明确LONG/SHORT方向，无法带入','error');return}
  $('simSide').value=sig.side;
  $('simEntry').value=sig.entry??'';
  $('simStop').value=sig.stop??'';
  $('simTarget').value=sig.target??'';
  $('simBalance').value=$('decisionBalance').value||1000;
  $('simRisk').value=String(x?.economics?.final_risk_fraction||$('decisionRiskCap').value||0.01);
  if(!$('simRisk').querySelector(`option[value="${$('simRisk').value}"]`))$('simRisk').value='0.01';
  $('simNotes').value=`9.0 AI决策｜评分${x.decision_score||'—'}｜校准概率${fmt((x.calibration?.calibrated_probability||0)*100,1)}%`;
  document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.target==='simulationPanel'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.add('hidden'));
  $('simulationPanel').classList.remove('hidden');
  setStatus('simStatus','已带入策略参数，请核对后开始模拟','success');
}

function simStatusZh(v){return({OPEN:'持仓中',PENDING:'待成交',CLOSED:'已结算',CANCELLED:'已取消'})[v]||v}
function simReasonZh(v){return({TP:'止盈',SL:'止损',SL_FIRST_AMBIGUOUS:'同K线双触发，保守按止损',TIME:'超时平仓',MANUAL:'手动平仓',CANCELLED:'取消挂单'})[v]||v||'—'}
function simTime(ts){return ts?new Date(ts*1000).toLocaleString('zh-CN'):'—'}
function renderSimulation(j){
  const st=j.stats||{};
  $('simStats').innerHTML=[['已结算',st.closed_trades||0],['胜率',`${fmt(st.win_rate_pct||0,1)}%`],['净盈亏',`${fmt(st.net_pnl||0,2)} USDT`],['平均R',fmt(st.average_r||0,2)],['Profit Factor',st.profit_factor==null?'—':fmt(st.profit_factor,2)],['未结束',st.open_trades||0]].map(([k,v])=>`<article><span>${k}</span><strong>${v}</strong></article>`).join('');
  $('simStorageNotice').textContent=j.storage_notice||'模拟记录已读取。';
  const rows=j.result||[];
  $('simTrades').innerHTML=rows.map(t=>{
    const pnl=Number(t.net_pnl||0),pcls=pnl>0?'sim-positive':pnl<0?'sim-negative':'';
    const mark=t.last_mark==null?'—':fmt(t.last_mark,6),fill=t.fill_price==null?'待成交':fmt(t.fill_price,6);
    const action=t.status==='OPEN'?`<button class="secondary sim-close" data-id="${t.id}">手动平仓</button>`:t.status==='PENDING'?`<button class="secondary sim-close" data-id="${t.id}">取消挂单</button>`:'';
    return `<article class="sim-trade"><div class="sim-trade-main"><div class="sim-trade-head"><strong>${t.contract} · ${t.interval}</strong><span class="viz-badge">${t.side}</span><span class="sim-status">${simStatusZh(t.status)}</span></div><div class="sim-lines"><span>类型：${t.order_type==='MARKET'?'市价模拟':'触价入场'}</span><span>创建：${simTime(t.created_at)}</span><span>成交：${fill}</span><span>最新：${mark}</span><span>止损：${fmt(t.stop,6)}</span><span>止盈：${fmt(t.target,6)}</span><span>数量：${fmt(t.quantity,6)}</span><span>持有：${t.bars_held||0}/${t.max_holding_bars}根</span><span>最大有利：${fmt((t.max_favorable_excursion||0)*100,2)}%</span><span>最大不利：${fmt((t.max_adverse_excursion||0)*100,2)}%</span><span>结果：${simReasonZh(t.exit_reason)}</span><span class="sim-pnl ${pcls}">净盈亏：${t.status==='CLOSED'?`${fmt(pnl,2)} USDT / ${fmt(t.r_multiple,2)}R`:'—'}</span></div>${t.notes?`<small>${t.notes}</small>`:''}</div><div class="sim-actions">${action}</div></article>`
  }).join('')||'<div class="list-empty">暂无模拟交易。</div>';
  document.querySelectorAll('.sim-close').forEach(b=>b.onclick=()=>closeSimulation(b.dataset.id));
}
async function loadSimulation(){
  setStatus('simStatus','正在读取模拟交易并检查最新已收盘K线…','loading');$('simLoadBtn').disabled=true;
  try{const j=await api(`/api/simulation/trades?status=${encodeURIComponent($('simFilter').value)}&refresh=true&_=${Date.now()}`,{},'simulation',30000);renderSimulation(j);setStatus('simStatus','模拟交易已更新','success')}
  catch(e){setStatus('simStatus',e.message,'error')}finally{$('simLoadBtn').disabled=false}
}
async function closeSimulation(id){
  try{const j=await api('/api/simulation/close',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({trade_id:id})},`sim-close-${id}`,20000);setStatus('simStatus',j.result.status==='CANCELLED'?'挂单已取消':'已按当前公开行情模拟平仓','success');await loadSimulation()}
  catch(e){setStatus('simStatus',e.message,'error')}
}
$('simCreateBtn').onclick=async()=>{
  setStatus('simStatus','正在读取最新公开行情并建立模拟订单…','loading');$('simCreateBtn').disabled=true;
  try{
    const strategy=lastDecisionData||{};
    const body={contract:currentContract(),interval:$('interval').value,side:$('simSide').value,order_type:$('simOrderType').value,entry:Number($('simEntry').value),stop:Number($('simStop').value),target:Number($('simTarget').value),account_balance:Number($('simBalance').value),risk_fraction:Number($('simRisk').value),leverage:Number($('simLeverage').value),fee_rate:Number($('fee').value),slippage_rate:Number($('slippage').value),max_holding_bars:Number($('simMaxBars').value),strategy,notes:$('simNotes').value};
    const j=await api('/api/simulation/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)},'sim-create',20000);
    setStatus('simStatus',`${j.result.contract} ${j.result.side} 模拟订单已建立｜${simStatusZh(j.result.status)}`,'success');await loadSimulation();
  }catch(e){setStatus('simStatus',e.message,'error')}finally{$('simCreateBtn').disabled=false}
};
$('simLoadBtn').onclick=loadSimulation;$('simFilter').onchange=loadSimulation;
