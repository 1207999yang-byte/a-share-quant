import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime
import re

try:
    import akshare as ak
except Exception:
    ak = None

st.set_page_config(page_title='A股量化选股工具 V5.0', page_icon='📈', layout='wide')
st.title('📈 A股量化选股工具 V5.0')
st.caption('自动选出前10名 + 多因子评分 + 趋势增强 + 参考买入区间/止损/目标位/仓位；仅用于研究与模拟。')
WEIGHTS = {'估值': 0.25, '盈利质量': 0.25, '成长': 0.20, '动量': 0.20, '低波': 0.10}

def clean_code(x):
    s = str(x).strip().upper()
    m = re.search(r'(?<!\d)(\d{6})(?!\d)', s)
    if m: return m.group(1)
    d = re.sub(r'\D', '', s)
    return d.zfill(6) if d else ''

def market_symbol(code):
    code = clean_code(code)
    if code.startswith(('60','68')): return 'sh' + code
    if code.startswith(('00','30')): return 'sz' + code
    if code.startswith(('8','4')): return 'bj' + code
    return ''

def num(x):
    try:
        if pd.isna(x): return np.nan
        return float(str(x).replace(',','').replace('%','').strip())
    except Exception: return np.nan

def pick_col(df, names):
    cols = [str(c) for c in df.columns]
    for name in names:
        for i, c in enumerate(cols):
            if c == name: return df.columns[i]
    for name in names:
        for i, c in enumerate(cols):
            if name.lower() in c.lower(): return df.columns[i]
    return None

@st.cache_data(ttl=900, show_spinner=False)
def load_market():
    if ak is None: raise RuntimeError('AKShare 未安装')
    errs=[]
    try:
        df=ak.stock_zh_a_spot_em()
        if df is not None and not df.empty: return df,'东方财富'
    except Exception as e: errs.append('东方财富：'+str(e)[:100])
    try:
        df=ak.stock_zh_a_spot()
        if df is not None and not df.empty: return df,'新浪'
    except Exception as e: errs.append('新浪：'+str(e)[:100])
    raise RuntimeError('行情源均失败；'+' | '.join(errs))

def normalize_market(df):
    mapping={
        '代码':['代码','symbol'], '名称':['名称','name'], '价格':['最新价','price'],
        '涨跌幅':['涨跌幅','change'], 'PE':['市盈率','pe'], 'PB':['市净率','pb'],
        '换手率':['换手率','turnover'], '总市值':['总市值','marketcap','market cap']}
    out=pd.DataFrame(index=df.index)
    for target,names in mapping.items():
        c=pick_col(df,names); out[target]=df[c] if c is not None else np.nan
    out['代码']=out['代码'].map(clean_code)
    for c in ['价格','涨跌幅','PE','PB','换手率','总市值']: out[c]=out[c].map(num)
    out=out[out['代码'].str.match(r'^(00|30|60|68|4|8)',na=False)]
    out=out[out['价格'].notna() & (out['价格']>0)]
    return out.drop_duplicates('代码').reset_index(drop=True)

@st.cache_data(ttl=3600, show_spinner=False)
def history(code):
    code=clean_code(code); errors=[]
    try:
        df=ak.stock_zh_a_hist(symbol=code,period='daily',start_date='20240101',end_date=datetime.now().strftime('%Y%m%d'),adjust='qfq')
        if df is not None and not df.empty: return df,'东方财富K线'
    except Exception as e: errors.append(str(e)[:70])
    try:
        sym=market_symbol(code)
        if sym:
            df=ak.stock_zh_a_hist_tx(symbol=sym,start_date='2024-01-01',end_date=datetime.now().strftime('%Y-%m-%d'),adjust='qfq')
            if df is not None and not df.empty: return df,'腾讯K线'
    except Exception as e: errors.append(str(e)[:70])
    return pd.DataFrame(),'；'.join(errors)

def hist_factors(code):
    df,src=history(code)
    if df.empty: return np.nan,np.nan,src
    c=pick_col(df,['收盘','close'])
    if c is None: return np.nan,np.nan,src
    close=pd.to_numeric(df[c],errors='coerce').dropna()
    if len(close)<30: return np.nan,np.nan,src
    base=close.iloc[-61] if len(close)>=61 else close.iloc[0]
    mom=(close.iloc[-1]/base-1)*100
    vol=close.pct_change().dropna().tail(60).std()*np.sqrt(250)*100
    return mom,vol,src


