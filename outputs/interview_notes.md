# Cushing 库存与 WTI 月差：理解与面试讲述

**状态：公开研究已完成；预测与交易测试尚未完成**  
**数据截止：14 Sep 2026**  
**结果版本：902e770aeb2ecdab**

## 60 秒英文介绍

I studied how Cushing inventory relative to its seasonal history relates to the WTI curve, and where that relationship breaks down. I reconstructed publication-dated stock records and matched 481 releases to the preceding public curve quote. The pooled correlation was -0.729, and I checked how that relationship varied across years. The exploratory joint shape test, after year and seasonal controls, has a p-value of 0.7753. In April 2020, stocks still received a Normal seasonal label after rising 16.52 million barrels in four weeks. That is why I would check lease commitments, injection capacity and expected flows alongside the stock level. I also implemented and tested the fixed-contract forecasting and daily accounting workflow. Those predictive and trading tests await verified month-contract data. The useful result is a better physical-market question, with a clear distinction between explaining a curve and forecasting its next move.

## 三分钟讲述顺序

1. **0:00–0:25：为什么交易台关心。** 同样一桶原油，不同交付时间有不同价值。库欣库存与收货、储存、运输约束可能改变这个相对价值。问题是：库存是否增加了现有曲线以外的信息？
2. **0:25–1:00：先解释数据究竟说明什么。** 展示“观察周—实际公布日—报价日”。本次公开图把新公布库存与之前已经存在的 F2-F3 报价配对；不能称为公布后的交易效果。
3. **1:00–1:40：说真实结果与反例。** 481次匹配发布，整体相关 -0.729；2019年只有 0.079，接近没有关联。控制年份与季节后，形状检验 p=0.7753。非显著不等于已证明关系是线性的；删年份与去除年份控制会改变结果。
4. **1:40–2:20：用一个运营例子解释模型设计。** 2020年4月15日报告的季节状态仍是 Normal，z=0.39，但四周库存已经增加 16.520 百万桶。 这说明为什么 B2 不只使用库存水平，还保留一周和四周变化。它是研究设计动机，尚不是 B2 有预测优势的证据。
5. **2:20–3:00：商业判断与下一步。** 先查可使用的仓储、注入能力和进出流量，区分持续短缺与预计补库。最后明确真实合约数据到位后才检验预测与账本。

## 中文机制解释

**先从两张交货单想起。** 一张是较早交货，另一张是较晚交货。月差等于“较早价格减较晚价格”。正月差说明市场给较早交付更高价格；负月差说明较晚交付更贵。公开图里的 F2/F3 是顺位标签，真正持有一笔价差时必须记住两张具体合约，直到退出都按原合约结算。

**库存水平不是全部。** 库存低可能反映当期可交付原油紧张，但若市场已经预计管道来油恢复，月差也可能先回落。库存还不算特别高，却快速增加，也可能让收货安排先变得紧张。因此需要同时区分：有多少桶、变化多快、哪些桶或空间能用、未来流量会怎样。

**z-score 是历史比较尺。** 用当期库存减去前三年同季节均值，再除以同季节标准差。参考区间为年内位置前后28天，至少20个当时已知观察；z低于−1为Low、高于+1为High。它既不是“储罐剩余空间”，也不是安全运营底线。历史库存制度变化、参考样本波动小，会使绝对z很大。

## 八个核心追问

### 1. 为什么选 Cushing，而不是全国库存？

WTI的交割地与库欣相连，地理位置和收发能力让当地库存具有商业意义。全国库存变化可能反映共同供需因素，却不能说明库欣能收到或交出多少油。项目原计划把“全国商业库存减库欣”作为辅助控制；全国历史lease-stock口径在2016年变化，因此此控制暂不作为已经通过验收的结论。

### 2. 为什么主研究选择 M2–M3？

它靠近原油实物时间价值，同时减少最临近交割月的特殊挤压对常规检验的影响。代价是可能错过最强的M1交割信号，也不能完全消除流动性或交割风险。S=F(M2)−F(M3)：预期S上升，才对应买近卖远。2020年的M1负价事件不能直接充当M2–M3策略盈利证据。

