"""Frozen local inference. No network, training, trading, or modification of source data."""
from pathlib import Path
from datetime import datetime
import argparse, hashlib, json, math, re, sys, html
import numpy as np
import pandas as pd

HOME=Path(__file__).resolve().parents[1]
MODEL=HOME/'assets/models.json'
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(v,p): Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
def numeric(s): return pd.to_numeric(s.astype(str).str.strip().str.replace(',','',regex=False),errors='coerce')
def date(s):
    datetime.strptime(str(s),'%Y%m%d')
    if len(str(s))!=8: raise ValueError('日期必须为YYYYMMDD')
    return str(s)
def codes(d,column='代码'):
    if column not in d: raise ValueError('缺少代码列 '+column)
    d=d.copy(); d[column]=d[column].astype(str).str.strip().str.replace(r'\.0$','',regex=True).str.replace('="','',regex=False).str.replace('"','',regex=False).str.zfill(6)
    # Summary/footer rows are not securities.
    d=d[d[column].str.fullmatch(r'\d{6}')].copy()
    if d[column].duplicated().any(): raise ValueError('重复证券代码，不可静默覆盖')
    return d
def table(p):
    p=Path(p)
    if p.suffix.lower()=='.xlsx': d=pd.read_excel(p,dtype=str)
    elif p.suffix.lower()=='.xls':
        if p.read_bytes()[:2]==b'PK': d=pd.read_excel(p,engine='openpyxl',dtype=str)
        else: d=pd.read_csv(p,sep='\t',encoding='gb18030',dtype=str)
    else: d=pd.read_csv(p,encoding='utf-8-sig',dtype=str)
    d.columns=d.columns.str.strip(); return codes(d)
def day(details,dt,inputs):
    ps=sorted(Path(details).glob('全部*'+dt+'*'))
    if not ps: raise ValueError('缺少日度明细 '+dt)
    # Standard file first, supplement overrides matching codes.
    ps.sort(key=lambda p:('补充' in p.name,p.name))
    blocks=[]
    for p in ps: blocks.append(table(p)); inputs[str(p)]=sha(p)
    return pd.concat(blocks).drop_duplicates('代码',keep='last')
def minute(v):
    s=str(v).strip().replace(':',''); s=re.sub(r'\.0$','',s).zfill(6)
    if not re.fullmatch(r'\d{6}',s): return np.nan
    h,m,ss=int(s[:2]),int(s[2:4]),int(s[4:]); val=h*60+m+ss/60
    if m>59 or ss>59 or not(565<=val<=690 or 780<=val<=900): return np.nan
    return max(0,val-570-(90 if val>=780 else 0))
def predict(frame,model):
    x=frame[model['features']].to_numpy(float); missing=~np.isfinite(x)
    x=np.where(missing,np.array(model['median']),x)
    x=np.clip(x,model['clip_low'],model['clip_high']); z=(x-model['mean'])/model['std']
    if model['kind']=='logistic':
        y=model['coef'][0]+z@np.array(model['coef'][1:]); y=1/(1+np.exp(-np.clip(y,-30,30)))
    else: y=model['intercept']+z@np.array(model['coef'])
    return y,missing.sum(axis=1)
def bounded(pred,close):
    low=(np.floor(close*.9*100+.5+1e-8)/100/close-1)*100
    high=(np.floor(close*1.1*100+.5+1e-8)/100/close-1)*100
    return np.clip(pred,low,high),low,high
def score_features(z,bundle):
    z=z.copy(); p,n=predict(z,bundle['first_board']); z['首板晋级分']=p*100
    pred,n2=predict(z,bundle['opening']); mu,low,high=bounded(pred,z['最新价'].to_numpy(float))
    z['预期开盘%']=mu; z['常态下界%']=np.maximum(mu+bundle['residual_low'],low); z['常态上界%']=np.minimum(mu+bundle['residual_high'],high)
    z['预期开盘强度分']=np.searchsorted(bundle['opening_reference'],mu,side='right')/len(bundle['opening_reference'])*100
    z['量能预期对数']=predict(z,bundle['auction_volume'])[0]
    z['缺失填补字段数']=n2
    z['价格上限约束']=mu+bundle['residual_high']>=high
    z=z.sort_values(['首板晋级分','代码'],ascending=[False,True]).reset_index(drop=True)
    z['首板排名']=np.arange(1,len(z)+1); z['每日前20%']=z['首板排名']<=math.ceil(len(z)*.2)
    return z