def trading_plan(code, current_price, risk_profile):
    df, src = history(code)
    result = {"趋势":"数据不足","MA20":np.nan,"MA60":np.nan,
              "买入下限":np.nan,"买入上限":np.nan,"止损位":np.nan,
              "目标位":np.nan,"参考仓位":np.nan}
    if df.empty:
        return result, src
    c = pick_col(df, ['收盘','close'])
    if c is None:
        return result, src
    close = pd.to_numeric(df[c], errors='coerce').dropna()
    if len(close) < 20:
        return result, src
    ma20 = close.tail(20).mean()
    ma60 = close.tail(60).mean() if len(close) >= 60 else np.nan
    if np.isfinite(ma60):
        trend = '上升' if close.iloc[-1] > ma20 > ma60 else ('下降' if close.iloc[-1] < ma20 < ma60 else '震荡')
    else:
        trend = '上升' if close.iloc[-1] > ma20 else '震荡'
    params = {
        '保守': {'entry':0.025,'stop':0.07,'target':0.12,'pos':10},
        '稳健': {'entry':0.020,'stop':0.06,'target':0.15,'pos':15},
        '积极': {'entry':0.015,'stop':0.05,'target':0.20,'pos':20}
    }[risk_profile]
    low = current_price * (1 - params['entry'])
    high = current_price * 1.005
    # 明显偏离20日均线时，不把“追高”区间抬高
    if np.isfinite(ma20) and current_price > ma20 * 1.06:
        high = max(low, ma20 * 1.02)
    result.update({
        '趋势':trend, 'MA20':ma20, 'MA60':ma60,
        '买入下限':low, '买入上限':high,
        '止损位':current_price*(1-params['stop']),
        '目标位':current_price*(1+params['target']),
        '参考仓位':params['pos']
    })
    return result, src


def pct_score(s,higher=True):
    x=pd.to_numeric(s,errors='coerce'); valid=x.notna(); out=pd.Series(np.nan,index=s.index,dtype=float)
    if valid.sum()>=2:
        r=x[valid].rank(pct=True)*100; out.loc[valid]=r if higher else 100-r
    return out

def score_market(df):
    o=df.copy(); pe=o['PE'].where(o['PE']>0); pb=o['PB'].where(o['PB']>0)
    a=pct_score(pe,False); b=pct_score(pb,False)
    o['估值']=a*0.65+b*0.35; o['盈利质量']=np.nan; o['成长']=np.nan
    o['动量']=pct_score(o['涨跌幅'],True); o['低波']=np.nan
    return o

def enrich(o,n):
    o=o.copy(); ms=[]; vs=[]; ss=[]
    for code in o.head(n)['代码']:
        m,v,s=hist_factors(code); ms.append(m);vs.append(v);ss.append(s)
    o['60日动量']=np.nan;o['年化波动率']=np.nan;o['K线数据源']=''
    idx=o.head(n).index;o.loc[idx,'60日动量']=ms;o.loc[idx,'年化波动率']=vs;o.loc[idx,'K线数据源']=ss
    o['动量']=pct_score(o['60日动量'],True).fillna(pct_score(o['涨跌幅'],True))
    o['低波']=pct_score(o['年化波动率'],False)
    return o

def final_score(o):
    o=o.copy(); scores=[]; completeness=[]
    for _,r in o.iterrows():
        vals=[]; ws=[]
        for c,w in WEIGHTS.items():
            v=num(r.get(c,np.nan))
            if np.isfinite(v): vals.append(v);ws.append(w)
        scores.append(np.average(vals,weights=ws) if vals else np.nan)
        completeness.append(len(vals)/5*100)
    o['综合评分']=scores;o['数据完整度']=completeness
    o['信号']=np.select([o['综合评分']>=75,o['综合评分']>=60,o['综合评分']>=45],['重点关注','观察','回避'],default='数据不足')
    return o.sort_values(['综合评分','数据完整度'],ascending=False)

