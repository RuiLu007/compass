/** Editable System One examples shared by JEV and Alibaba decision providers. */
export type DecisionQuestionDraft = {
  id: string;
  type: "choice" | "noul" | "score";
  instructions: string;
  criteria: string;
};
export type DecisionExample = {
  id: string;
  title: string;
  description: string;
  stateFormat: "text" | "json";
  state: string;
  questions: DecisionQuestionDraft[];
};
export const decisionExamples: DecisionExample[] = [
  {
    id: "intent-routing",
    title: "意图分流 · Choice",
    description: "将经营问题分配到销售、库存或售后任务，查看选项及完整概率分布。",
    stateFormat: "text",
    state: "请检查上海仓 SKU-A 的可售库存，结合最近 7 天销量，判断还够卖几天、是否需要补货。",
    questions: [
      {
        id: "intent",
        type: "choice",
        instructions: "根据用户请求，选择最适合处理该问题的任务。",
        criteria: JSON.stringify(
          {
            sales: "销售额、销量、渠道或商品销售表现分析",
            inventory: "库存、缺货风险、周转和补货分析",
            after_sales: "退款、投诉和售后问题",
            other: "以上任务均不适用",
          },
          null,
          2,
        ),
      },
    ],
  },
  {
    id: "escalation",
    title: "是否升级处理 · Noul",
    description: "判断投诉是否需要人工升级，观察 Noul 返回的成立概率。",
    stateFormat: "text",
    state:
      "客户反馈同一订单被扣款两次，已连续三天联系客服但没有解决，今天要求主管立即处理。支付系统记录显示有两笔成功扣款。",
    questions: [
      {
        id: "escalate",
        type: "noul",
        instructions:
          "是否需要升级给人工主管处理？已确认重复扣款、重复投诉未解决或明确要求主管介入时，应升级处理。",
        criteria: "",
      },
    ],
  },
  {
    id: "severity",
    title: "问题严重度 · Score",
    description: "按从低到高的等级评分，查看加权分数、各等级概率和置信度。",
    stateFormat: "text",
    state:
      "促销期间，华东区域所有门店的支付接口持续失败 20 分钟，用户无法完成订单。库存查询和商品浏览仍可用，目前没有可用的备用支付通道。",
    questions: [
      {
        id: "severity",
        type: "score",
        instructions: "按业务影响评估故障严重程度。",
        criteria:
          "轻微：体验问题，业务正常完成\n一般：部分功能受影响，有可行替代方式\n严重：核心交易受阻，缺少替代方式\n紧急：大范围核心业务中断，且存在资金或安全损失",
      },
    ],
  },
  {
    id: "multi-question",
    title: "工单综合决策 · 多问题",
    description: "使用 JSON 场景，在一次调用中完成团队分流、升级判断和严重度评分。",
    stateFormat: "json",
    state: JSON.stringify(
      {
        ticket_id: "DEMO-1001",
        customer_message: "订单被扣款两次，联系两天仍未退款，希望主管介入。",
        payment: { successful_charges: 2, order_count: 1 },
        contacts: 3,
        elapsed_hours: 48,
      },
      null,
      2,
    ),
    questions: [
      {
        id: "department",
        type: "choice",
        instructions: "应该由哪个团队负责解决此工单？",
        criteria: JSON.stringify(
          {
            billing: "支付、扣款、退款和账单问题",
            technical: "产品故障和接口集成问题",
            sales: "报价、购买咨询和套餐升级",
          },
          null,
          2,
        ),
      },
      {
        id: "escalate",
        type: "noul",
        instructions: "是否需要人工升级？涉及已确认的资金异常或多次联系仍未解决时，应升级。",
        criteria: "",
      },
      {
        id: "severity",
        type: "score",
        instructions: "根据业务和资金影响评估工单严重度。",
        criteria:
          "低：普通咨询，无业务影响\n中：使用受影响，有替代办法\n高：核心业务受阻或资金异常\n紧急：大范围故障或严重安全风险",
      },
    ],
  },
];
