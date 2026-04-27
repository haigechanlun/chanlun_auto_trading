# 📈 Chanlun Quant Trading System

<p align="center">
  <a href="#readme-zh">简体中文</a>
  &nbsp;·&nbsp;
  <a href="#readme-en">English</a>
</p>

---

<h2 id="readme-zh">简体中文</h2>

一个基于缠论（Chanlun）理论构建的自动化量化交易系统，结合历史数据训练、严格风控与实盘执行，致力于实现稳定且可持续的收益表现。

回测收益年化 80%+。欢迎订阅信号和实盘测试。

### 联系海哥（订阅信号 & 实盘对接测试）

- Telegram: [http://t.me/haigechanlun](http://t.me/haigechanlun)
- 公众号：海哥缠论
- 推特：[https://x.com/haigechanlun666](https://x.com/haigechanlun666)
- 微信：roganwu32

![ETH 一年回测](./images/haige.png)

### 实盘对接流程

- **注册新账号**
  - Gate： [https://www.gatenode.xyz/share/VVNAULXZAG](https://www.gatenode.xyz/share/VVNAULXZAG)（邀请码：`VVNAULXZAG`）
  - OKX： [https://www.vmutkhamuut.com/join/5032833](https://www.vmutkhamuut.com/join/5032833)（邀请码：`5032833`）
- 开通合约 API 读取与交易权限，并将 Key 提供给我们
- 将资金划转至合约账户

---

## 🚀 核心特性

### 1️⃣ 缠论买卖点识别（AI + 规则引擎）

- 基于 **近 2 年 K 线数据** 进行结构化训练与验证
- 自动识别：中枢结构、笔 / 线段演化、一买 / 二买 / 三买与一卖 / 二卖 / 三卖
- 支持多周期联动（1H / 4H / 日线）

### 2️⃣ 全自动化交易执行

- 信号生成 → 下单执行 → 持仓管理全流程自动化
- 支持主流交易所 API 对接
- 可扩展策略模块（多策略并行）

### 3️⃣ 风控管理体系（Risk Control Engine）

- 仓位动态控制（Position Sizing）
- 单笔风险限制
- 最大回撤控制（Max Drawdown Protection）
- 高频异常波动过滤

### 4️⃣ 移动止盈止损（Trailing System）

- 动态止盈（趋势跟随）
- 自适应止损（基于结构 / 波动率）
- 支持固定比例、ATR 波动模型、缠论结构止损

### 5️⃣ 回测表现（Backtesting）

- 回测周期：近 1 年历史数据
- 年化收益率：**> 85%**
- 夏普率（Sharpe）表现突出
- 支持多市场验证（加密货币 / 外汇）

> ⚠️ 历史回测不代表未来收益，市场具有不确定性。

![ETH 一年回测](./backtesting/eth.png)
![ETH 夏普率](./backtesting/report.png)

---

### 常用技术分析信号监控

- [TD 迪马克序列、神奇九转](./monitor/td.py)
- TD 实战教程：[微信文章](https://mp.weixin.qq.com/s/5A8oKSIA0tQN8OAsDdtKiQ)

### K 线行情

- 采用 Binance 数据源：[代码实现](./data/binance_api.py)

### 实盘交易

- 支持 Binance、OKX、Gate、WEEX
- Gate：[代码实现](./trade/gate/trade.py)
- OKX：[代码实现](./trade/okx/trade.py)

### 核心策略

- 基于近 2 年 K 线训练缠论买卖点，得到实时决策模型（不开源；需要跑实盘可联系）

### 开源策略（仅供参考，实盘有风险）

- **MACD 背驰 + TD 信号** 动态加减仓
  - [实盘代码](./strategy/live_trading_macd_td.py)
  - [回测代码](./strategy/backtest_macd_td.py)
  - 最近 10 天回测：

![最近 10 天回测数据](./backtesting/macd_td.png)
![最近 10 天回测统计](./backtesting/macd_td_report.png)

---

<p align="center"><a href="#readme-zh">↑ 回到简体中文</a> · <a href="#readme-en">English ↓</a></p>

---

<h2 id="readme-en">English</h2>

An automated quantitative trading system built on **Chanlun** (缠论) theory, combining historical data training, strict risk control, and live execution for stable, sustainable performance.

Backtests show **80%+ annualized** returns. Signal subscription and live testing are welcome.

### Contact (signals & live onboarding)

- Telegram: [http://t.me/haigechanlun](http://t.me/haigechanlun)
- WeChat official account: 海哥缠论
- X (Twitter): [https://x.com/haigechanlun666](https://x.com/haigechanlun666)
- WeChat: roganwu32

![ETH 1-year backtest](./images/haige.png)

### Live trading onboarding

- **Create exchange accounts**
  - Gate: [https://www.gatenode.xyz/share/VVNAULXZAG](https://www.gatenode.xyz/share/VVNAULXZAG) (invite: `VVNAULXZAG`)
  - OKX: [https://www.vmutkhamuut.com/join/5032833](https://www.vmutkhamuut.com/join/5032833) (invite: `5032833`)
- Enable futures API **read + trade** permissions and provide the keys to us
- Transfer funds to your futures account

---

## 🚀 Highlights

### 1️⃣ Chanlun entry/exit signals (AI + rules)

- Structured training and validation on **~2 years** of OHLCV data
- Detects: pivots (中枢), strokes/segments (笔/线段), first/second/third buy & sell points
- Multi-timeframe alignment (1H / 4H / daily)

### 2️⃣ Fully automated execution

- End-to-end: signal → orders → position management
- Major exchange APIs supported
- Extensible for multiple strategies in parallel

### 3️⃣ Risk control engine

- Dynamic position sizing
- Per-trade risk limits
- Max drawdown protection
- Filters for abnormal volatility

### 4️⃣ Trailing profit & stop

- Trend-following take-profit
- Structure/volatility-aware stops
- Fixed ratio, ATR-based, and Chanlun-structure stops

### 5️⃣ Backtesting

- ~1 year of historical data
- Annualized return: **> 85%**
- Strong Sharpe characteristics
- Crypto / FX validation

> ⚠️ Past backtests are not a guarantee of future results.

![ETH 1-year backtest](./backtesting/eth.png)
![ETH Sharpe report](./backtesting/report.png)

---

### Technical signal monitoring

- [Tom DeMark (TD) / “magic nine” sequences](./monitor/td.py)
- TD tutorial (WeChat article): [link](https://mp.weixin.qq.com/s/5A8oKSIA0tQN8OAsDdtKiQ)

### Market data

- Binance data source: [implementation](./data/binance_api.py)

### Live trading

- Binance, OKX, Gate, WEEX
- Gate: [code](./trade/gate/trade.py)
- OKX: [code](./trade/okx/trade.py)

### Core strategy (proprietary)

- Real-time model trained on ~2 years of Chanlun buy/sell points (**not open-sourced**; contact us for live deployment)

### Open-source strategy (for reference only; live trading involves risk)

- **MACD divergence + TD** dynamic scaling in/out
  - [Live](./strategy/live_trading_macd_td.py)
  - [Backtest](./strategy/backtest_macd_td.py)
  - Last 10 days backtest:

![MACD+TD backtest](./backtesting/macd_td.png)
![MACD+TD stats](./backtesting/macd_td_report.png)

---

<p align="center"><a href="#readme-en">↑ Top of English</a> · <a href="#readme-zh">简体中文</a></p>
