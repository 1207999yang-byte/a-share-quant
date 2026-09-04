import streamlit as st
import pandas as pd
import numpy as np
from io import StringIO

st.set_page_config(page_title="A股量化工具 V2.0", page_icon="📈", layout="wide")
st.title("📈 A股量化工具 V2.0")
st.caption("多因子选股 + 趋势择时 + 回撤风控 + 历史回测")

# ---------------- 数据与指标 ----------------
def max_drawdown(nav):
    peak = nav.cummax()
    dd = nav / peak - 1
    return dd.min()

def sharpe(returns, periods=252):
    if returns.std() == 0 or len(returns) < 2:
        return np.nan
    return returns.mean() / returns.std() * np.sqrt(periods)

def score_cross_section(df):
    out=df.copy()
    def rank(s, high=True):
        return s.rank(pct=True, ascending=high)*100
    out["估值分"]=(rank(out["PE"],False)+rank(out["PB"],False))/2
    out["盈利质量分"]=rank(out["ROE"],True)
    out["成长分"]=(rank(out["营收增长率"],True)+rank(out["净利润增长率"],True))/2
    out["动量分"]=(rank(out["6个月收益率"],True)+rank(out["12个月收益率"],True))/2
    out["低波分"]=rank(out["120日波动率"],False)
    out["综合评分"]=(
        .25*out["估值分"]+.25*out["盈利质量分"]+.20*out["成长分"]+
        .20*out["动量分"]+.10*out["低波分"]
    )
    return out.sort_values("综合评分",ascending=False).reset_index(drop=True)

st.sidebar.header("策略参数")
initial = st.sidebar.number_input("初始资金", 10000, 10000000, 100000, 10000)
top_n = st.sidebar.slider("持仓股票数", 5, 30, 10)
max_sector = st.sidebar.slider("单行业最大仓位（若有行业字段）", 10, 100, 30)
fee = st.sidebar.number_input("单边交易费率", 0.0000, 0.01, 0.0003, 0.0001, format="%.4f")
cash_buffer = st.sidebar.slider("最低现金比例", 0, 50, 10) / 100

tabs=st.tabs(["① 多因子选股","② 历史回测","③ 10万元模拟","④ 使用说明"])

with tabs[0]:
    st.subheader("多因子排名")
    up=st.file_uploader("上传横截面股票数据 CSV", type="csv", key="factor")
    required=["股票代码","股票名称","PE","PB","ROE","营收增长率","净利润增长率","6个月收益率","12个月收益率","120日波动率"]
    if up:
        df=pd.read_csv(up)
        miss=[x for x in required if x not in df.columns]
        if miss:
            st.error("缺少字段："+ "、".join(miss))
        else:
            for c in required[2:]:
                df[c]=pd.to_numeric(df[c],errors="coerce")
            df=df.dropna(subset=required[2:])
            df=df[(df.PE>0)&(df.PB>0)]
            if "ST" in df:
                df=df[~df["ST"].astype(str).str.upper().isin(["TRUE","1","YES","是"])]
            ranked=score_cross_section(df)
            ranked.insert(0,"排名",np.arange(1,len(ranked)+1))
            st.dataframe(ranked.head(top_n),use_container_width=True,hide_index=True)
            st.download_button("下载排名",ranked.to_csv(index=False).encode("utf-8-sig"),"量化排名_V2.csv","text/csv")
    else:
        template=pd.DataFrame([{
            "股票代码":"600000","股票名称":"示例股票","PE":12,"PB":1.2,"ROE":13,
            "营收增长率":10,"净利润增长率":15,"6个月收益率":8,
            "12个月收益率":12,"120日波动率":20,"行业":"银行"
        }])
        st.write("模板：")
        st.dataframe(template,use_container_width=True,hide_index=True)
        st.download_button("下载横截面模板",template.to_csv(index=False).encode("utf-8-sig"),"横截面模板.csv","text/csv")

with tabs[1]:
    st.subheader("历史回测")
    st.markdown("上传**单个策略组合或指数的日线数据**。V2 使用后复权收盘价进行研究；这类数据通常用于量化投资研究。")
    up2=st.file_uploader("上传回测CSV",type="csv",key="backtest")
    st.caption("格式：日期, 收盘价；可选：基准收盘价。")
    if up2:
        px=pd.read_csv(up2)
        if not {"日期","收盘价"}.issubset(px.columns):
            st.error("CSV至少需要：日期、收盘价")
        else:
            px["日期"]=pd.to_datetime(px["日期"],errors="coerce")
            px["收盘价"]=pd.to_numeric(px["收盘价"],errors="coerce")
            px=px.dropna().sort_values("日期").drop_duplicates("日期")
            px["策略日收益"]=px["收盘价"].pct_change().fillna(0)
            # 模拟：满仓持有，扣除单边费率的近似双边成本
            px["净日收益"]=px["策略日收益"] - fee*np.abs(px["策略日收益"])
            px["净值"]=(1+px["净日收益"]).cumprod()
            px["资产"]=initial*px["净值"]
            total=px["净值"].iloc[-1]-1
            years=max((px["日期"].iloc[-1]-px["日期"].iloc[0]).days/365.25,1/365.25)
            cagr=px["净值"].iloc[-1]**(1/years)-1
            mdd=max_drawdown(px["净值"])
            sr=sharpe(px["净日收益"])
            a,b,c,d=st.columns(4)
            a.metric("累计收益",f"{total:.2%}")
            b.metric("年化收益",f"{cagr:.2%}")
            c.metric("最大回撤",f"{mdd:.2%}")
            d.metric("夏普比率",f"{sr:.2f}" if pd.notna(sr) else "—")
            chart=px.set_index("日期")[["资产"]]
            st.line_chart(chart)
            st.dataframe(px.tail(20),use_container_width=True,hide_index=True)
            st.download_button("下载回测结果",px.to_csv(index=False).encode("utf-8-sig"),"回测结果_V2.csv","text/csv")

with tabs[2]:
    st.subheader("10万元仓位模拟")
    capital=initial
    invest=capital*(1-cash_buffer)
    per=invest/top_n
    st.metric("本金",f"¥{capital:,.0f}")
    st.metric("最低现金",f"¥{capital*cash_buffer:,.0f}")
    st.metric("个股理论等权",f"¥{per:,.0f}")
    st.write("如果使用行业字段，实盘时还应进一步按行业上限重新分配仓位。")
    st.markdown("""
**默认风控规则**
- 核心资产趋势跌破120日均线：降低风险仓位。
- 组合回撤10%～15%：减少风险仓位。
- 组合回撤超过20%：暂停新增个股。
- 单行业不超过30%。
- 单只股票默认约2%～10%，避免单股风险过大。
""")

with tabs[3]:
    st.subheader("数据要求与注意事项")
    st.markdown("""
### 横截面选股数据
必需字段：
`股票代码、股票名称、PE、PB、ROE、营收增长率、净利润增长率、6个月收益率、12个月收益率、120日波动率`

### V2.0 与 V1.0 的区别
- 可调参数
- 选股排名
- 历史净值曲线
- CAGR / 最大回撤 / 夏普比率
- 10万元仓位模拟
- 交易费率参数
- 趋势与回撤风控框架

### 非常重要
这个工具用于研究和模拟，不保证收益，也不构成投资建议。
严谨回测还需要处理：停牌、涨跌停、退市股票、幸存者偏差、财务数据发布日期、复权方式、滑点、印花税、ETF跟踪误差等。
""")
