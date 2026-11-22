# 华东经营周报数据字典

本账本是第 23 课华东经营诊断的独立合成快照。金额单位为人民币分，数量以件、双、单或次计，各表如下。时间按北京时间解释。课堂观察时点为 2025-11-04 12:00，两个完整经营周为 10 月 13–19 日和 20–26 日。样本在这十四天内完整覆盖华东六个经营单元，另留一组 SOUTH 销售记录供检查区域筛选。账本没有顾客标识。

## 打开账本

在 Compass 仓库根执行下列命令。需要系统自带或自行安装的 SQLite 命令行。临时目录中的数据库可在本次备课期间复用。

```sh
case_dir=examples/showcases/lesson-23-east-gmv-rag
demo_dir="$(mktemp -d)"
demo_db="$demo_dir/east-gmv.sqlite"
sqlite3 -bail "$demo_db" < "$case_dir/data/snapshot.sql"
sqlite3 -readonly -header -column "$demo_db" 'SELECT * FROM cell_dim;'
```

数据库工作台可直接打开上面生成的数据库文件。课堂发布器还会在 PostgreSQL 中装载[表及字段释义](postgres-comments.sql)，Agent 用数据库工具查看元数据时可以直接读到金额单位、单据含义和业务粒度。若在 Compass 中演示，将同一份 [snapshot.sql](snapshot.sql) 导入专用 PostgreSQL 空数据库或空 schema，并通过教学工作区已有的授权只读连接访问这 9 张表。使用已授权的来源发布 RAG 文档，经营分析员只需查询权限。这里使用独立教学表，已有的大零售库和跑鞋复议案例可继续按各自的经营日历使用。

## 字段与粒度

| 表          | 每行代表                              | 字段                                                                                                                       |
| ----------- | ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| cell_dim    | 一个经营单元，以 cell 为键            | channel 销售渠道，entry 入口，family 商品族，site 收银地点或线上主体                                                       |
| sales_day   | day、region、cell                     | visits 有效访次（门店为校正进店人次），paid_orders 成功支付单，paid_fen 实付商品金额，units 销售件数                       |
| ad_slot     | day、region、plan、slot               | PV27 为直营跑鞋媒体计划。DAY 与 EVE 是两个站方交付时段。spend_fen 实耗，impressions 曝光，clicks 点击，landings 有效抵达   |
| entry_audit | day、region、cell，覆盖三个小程序单元 | raw_open_events 原始打开，retry_events 重试，unlinked_opens 未归属打开，visits 最终有效会话。与营业账同日同单元可对齐      |
| stock_day   | day、region、pool、sku_size           | EAST-FLAG 为旗舰店承诺池。attempts 锁货尝试，short_rejects 缺货拒绝，other_rejects 其他拒绝，sellable_close 关账时可售双数 |
| stock_event | 单据、状态、经营区、货池、尺码        | ts 业务时间，doc_no 单号，event 业务状态，qty 该环节处理的双数。这里只摘录四张相关单据，各有两个尺码行                     |
| coupon_day  | day、region、site、offer              | site 存门店经营码。checks 资格检查，eligible 达标，issued 领券，redeemed 核销，discount_fen 已核销让利                     |
| refund_day  | day、region，两条序列并列             | cash_refund_fen 随退款成功出账日归属，cohort_d7_fen 随原支付日归属并统计至 D+7 当天结束                                    |
| source_day  | day、region、asset                    | expected_parts 应到批数，first_parts 首到批数，final_parts 终版批数，state 终版状态，final_at 终版可见时间                 |

可以先执行这条不含答案的查询，检查经营单元的覆盖天数。营业额与退款、广告时段和库存尺码应分别聚合后再对比。

```sql
SELECT s.cell, c.channel, c.entry, c.family, COUNT(*) AS days_covered
FROM sales_day AS s JOIN cell_dim AS c ON c.cell = s.cell
WHERE s.region = 'EAST'
  AND s.day >= '2025-10-13' AND s.day < '2025-10-27'
GROUP BY s.cell, c.channel, c.entry, c.family
ORDER BY s.cell;
```

业务资料中的小票演算和培训购物篮均为说明规则的例子。经营汇总使用本快照，周报数值以实际查询为准。库内未提供 SKU 支付明细、跨端顾客身份或未曝光的随机对照组。