def eod(args,bundle):
    sd=date(args.date); ed=date(args.evaluation_date)
    if ed<=sd: raise ValueError('竞价日必须晚于评分日')
    if sd<bundle['effective_signal_date'] and not args.retrospective: raise ValueError('评分日早于模型生效日；只可显式用--retrospective做回溯演示')
    inputs={}; root=Path(args.pool_dir); folder=root/sd
    sp=folder/'snapshot.json'; snap=json.loads(sp.read_text(encoding='utf-8-sig')); inputs[str(sp)]=sha(sp)
    if str(snap['交易日期'])!=sd or not snap['数据源状态']['涨停池']['ok']: raise ValueError('涨停池日期或源状态无效')
    ep=root/ed/'snapshot.json'; calendar='explicit_target_date'
    known_next=sorted(p.name for p in root.iterdir() if p.is_dir() and re.fullmatch(r'\d{8}',p.name) and sd<p.name<ed and (p/'snapshot.json').exists())
    if known_next: raise ValueError('评分日与竞价日之间存在已知交易日 '+known_next[0])
    if ep.exists():
        es=json.loads(ep.read_text(encoding='utf-8-sig'))
        if str(es['上一交易日'])!=sd: raise ValueError('指定竞价日不是评分日的下一交易日')
        calendar='snapshot_previous_trade_date'; inputs[str(ep)]=sha(ep)
    pp=folder/'zt_pool.csv'; z=table(pp); inputs[str(pp)]=sha(pp)
    if not z['交易日期'].astype(str).eq(sd).all(): raise ValueError('涨停CSV中存在错日期')
    d=day(args.details_dir,sd,inputs)
    for c in ['现价','涨幅%','最高','最低','昨成交额','总金额']:
        if c not in d: d[c]=np.nan
        d[c]=numeric(d[c])
    for c in ['最新价','涨跌幅','连板数','封板资金','成交额','流通市值','换手率','炸板次数']: z[c]=numeric(z[c])
    z=z.merge(d[['代码','现价','涨幅%','最高','最低','昨成交额','总金额']],on='代码',how='left',validate='one_to_one')
    z['first_min']=z['首次封板时间'].map(minute); z['last_min']=z['最后封板时间'].map(minute)
    z['early']=1-z.first_min/240; z['stable']=1-z.last_min/240
    z['one_price']=((z['最高']-z['最低']).abs()<.005).astype(float)
    z['seal_turn']=np.log1p(z['封板资金']/z['成交额']); z['seal_cap']=np.log1p(z['封板资金']/z['流通市值']*100)
    z['breaks']=np.log1p(z['炸板次数']); z['turn']=np.log1p(z['换手率']); z['turn_sq']=z.turn**2
    z['cap']=np.log(z['流通市值']/1e8); z['volume_ratio']=np.log((z['成交额']/(z['昨成交额']*10000)).clip(.01,100)); z['height']=z['连板数'].clip(upper=6)
    z['industry_count']=z.groupby('所属行业')['代码'].transform('count')-1; z['industry_share']=z.industry_count/len(z)
    z['industry_count']=np.log1p(z.industry_count); z['industry_height']=z.groupby('所属行业')['连板数'].transform('max')
    z['market_count']=np.log(len(z)); z['market_seal']=float(snap['市场概览']['封板率_pct'])/100
    # Fetch only 5 verified signal dates; no t+1 outcome fields are read for features.
    dates=[sd]
    for _ in range(4):
        q=root/dates[-1]/'snapshot.json'
        if not q.exists(): break
        js=json.loads(q.read_text(encoding='utf-8-sig')); inputs[str(q)]=sha(q)
        prev=str(js['上一交易日'])
        if prev>=dates[-1]: raise ValueError('交易日历不递减')
        dates.append(prev)
    returns=[]
    for dt in reversed(dates):
        try:
            dd=d if dt==sd else day(args.details_dir,dt,inputs)
            r=dd.set_index('代码')['涨幅%']; returns.append(numeric(r).rename(dt))
        except (ValueError,KeyError): returns.append(pd.Series(dtype=float,name=dt))
    rs=pd.concat(returns,axis=1)
    recent=((1+rs/100).prod(axis=1,min_count=5)-1)*100 if len(dates)==5 else pd.Series(np.nan,index=rs.index)
    z['recent']=z['代码'].map(recent).clip(-50,150)/10
    issues=[]
    for _,r in z.iterrows():
        reason=[]
        if not r['代码'].startswith(('00','60')): reason.append('非普通10%主板')
        if re.search(r'ST|退|^N|^C',str(r['名称']),re.I): reason.append('特殊证券')
        if r['连板数']!=1: reason.append('非首板')
        if not 9<=r['涨跌幅']<=11: reason.append('涨停涨幅异常')
        if not(abs(r['最新价']-r['现价'])<=.011 and abs(r['涨跌幅']-r['涨幅%'])<=.15): reason.append('收盘行情缺失或冲突')
        if not(r['成交额']>0 and r['流通市值']>0 and r['封板资金']>=0 and r['炸板次数']>=0): reason.append('金额或炸板次数无效')
        if not(np.isfinite(r['first_min']) and np.isfinite(r['last_min']) and r['last_min']>=r['first_min']): reason.append('封板时间无效')
        if not(np.isfinite(r['最高']) and np.isfinite(r['最低']) and r['最高']>=r['最低']>0): reason.append('日线高低价缺失')
        if not np.isfinite(r['volume_ratio']): reason.append('昨日成交额缺失')
        issues.append('；'.join(reason))
    z['排除原因']=issues; bad=z[z['排除原因']!=''].copy(); z=z[z['排除原因']==''].copy()
    if z.empty: raise ValueError('没有可评分的合格首板')
    z=score_features(z,bundle); z['评分日']=sd; z['竞价日']=ed
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    z.to_csv(out/'eod_state.csv',index=False,encoding='utf-8-sig'); bad[['代码','名称','连板数','排除原因']].to_csv(out/'排除明细.csv',index=False,encoding='utf-8-sig')
    cols=['评分日','竞价日','代码','名称','首板晋级分','首板排名','每日前20%','预期开盘%','常态下界%','常态上界%','价格上限约束','缺失填补字段数']
    z[cols].to_csv(out/'首板评分.csv',index=False,encoding='utf-8-sig')
    top=z[z['每日前20%']]
    meta={'version':bundle['version'],'model_sha256':sha(MODEL),'signal_date':sd,'evaluation_date':ed,'calendar_check':calendar,'retrospective':bool(args.retrospective),'inputs':inputs,'rows':len(z),'top_rows':len(top),'excluded':len(bad),'state_sha256':sha(out/'eod_state.csv')}
    dump(meta,out/'manifest.json')
    print(json.dumps(meta,ensure_ascii=False))
