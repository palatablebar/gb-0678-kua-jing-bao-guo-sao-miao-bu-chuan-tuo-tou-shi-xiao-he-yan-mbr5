# 扫描时效定义

scan_id是扫描事件唯一键，parcel是包裹号。节点按收件、起运、清关、妥投顺序推进；可缺中间节点，但必须有收件。按event_time排序，received_time仅用于上传延迟。倒退扫描隔离，不改写先前妥投事件。妥投使用最早有效发生时刻。

目的地JP/US/DE对应东京/纽约/柏林。时效以目的地工作日小时计算，周末与节假日停表。JP含2026-11-03与11-23，US含11-26，DE含10-03；支持按参数覆盖假日。未妥投计算至as_of，默认2026-11-18T00:00Z。每超时小时赔付1元，上限保价10%，reported为原申报赔付。

## 外币申报与汇率口径

基础表头为十列；另接受扩展十二列，在reported与note之间插入reported_currency、settlement_date。缺扩展列时按CNY、结算日回退妥投日处理，旧样例无需改造。reported_currency仅登记CNY/JPY/USD，哨兵或空值不按零折算。

合同预期赔付以人民币计赔，合同基准日默认取妥投日（未妥投取as_of日期），可用contract_rate_date或参数fx_rates覆盖。承运商按结算日汇率以外币申报，M6同时给出结算口径人民币reported_cny与合同基准口径reported_at_contract，汇差fx_gap=申报原币额×(结算日汇率−基准日汇率)，只用于解释差异；对账差额以合同基准口径为准，汇差不重复计入。

参数fx_rates为{币种:{汇率日:汇率}}。不传时用内置演示汇率表；一旦传入（含空对象）即为权威表，不再回退内置值，缺日期抛RATE_NOT_FOUND、币种未登记抛UNKNOWN_CURRENCY、同币同日冲突抛RATE_CONFLICT，整批失败并由服务层恢复上一可用结论，失败切换不写结论台账。

补传调整经POST /api/runs/<id>/adjustments登记，字段parcel/scan_id/batch_date/amount/currency，按新批次汇率日batch_date取汇率，补传差额=(新金额−旧金额)×批次汇率，逐笔追加到adjustments。旧金额、原始申报额与原始扫描只保留不改写；同run内(parcel,scan_id,batch_date,amount)唯一，重试返回deduplicated=true且不第二次冲销。

业务ID和超大整数按文本保形；Decimal参与数量金额计算，对外序列化为字符串。哨兵集合定义在app/values.py，缺失值不能当零。所有时间带UTC偏移，先转UTC比较，业务日按指定地区划分。区间统一[start,end)。重复业务键保留第一条、完全相同行计重复；值冲突行隔离且保留原值。空文件、全空白、仅表头分开拒绝。

运行清单包括输入sha256、规则版本、参数、生成时刻与耗时。源行编号以CSV解析器的物理结束行号表示，引号内换行不增加记录数。输入超过4MB返回413，非法表头和类型返回422，失效版本复核返回409。汇率切换失败返回409并回滚上一可用结论，原始输入字节保存在raw_inputs表供同输入重算。