with st.sidebar:
    st.header('筛选条件')
    risk_profile=st.selectbox('风险偏好',['保守','稳健','积极'],index=1)
    min_price=st.number_input('最低股价',0.0,10000.0,3.0,0.5)
    max_price=st.number_input('最高股价',0.0,10000.0,300.0,5.0)
    min_cap=st.number_input('最低市值（亿元）',0.0,100000.0,50.0,10.0)
    max_pe=st.number_input('最高动态PE（0=不限制）',0.0,100000.0,80.0,5.0)
    top_n=st.slider('最终显示数量',5,30,10)
    enrich_n=st.slider('历史K线增强数量',10,100,30)
    exclude_st=st.checkbox('排除ST/*ST',True)
    only_up=st.checkbox('只保留上升趋势',False)
    run=st.button('🚀 自动选股',type='primary',use_container_width=True)

if run:
    with st.spinner('正在获取全市场行情……'):
        try: raw,source=load_market(); market=normalize_market(raw)
        except Exception as e: st.error(f'行情获取失败：{e}'); st.stop()
    if exclude_st: market=market[~market['名称'].astype(str).str.contains('ST',case=False,na=False)]
    market=market[(market['价格']>=min_price)&(market['价格']<=max_price)]
    market=market[market['总市值'].isna()|(market['总市值']>=min_cap*1e8)]
    if max_pe>0: market=market[market['PE'].isna()|(market['PE']<=max_pe)]
    if market.empty: st.warning('当前条件没有候选股票，请放宽筛选。');st.stop()
    st.info(f'行情数据源：{source}｜筛选后候选：{len(market)} 只')
    s=score_market(market).sort_values('估值',ascending=False).reset_index(drop=True)
    with st.spinner(f'正在为前 {min(enrich_n,len(s))} 只候选补充历史K线……'): s=enrich(s,min(enrich_n,len(s)))
    s=final_score(s)
    plans=[]; sources=[]
    for _, r in s.head(min(enrich_n,len(s))).iterrows():
        p, src = trading_plan(r['代码'], r['价格'], risk_profile)
        plans.append(p); sources.append(src)
    if plans:
        p_df=pd.DataFrame(plans,index=s.head(len(plans)).index)
        for c in p_df.columns: s.loc[p_df.index,c]=p_df[c]
    if only_up: s=s[s['趋势']=='上升']
    show=s.head(top_n).copy();show.insert(0,'排名',range(1,len(show)+1))
    cols=['排名','代码','名称','价格','涨跌幅','PE','PB','总市值','估值','盈利质量','成长','动量','低波','综合评分','数据完整度','趋势','买入下限','买入上限','止损位','目标位','参考仓位','信号']
    show=show[[c for c in cols if c in show.columns]]
    for c in ['价格','涨跌幅','PE','PB','估值','盈利质量','成长','动量','低波','综合评分','数据完整度','MA20','MA60','买入下限','买入上限','止损位','目标位','参考仓位']: show[c]=show[c].round(2) if c in show.columns else show.get(c)
    show['总市值']=(show['总市值']/1e8).round(1)
    st.subheader('🏆 量化选股结果');st.dataframe(show,use_container_width=True,hide_index=True)
    st.caption('综合评分权重：估值25%｜盈利质量25%｜成长20%｜动量20%｜低波10%。当前版本对缺失因子按实际可用数据重新归一化，不虚构缺失财务数据。')
    if len(show):
        r=show.iloc[0]
        st.success(f"模型第一名：{r['代码']} {r['名称']}｜评分 {r['综合评分']:.1f}｜完整度 {r['数据完整度']:.0f}%｜{r['信号']}")
        c1,c2,c3,c4=st.columns(4)
        c1.metric('参考买入下限', f"{r['买入下限']:.2f}" if pd.notna(r.get('买入下限')) else '-')
        c2.metric('参考买入上限', f"{r['买入上限']:.2f}" if pd.notna(r.get('买入上限')) else '-')
        c3.metric('参考止损位', f"{r['止损位']:.2f}" if pd.notna(r.get('止损位')) else '-')
        c4.metric('参考目标位', f"{r['目标位']:.2f}" if pd.notna(r.get('目标位')) else '-')
    st.download_button('⬇️ 下载前100名CSV',s.head(100).to_csv(index=False).encode('utf-8-sig'),'a_share_quant_v5_results.csv','text/csv',use_container_width=True)
else:
    st.markdown('### 使用方法\n1. 左侧设置价格、市值、PE等条件。\n2. 点击「开始全市场选股」。\n3. 工具建立A股候选池，并对排名靠前的股票补充历史K线。\n4. 按多因子综合评分排序并支持CSV导出。')