### 3. 库存和月差相关，为什么还要市场信息基准？

市场在报告公布前可能已经通过流量、装运和其他信息形成预期。相关性可能说明曲线已经反映库存状态，而不是库存报告还剩下可交易信息。B1使用当前月差、近期同合约变化、波动、到期距离和季节项；B3只是在这个基准上增加库存信息。只有相同测试样本上的误差比较才回答“有没有额外信息”。

### 4. 如何证明没用到未来？

决策截止是EIA实际发布日期纽约时间23:59:59，不能把观察周末当公布日。结算可用性按固定规则处理；无法确认当天可用则再滞后。下一有效结算才模拟执行，未来目标始终跟踪原合约。标准化只拟合训练集，训练只使用已完成标签，拟合前保留五个交易日间隔。系统还应通过改变未来数据不改变过去特征的反例测试，而不是只口头保证。

### 5. 预测改善为什么仍可能亏损？

MAE变小可能只改善小幅变化，或方向优势太小而不够覆盖双腿成本。基准每腿每方向1tick滑点加$2.50费用，完整两腿往返共$50，即$0.05/桶；开仓门槛固定为$0.075/桶。真实净成交量决定收费：续持不重复收费，反向每腿两手，换月共同腿也可能变动两手。每日结算现金流、回撤和最终平仓都要记清楚。目前真实预测与经济价值都未验收，缺数据不是无效结果。

### 6. 哪项结果最反驳最初直觉？

不是把“低库存、月差高”画出来就证明了非线性。控制年份和季节后联合检验 p=0.7753，当前样本不足以充分确认额外弯折。Without year intercepts, p = 2.901e-07; excluding 2018, p = 1.326e-06; excluding 2020, p = 0.6882. These diagnostics expose dependence on controls and sample composition. Non-significance does not prove a linear mechanism. All year deletions and block sensitivities are retained. 2019年相关只有 0.079，属于弱关系，不足以称为可靠反转。新增分析是在看过汇总后提出的探索，不能包装成事前登记或未见样本发现。

### 7. 哪个实物风险是公开库存看不见的？

2020年4月15日报告的季节状态仍是 Normal，z=0.39，但四周库存已经增加 16.520 百万桶。 应追问空位是否已签租约、注入流速是否够、来油是否已排期、能否满足交割责任。统计标签不能回答这些问题。2023案例则提醒：库存低点和月差高点不必同日；预期补库可能先影响曲线，但当前公开数据不能确认究竟是哪项流量因素主导。

### 8. 下一份数据为什么值得拿？

先拿实际月份合约的结算价、最后交易日、有效结算日与行情可用规则：它们解除的是“不能构造可信未来目标和持仓收益”的限制。再拿带日期的管道流量、检修、仓储承诺和现货升贴水：它们帮助区别持续可交付短缺与暂时低库存，以及解释模型失败。两类数据解决不同问题，不能用更多库存指标替代缺失的合约身份。

## 两条英文简历草稿

- Reconstructed publication-dated Cushing inventories and analyzed 481 WTI rank-spread observations; tested controlled nonlinear relationships with HAC inference, paired block resampling and year-deletion diagnostics, retaining unstable and adverse findings.
- Implemented and unit-tested a fixed-contract WTI forecasting and accounting workflow with publication cutoffs, market-only benchmarks, net contract turnover and reconciled daily leg attribution; real-contract forecast and trading validation remain pending.

## 面试前自测

- 用自己的话解释：为什么 Normal 的库存状态仍可能让交易台担心？
- 指出某次报告的观察周、公布日、匹配报价日；说明哪项当时还不知道。
- 手算：近腿涨$0.10、远腿涨$0.04，一手买近卖远毛盈亏是+$100−$40=+$60；再说明两腿成本如何扣除。
- 解释 p=0.7753 的正确含义：当前样本不足以充分确认额外弯折；它不能单独证明经济机制，也不能证明线性一定正确。

所有数字来自当前结果对象。可以说“我实现并测试了预测框架”；不可说“我验证了真实预测优势”或“策略赚了钱”。
