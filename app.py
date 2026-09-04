
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import plotly.graph_objects as go
import re

try:
    import akshare as ak
except Exception:
    ak = None

st.set_page_config(
    page_title="A股量化助手 V3.1",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📈 A股量化助手 V3.1")
st.caption("自动行情 + 多源容错 + 股票搜索 + 多因子量化评分 + K线 + 买入/观望/回避 + 10万元模拟账户")
st.warning("本工具仅用于信息整理、研究与模拟，不构成投资建议；行情/财务数据来自第三方公开数据源，可能存在延迟、缺失或接口波动。")

# -------------------- 通用工具 --------------------
def clean_code(code):
    s = str(code).strip().upper()
    s = re.sub(r"\.SH$|\.SZ$|\.BJ$", "", s)
    s = re.sub(r"[^0-9]", "", s)
    return s.zfill(6) if s else ""

def market_suffix(code):
    code = clean_code(code)
    if code.startswith(("600", "601", "603", "605", "688")):
        return f"{code}.SH"
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return f"{code}.SZ"
    if code.startswith(("430", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873")):
        return f"{code}.BJ"
    return f"{code}.SZ"

def to_num(v):
    try:
        return float(pd.to_numeric(v, errors="coerce"))
    except Exception:
        return np.nan

def safe_pct(v):
    x = to_num(v)
    return x / 100 if abs(x) > 2 else x

def max_drawdown(nav):
    peak = nav.cummax()
    return (nav / peak - 1).min()

def annualized_vol(ret):
    ret = pd.Series(ret).dropna()
    if len(ret) < 20:
        return np.nan
    return ret.std() * np.sqrt(252)

def score_lower_better(x, good, bad):
    if pd.isna(x):
        return np.nan
    if x <= good:
        return 100.0
    if x >= bad:
        return 0.0
    return 100 * (bad - x) / (bad - good)

def score_higher_better(x, bad, good):
    if pd.isna(x):
        return np.nan
    if x >= good:
        return 100.0
    if x <= bad:
        return 0.0
    return 100 * (x - bad) / (good - bad)

# -------------------- 数据获取 --------------------
@st.cache_data(ttl=1800, show_spinner=False)
def load_spot():
    """优先东财；失败后尝试新浪。注意：两者都是公开数据源，可能被限流。"""
    if ak is None:
        raise RuntimeError("AKShare 未成功安装。")
    errors = []
    for fn in (getattr(ak, "stock_zh_a_spot_em", None), getattr(ak, "stock_zh_a_spot", None)):
        if fn is None:
            continue
        try:
            df = fn()
            if df is not None and not df.empty and "代码" in df.columns:
                return df
        except Exception as e:
            errors.append(type(e).__name__ + ": " + str(e)[:160])
    raise RuntimeError("实时行情源暂时不可用。可直接输入股票代码继续分析。\n" + " | ".join(errors))

@st.cache_data(ttl=300, show_spinner=False)
def load_quote(code):
    """单只股票报价：先从批量行情中取；批量失败则尝试腾讯历史接口的最新一条。"""
    code = clean_code(code)
    errors = []
    try:
        spot = load_spot()
        row = spot[spot["代码"].astype(str).str.zfill(6) == code]
        if not row.empty:
            return row.iloc[0].to_dict()
    except Exception as e:
        errors.append("批量行情: " + str(e)[:120])
    try:
        symbol = market_suffix(code).lower()
        df = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=(datetime.now()-timedelta(days=30)).strftime("%Y%m%d"), end_date=datetime.now().strftime("%Y%m%d"), adjust="")
        if df is not None and not df.empty:
            r = df.iloc[-1]
            return {"代码": code, "名称": code, "最新价": to_num(r.get("close")), "涨跌幅": np.nan, "市盈率-动态": np.nan, "市净率": np.nan}
    except Exception as e:
        errors.append("腾讯行情: " + str(e)[:120])
    raise RuntimeError("无法获取该股票的实时/最近收盘报价。请稍后重试。" + (" | ".join(errors) if errors else ""))

@st.cache_data(ttl=900, show_spinner=False)
def load_hist(code, days=760, adjust="qfq"):
    if ak is None:
        raise RuntimeError("AKShare 未成功安装。")
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    errors = []
    # 1) 东方财富：数据字段最完整
    try:
        df = ak.stock_zh_a_hist(symbol=clean_code(code), period="daily", start_date=start, end_date=end, adjust=adjust)
        if df is not None and not df.empty:
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            for c in ["开盘", "收盘", "最高", "最低", "成交量", "成交额", "涨跌幅", "换手率"]:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            return df.dropna(subset=["日期", "收盘"]).sort_values("日期").drop_duplicates("日期")
    except Exception as e:
        errors.append("东财历史: " + str(e)[:120])

    # 2) 腾讯：作为东财历史接口的备用源
    try:
        symbol = market_suffix(code).lower()
        df = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start, end_date=end, adjust=adjust)
        if df is not None and not df.empty:
            df = df.rename(columns={"date":"日期", "open":"开盘", "close":"收盘", "high":"最高", "low":"最低", "amount":"成交量"})
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            for c in ["开盘", "收盘", "最高", "最低", "成交量"]:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            if "成交额" not in df.columns:
                df["成交额"] = np.nan
            if "涨跌幅" not in df.columns:
                df["涨跌幅"] = df["收盘"].pct_change() * 100
            if "换手率" not in df.columns:
                df["换手率"] = np.nan
            return df.dropna(subset=["日期", "收盘"]).sort_values("日期").drop_duplicates("日期")
    except Exception as e:
        errors.append("腾讯历史: " + str(e)[:120])
    raise RuntimeError("历史行情暂时不可用，请稍后重试。" + (" | ".join(errors) if errors else ""))

@st.cache_data(ttl=3600, show_spinner=False)
def load_financial(code):
    if ak is None:
        return pd.DataFrame()
    errors = []
    for fn, kwargs in [
        (getattr(ak, "stock_financial_analysis_indicator_em", None), {"symbol": clean_code(code), "indicator": "按报告期"}),
        (getattr(ak, "stock_financial_analysis_indicator", None), {"symbol": market_suffix(code).lower()}),
    ]:
        if fn is None:
            continue
        try:
            df = fn(**kwargs)
            if df is not None and not df.empty:
                # 尽量统一报告期字段
                if "REPORT_DATE" not in df.columns:
                    for c in ["报告期", "日期", "REPORT_DATE"]:
                        if c in df.columns:
                            df["REPORT_DATE"] = pd.to_datetime(df[c], errors="coerce")
                            break
                if "REPORT_DATE" in df.columns:
                    df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"], errors="coerce")
                    return df.sort_values("REPORT_DATE").drop_duplicates("REPORT_DATE").reset_index(drop=True)
                return df
        except Exception as e:
            errors.append(type(e).__name__ + ": " + str(e)[:120])
    return pd.DataFrame()

def get_latest_financial(df):
    if df is None or df.empty:
        return {}
    row = df.dropna(subset=["REPORT_DATE"]).iloc[-1]
    return {k: row.get(k) for k in df.columns}

def search_stocks(query, spot):
    q = str(query).strip()
    if not q:
        return spot.head(20)
    mask = (
        spot["代码"].astype(str).str.contains(q, na=False)
        | spot["名称"].astype(str).str.contains(q, na=False, case=False)
    )
    cols = [c for c in ["代码", "名称", "最新价", "涨跌幅", "市盈率-动态", "市净率", "总市值"] if c in spot.columns]
    return spot.loc[mask, cols].head(30)

# -------------------- 量化评分 --------------------
def build_analysis(code, spot, hist, fin, quote=None):
    code = clean_code(code)
    if quote is not None:
        s = pd.Series(quote)
    elif spot is not None and not spot.empty and "代码" in spot.columns:
        spot_row = spot[spot["代码"].astype(str).str.zfill(6) == code]
        if spot_row.empty:
            raise RuntimeError("股票不在当前A股实时行情列表中，可能已停牌、退市或代码有误。")
        s = spot_row.iloc[0]
    else:
        s = pd.Series({"代码": code, "名称": code, "最新价": np.nan})
    latest_fin = get_latest_financial(fin)

    px = hist["收盘"].astype(float).dropna()
    last = float(px.iloc[-1])
    ret_60 = px.iloc[-1] / px.iloc[-61] - 1 if len(px) > 61 else np.nan
    ret_120 = px.iloc[-1] / px.iloc[-121] - 1 if len(px) > 121 else np.nan
    ret_250 = px.iloc[-1] / px.iloc[-251] - 1 if len(px) > 251 else np.nan
    vol120 = annualized_vol(px.pct_change().tail(120))
    ma20 = px.rolling(20).mean().iloc[-1]
    ma60 = px.rolling(60).mean().iloc[-1]
    ma120 = px.rolling(120).mean().iloc[-1]
    ma250 = px.rolling(250).mean().iloc[-1] if len(px) >= 250 else np.nan

    pe = to_num(s.get("市盈率-动态"))
    pb = to_num(s.get("市净率"))
    roe = to_num(latest_fin.get("ROEJQ"))
    revenue_yoy = to_num(latest_fin.get("TOTALOPERATEREVETZ"))
    profit_yoy = to_num(latest_fin.get("PARENTNETPROFITTZ"))
    debt = to_num(latest_fin.get("ZCFZL"))
    ocf_sales = to_num(latest_fin.get("JYXJLYYSR"))

    valuation_pe = score_lower_better(pe, 12, 40)
    valuation_pb = score_lower_better(pb, 1.2, 5)
    valuation = np.nanmean([valuation_pe, valuation_pb])

    quality_roe = score_higher_better(roe, 5, 20)
    quality_debt = score_lower_better(debt, 35, 75)
    quality_cash = score_higher_better(ocf_sales, 0, 20)
    quality = np.nanmean([quality_roe, quality_debt, quality_cash])

    growth_rev = score_higher_better(revenue_yoy, 0, 20)
    growth_profit = score_higher_better(profit_yoy, 0, 25)
    growth = np.nanmean([growth_rev, growth_profit])

    momentum_6m = score_higher_better(ret_120 * 100 if pd.notna(ret_120) else np.nan, -20, 30)
    momentum_12m = score_higher_better(ret_250 * 100 if pd.notna(ret_250) else np.nan, -30, 50)
    trend_bonus = 10 if (last > ma120 and ma60 > ma120) else 0
    momentum = min(100, np.nanmean([momentum_6m, momentum_12m]) + trend_bonus)

    lowvol = score_lower_better(vol120 * 100 if pd.notna(vol120) else np.nan, 18, 45)

    components = {
        "估值": valuation,
        "盈利质量": quality,
        "成长": growth,
        "动量": momentum,
        "低波": lowvol,
    }
    weights = {"估值": .25, "盈利质量": .25, "成长": .20, "动量": .20, "低波": .10}
    available = [(k, v, weights[k]) for k, v in components.items() if pd.notna(v)]
    if not available:
        total = np.nan
    else:
        total = sum(v*w for _, v, w in available) / sum(w for _, _, w in available)

    trend = "偏强" if last > ma120 and ma60 > ma120 else ("震荡" if last > ma120 else "偏弱")
    name = str(s.get("名称", ""))
    is_st = name.upper().startswith("ST") or "退" in name

    if is_st:
        action = "回避"
        reason = "风险警示/退市风险标记，量化模型不纳入买入信号。"
    elif pd.isna(total):
        action = "观望"
        reason = "关键数据不足，暂不生成强信号。"
    elif total >= 75 and trend == "偏强":
        action = "买入"
        reason = "综合评分较高且价格位于中期趋势线上方。"
    elif total < 55 or (trend == "偏弱" and total < 70):
        action = "回避"
        reason = "评分或趋势偏弱，风险收益比暂不理想。"
    else:
        action = "观望"
        reason = "基本面/估值与趋势信号未形成足够强的一致性。"

    return {
        "代码": code,
        "名称": name,
        "最新价": last,
        "涨跌幅": to_num(s.get("涨跌幅")),
        "PE(动态)": pe,
        "PB": pb,
        "ROE": roe,
        "营收同比": revenue_yoy,
        "净利润同比": profit_yoy,
        "资产负债率": debt,
        "经营现金流/营收": ocf_sales,
        "60日收益": ret_60,
        "120日收益": ret_120,
        "250日收益": ret_250,
        "120日年化波动": vol120,
        "MA20": ma20,
        "MA60": ma60,
        "MA120": ma120,
        "MA250": ma250,
        "趋势": trend,
        "综合评分": total,
        "分项": components,
        "建议": action,
        "建议说明": reason,
        "财报日期": latest_fin.get("REPORT_DATE"),
    }

def fmt_pct(x):
    return "—" if pd.isna(x) else f"{x:.2%}"

def fmt_num(x, suffix=""):
    return "—" if pd.isna(x) else f"{x:.2f}{suffix}"

# -------------------- 侧栏 --------------------
st.sidebar.header("⚙️ 参数")
initial_capital = st.sidebar.number_input("模拟本金", min_value=10000, max_value=10000000, value=100000, step=10000)
max_position = st.sidebar.slider("单股最大仓位", 5, 30, 15)
cash_buffer = st.sidebar.slider("最低现金比例", 0, 50, 10)
adjust = st.sidebar.selectbox("K线复权", ["qfq", "hfq", ""])
st.sidebar.caption("qfq=前复权，适合看盘；hfq=后复权，适合长期收益研究；空白=不复权。")

if "paper_cash" not in st.session_state:
    st.session_state.paper_cash = float(initial_capital)
if "paper_positions" not in st.session_state:
    st.session_state.paper_positions = {}

tabs = st.tabs(["🔎 股票搜索与量化评分", "📊 K线与技术指标", "💰 10万元模拟账户", "🏆 A股快速筛选", "ℹ️ 使用说明"])

# -------------------- Tab 1 --------------------
with tabs[0]:
    st.subheader("输入股票代码或名称")
    c1, c2 = st.columns([3, 1])
    with c1:
        query = st.text_input("股票代码/名称", value="600519", placeholder="例如：600519、贵州茅台")
    with c2:
        do_search = st.button("🔍 搜索", use_container_width=True)

    spot = pd.DataFrame()
    try:
        with st.spinner("正在获取股票列表…"):
            spot = load_spot()
        results = search_stocks(query, spot)
        if not results.empty:
            st.dataframe(results, use_container_width=True, hide_index=True)
        else:
            st.info("没有找到匹配股票，请检查代码或名称。")
    except Exception as e:
        # 批量列表失败不再阻断整个 App；代码直查仍可用
        st.warning("股票列表数据源暂时不可用。你仍可以直接输入 6 位股票代码进行分析。")
        results = pd.DataFrame()

    code = clean_code(query)
    if not code and not results.empty:
        code = str(results.iloc[0]["代码"]).zfill(6)
    elif results is not None and not results.empty and not (spot.empty) and not (spot["代码"].astype(str).str.zfill(6) == code).any():
        code = str(results.iloc[0]["代码"]).zfill(6)

    analyze = st.button("🚀 开始量化分析", type="primary", use_container_width=True)
    if analyze:
        if len(code) != 6:
            st.error("请输入 6 位 A 股股票代码，例如 600519。")
        else:
            try:
                with st.spinner("正在获取行情、历史数据和财务指标…"):
                    quote = load_quote(code)
                    hist = load_hist(code, adjust=adjust)
                    fin = load_financial(code)
                    a = build_analysis(code, spot, hist, fin, quote=quote)
                st.session_state["analysis"] = a
                st.session_state["hist"] = hist
                st.session_state["quote"] = quote
            except Exception as e:
                st.error(f"分析失败：{e}")
                st.info("如果数据源暂时限流，请等待 30～60 秒再试。V3.1 已加入备用历史行情源，但公开接口仍可能临时不可用。")

    if "analysis" in st.session_state:
        a = st.session_state["analysis"]
        st.divider()
        title_col, score_col, action_col = st.columns([2, 1, 1])
        title_col.markdown(f"## {a['名称']}  `{a['代码']}`")
        score_col.metric("综合评分", "—" if pd.isna(a["综合评分"]) else f"{a['综合评分']:.1f}/100")
        action_col.metric("模型信号", a["建议"])
        st.caption(a["建议说明"])
        m = st.columns(6)
        m[0].metric("最新价", fmt_num(a["最新价"]))
        m[1].metric("今日涨跌", fmt_pct(a["涨跌幅"] / 100 if pd.notna(a["涨跌幅"]) else np.nan))
        m[2].metric("PE", fmt_num(a["PE(动态)"]))
        m[3].metric("PB", fmt_num(a["PB"]))
        m[4].metric("ROE", fmt_num(a["ROE"], "%"))
        m[5].metric("趋势", a["趋势"])
        score_df = pd.DataFrame([{"因子": k, "分数": None if pd.isna(v) else round(v, 1)} for k, v in a["分项"].items()])
        st.dataframe(score_df, use_container_width=True, hide_index=True)
        missing = [k for k, v in a["分项"].items() if pd.isna(v)]
        if missing:
            st.warning("以下因子暂缺数据：" + "、".join(missing) + "。综合评分会按可用因子重新归一化，不会因为单一财务接口失败而直接报错。")
        pos = max_position / 100
        suggested_amount = min(initial_capital * pos, initial_capital * (1 - cash_buffer / 100))
        st.info(f"模拟参考仓位上限：{max_position}%；以 {initial_capital:,.0f} 元本金计算，单股最多约 ¥{suggested_amount:,.0f}。模型信号仅供研究与模拟。")

# -------------------- Tab 2 --------------------
with tabs[1]:
    st.subheader("K线与趋势")
    if "analysis" not in st.session_state or "hist" not in st.session_state:
        st.info("请先在「股票搜索与量化评分」中输入股票并点击「开始量化分析」。")
    else:
        a = st.session_state["analysis"]
        hist = st.session_state["hist"].copy()
        h = hist.tail(500).copy()
        for n in [20, 60, 120, 250]:
            h[f"MA{n}"] = h["收盘"].rolling(n).mean()
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=h["日期"], open=h["开盘"], high=h["最高"], low=h["最低"], close=h["收盘"], name="K线"
        ))
        for n in [20, 60, 120]:
            fig.add_trace(go.Scatter(x=h["日期"], y=h[f"MA{n}"], mode="lines", name=f"MA{n}"))
        fig.update_layout(
            height=560,
            xaxis_rangeslider_visible=False,
            margin=dict(l=10, r=10, t=30, b=10),
            legend=dict(orientation="h"),
        )
        st.plotly_chart(fig, use_container_width=True)

        h["累计收益"] = h["收盘"] / h["收盘"].iloc[0] - 1
        st.dataframe(h.tail(20), use_container_width=True, hide_index=True)

        r = h["收盘"].pct_change().dropna()
        c = st.columns(4)
        c[0].metric("60日收益", fmt_pct(a["60日收益"]))
        c[1].metric("120日收益", fmt_pct(a["120日收益"]))
        c[2].metric("250日收益", fmt_pct(a["250日收益"]))
        c[3].metric("120日年化波动", fmt_pct(a["120日年化波动"]))