def auction(args,bundle):
    root=Path(args.eod); meta=json.loads((root/'manifest.json').read_text(encoding='utf-8')); ed=date(args.date)
    if ed!=meta['evaluation_date']: raise ValueError('竞价日期与昨日冻结文件不一致')
    if sha(MODEL)!=meta['model_sha256'] or sha(root/'eod_state.csv')!=meta['state_sha256']: raise ValueError('模型或昨晚数据被修改，停止评价')
    z=table(root/'eod_state.csv')
    for c in ['最新价','成交额','预期开盘%','首板晋级分','量能预期对数']: z[c]=numeric(z[c])
    src=Path(args.input); q=table(src)
    if args.kind=='details-replay':
        if not args.replay: raise ValueError('收盘明细回放必须明确--replay，不能冒充实时9:25快照')
        if ed not in src.name: raise ValueError('回放文件名不含目标日期')
        q=q[['代码','今开','开盘%','开盘金额']].copy(); q['开盘价']=numeric(q['今开']); q['竞价金额元']=numeric(q['开盘金额'])*10000
        q['参考昨收']=q['开盘价']/(1+numeric(q['开盘%'])/100)
    else:
        required=['竞价日期','快照时间','开盘价','参考昨收','竞价金额元']
        if any(x not in q for x in required): raise ValueError('竞价CSV缺字段，参见references/input-schema.md')
        if not q['竞价日期'].eq(ed).all(): raise ValueError('竞价输入混入其他日期')
        tm=pd.to_datetime(q['快照时间'],errors='coerce')
        if tm.isna().any() or not tm.dt.strftime('%Y%m%d').eq(ed).all() or not tm.dt.strftime('%H:%M:%S').ge('09:25:00').all(): raise ValueError('必须为目标日9:25之后的已确定开盘数据')
        for c in ['开盘价','参考昨收','竞价金额元']: q[c]=numeric(q[c])
    z=z.merge(q[['代码','开盘价','参考昨收','竞价金额元']],on='代码',how='left',validate='one_to_one')
    z['竞价日期']=ed; z['实际开盘%']=(z['开盘价']/z['参考昨收']-1)*100
    z['偏离百分点']=z['实际开盘%']-z['预期开盘%']; r=z['偏离百分点'].to_numpy()
    z['竞价超预期分']=np.searchsorted(bundle['residual_reference'],r,side='right')/len(bundle['residual_reference'])*100
    z['竞价评价']=np.select([r<bundle['residual_low'],r>bundle['residual_high']],['不及预期','超预期'],default='符合预期')
    # Physical price and reference validation. Missing/open=0 are never treated as misses.
    _,low,high=bounded(np.zeros(len(z)),z['最新价'].to_numpy(float))
    valid=z['开盘价'].gt(0)&z['参考昨收'].gt(0)&((z['参考昨收']/z['最新价']-1).abs()*100<=.2)&z['实际开盘%'].between(low-.2,high+.2)
    z.loc[~valid,['实际开盘%','偏离百分点','竞价超预期分']]=np.nan; z.loc[~valid,'竞价评价']='待核验：缺开盘／参考价异常'
    z['竞价额占昨成交额%']=z['竞价金额元']/z['成交额']*100
    vr=np.log1p(z['竞价额占昨成交额%'])-z['量能预期对数']
    z['量能标签']=np.select([vr<bundle['volume_low'],vr>bundle['volume_high']],['缩量','放量'],default='正常量')
    z.loc[~valid|~z['竞价金额元'].gt(0),'量能标签']='量能待核验'
    cols=['竞价日期','代码','名称','首板晋级分','首板排名','每日前20%','预期开盘%','实际开盘%','偏离百分点','竞价超预期分','竞价评价','竞价额占昨成交额%','量能标签','价格上限约束']
    out=Path(args.out)
    if out.resolve()==root.resolve(): raise ValueError('竞价输出请使用单独目录，不覆盖前晚冻结结果')
    out.mkdir(parents=True,exist_ok=True); z[cols].to_csv(out/'竞价评价.csv',index=False,encoding='utf-8-sig')
    result={'version':bundle['version'],'input':str(src),'input_sha256':sha(src),'eod_manifest_sha256':sha(root/'manifest.json'),'date':ed,'mode':'replay' if args.replay else 'auction_snapshot','rows':len(z),'status_counts':z['竞价评价'].value_counts().to_dict()}
    dump(result,out/'manifest.json'); print(json.dumps(result,ensure_ascii=False))
def main():
    ap=argparse.ArgumentParser(description=__doc__); sub=ap.add_subparsers(dest='mode',required=True)
    e=sub.add_parser('eod'); e.add_argument('--date',required=True); e.add_argument('--evaluation-date',required=True); e.add_argument('--pool-dir',default=r'D:\OneDrive\Stock\短线数据采集'); e.add_argument('--details-dir',default=r'D:\OneDrive\Stock\details'); e.add_argument('--out',required=True); e.add_argument('--retrospective',action='store_true')
    a=sub.add_parser('auction'); a.add_argument('--eod',required=True); a.add_argument('--date',required=True); a.add_argument('--input',required=True); a.add_argument('--kind',choices=['snapshot','details-replay'],default='snapshot'); a.add_argument('--replay',action='store_true'); a.add_argument('--out',required=True)
    args=ap.parse_args(); bundle=json.loads(MODEL.read_text(encoding='utf-8'))
    try: (eod if args.mode=='eod' else auction)(args,bundle)
    except (ValueError,KeyError,FileNotFoundError) as exc: print('输入校验失败：'+str(exc),file=sys.stderr); sys.exit(2)
if __name__=='__main__': main()
