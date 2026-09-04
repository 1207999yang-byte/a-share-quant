# A股量化助手 V3.0

V3.0 是一个可部署到 Streamlit Community Cloud 的 A 股研究型量化工具。

## 功能
- 自动获取沪深京 A 股行情
- 股票代码/名称搜索
- 多因子量化评分：估值25%、盈利质量25%、成长20%、动量20%、低波10%
- 日K线 + MA20/MA60/MA120
- 模型信号：买入 / 观望 / 回避
- 10万元纸上模拟账户（100股整数倍买卖）
- A股快速筛选
- 前复权 / 后复权 / 不复权切换

## 文件
- `app.py`：主程序
- `requirements.txt`：依赖
- `README.md`：说明

## Streamlit Cloud 部署
1. 在 GitHub 仓库中替换旧版 `app.py`。
2. 同时替换 `requirements.txt` 和 `README.md`。
3. Streamlit Community Cloud 的入口文件选择 `app.py`。
4. Branch 选择 `main`。
5. 等待依赖安装完成后打开 App。

## 数据说明
V3 使用 AKShare 获取 A 股实时行情、历史日线和财务分析指标。接口属于公开数据接口，可能因网络、限流、数据源调整等原因暂时不可用。

## 风险提示
本工具只用于研究、学习和模拟，不构成任何投资、买卖或收益承诺。量化信号不是未来收益的保证。