# -------------------- Tab 3 --------------------
with tabs[2]:
    st.subheader("💰 10万元模拟账户")
    st.caption("这是浏览器会话内的纸上交易账户；刷新/重启应用后不保证保留，不能作为真实券商账户。")

    # allow reset
    if st.button("♻️ 重置模拟账户"):
        st.session_state.paper_cash = float(initial_capital)
        st.session_state.paper_positions = {}
        st.rerun()

    cash = float(st.session_state.paper_cash)
    positions = st.session_state.paper_positions
    rows = []
    total_market = 0.0

    if positions:
        for code, p in positions.items():
            try:
                q = load_quote(code)
                price = to_num(q.get("最新价"))
                name = str(q.get("名称", code))
            except Exception:
                price = p["cost_price"]
                name = code
            market_value = p["shares"] * price
            total_market += market_value
            pnl = market_value - p["cost"] - p["fees"]
            rows.append({
                "代码": code, "名称": name, "持股": p["shares"],
                "成本": p["cost_price"], "现价": price,
                "市值": market_value, "浮动盈亏": pnl,
                "收益率": pnl / p["cost"] if p["cost"] else np.nan
            })

    total_assets = cash + total_market
    c1, c2, c3 = st.columns(3)
    c1.metric("现金", f"¥{cash:,.0f}")
    c2.metric("持仓市值", f"¥{total_market:,.0f}")
    c3.metric("账户总资产", f"¥{total_assets:,.0f}", f"{total_assets/initial_capital-1:.2%}")

    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("当前没有持仓。")

    st.divider()
    st.markdown("### 纸上买入")
    if "analysis" in st.session_state:
        a = st.session_state["analysis"]
        selected_code = a["代码"]
        selected_name = a["名称"]
        selected_price = a["最新价"]
    else:
        selected_code = clean_code(query) if "query" in locals() else ""
        selected_name = selected_code
        selected_price = np.nan

    b1, b2, b3 = st.columns(3)
    with b1:
        buy_code = st.text_input("买入代码", value=selected_code, key="buy_code")
    with b2:
        buy_price = st.number_input("模拟成交价", min_value=0.01, value=float(selected_price) if pd.notna(selected_price) and selected_price > 0 else 10.0, step=0.01, key="buy_price")
    with b3:
        buy_amount = st.number_input("投入金额", min_value=1000.0, value=10000.0, step=1000.0, key="buy_amount")

    if st.button("🟢 执行模拟买入", use_container_width=True):
        code = clean_code(buy_code)
        lots = int(buy_amount // (buy_price * 100))
        shares = lots * 100
        actual = shares * buy_price
        fee_amt = actual * 0.0003
        if shares <= 0:
            st.error("金额不足以买入100股。")
        elif actual + fee_amt > st.session_state.paper_cash:
            st.error("现金不足。")
        else:
            old = positions.get(code)
            if old:
                total_cost = old["cost"] + actual
                total_shares = old["shares"] + shares
                old["cost_price"] = total_cost / total_shares
                old["shares"] = total_shares
                old["cost"] = total_cost
                old["fees"] += fee_amt
            else:
                positions[code] = {
                    "shares": shares, "cost_price": buy_price,
                    "cost": actual, "fees": fee_amt
                }
            st.session_state.paper_cash -= actual + fee_amt
            st.success(f"已模拟买入 {code} {shares}股，成交额约 ¥{actual:,.0f}。")
            st.rerun()

    st.markdown("### 纸上卖出")
    sell_code = st.text_input("卖出代码", value=(next(iter(positions)) if positions else ""), key="sell_code")
    if st.button("🔴 全部卖出", use_container_width=True):
        code = clean_code(sell_code)
        if code not in positions:
            st.error("该股票不在模拟持仓中。")
        else:
            p = positions[code]
            try:
                q = load_quote(code)
                price = to_num(q.get("最新价"))
            except Exception:
                price = p["cost_price"]
            amount = p["shares"] * price
            fee_amt = amount * 0.0003
            st.session_state.paper_cash += amount - fee_amt
            del positions[code]
            st.success(f"已模拟卖出 {code}，到账约 ¥{amount-fee_amt:,.0f}。")
            st.rerun()

# -------------------- Tab 4 --------------------
with tabs[3]:
    st.subheader("🏆 A股快速筛选")
    st.caption("这里使用当前实时行情字段做快速初筛，不等同于完整的财务多因子排名。")
    try:
        if ak is None:
            st.error("AKShare 未安装。")
        else:
            spot2 = load_spot().copy()
            for c in ["市盈率-动态", "市净率", "60日涨跌幅", "年初至今涨跌幅", "换手率"]:
                if c in spot2.columns:
                    spot2[c] = pd.to_numeric(spot2[c], errors="coerce")
            spot2 = spot2[~spot2["名称"].astype(str).str.upper().str.startswith("ST")]
            spot2 = spot2[(spot2["最新价"] > 0)]
            spot2["估值快评分"] = (
                spot2["市盈率-动态"].apply(lambda x: score_lower_better(x, 12, 40)).fillna(50) * .45
                + spot2["市净率"].apply(lambda x: score_lower_better(x, 1.2, 5)).fillna(50) * .25
                + spot2["60日涨跌幅"].apply(lambda x: score_higher_better(x, -20, 30)).fillna(50) * .30
            )
            view = spot2.sort_values("估值快评分", ascending=False).head(30)
            cols = ["代码", "名称", "最新价", "涨跌幅", "市盈率-动态", "市净率", "60日涨跌幅", "估值快评分"]
            st.dataframe(view[cols], use_container_width=True, hide_index=True)
            st.caption("快速筛选只用于缩小研究范围；正式决策前建议对候选股票逐只运行完整量化分析。")
    except Exception as e:
        st.error(f"快速筛选失败：{e}")

# -------------------- Tab 5 --------------------
with tabs[4]:
    st.subheader("使用说明")
    st.markdown("""
### V3.1 做了什么？
1. **自动获取A股行情**：不再需要手工上传CSV。
2. **股票代码/名称搜索**：例如输入 `600519` 或 `贵州茅台`。
3. **多因子量化评分**：
   - 估值 25%
   - 盈利质量 25%
   - 成长 20%
   - 动量 20%
   - 低波动 10%
4. **K线**：显示日K以及 MA20 / MA60 / MA120。
5. **模型信号**：根据评分、趋势和风险标记输出“买入 / 观望 / 回避”。
6. **10万元模拟账户**：支持按A股100股整数倍进行纸上买入和全部卖出。
7. **A股快速筛选**：用实时估值+动量先缩小研究范围。

### 数据与计算说明
- 日线行情使用 AKShare 的 A 股历史行情接口。
- 复权选项默认前复权；长期收益研究可切换后复权。
- 财务指标使用最新可取得的报告期数据。
- 模型是研究型规则，不代表未来收益，也没有考虑所有实盘约束。

### 部署
GitHub 仓库根目录至少放：
`app.py`
`requirements.txt`
`README.md`

Streamlit Community Cloud 会根据 `requirements.txt` 安装依赖，然后运行 `app.py`。

### 常见问题
**1. 行情获取失败**
可能是公开数据源限流、网络波动或接口变更。V3.1 会在批量行情失败时尝试备用源；若仍失败，稍后重试即可。

**2. 为什么财务数据不是今天的？**
财务指标来自上市公司定期报告，不会像股价一样实时变化。

**3. 为什么模拟账户刷新后可能变化/重置？**
当前版本把模拟账户保存在 Streamlit 会话状态中，没有接数据库。它是纸上交易演示，不是永久记账系统。
""")
